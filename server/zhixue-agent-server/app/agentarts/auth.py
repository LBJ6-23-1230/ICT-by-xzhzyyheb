# -*- coding: utf-8 -*-
"""IAM Token 提供者：24 小时有效、服务端缓存、并发只刷一次。

为什么必须缓存
--------------
官方文档明确 Token 有效期 24 小时、并建议缓存复用。如果每次请求都去 IAM 换 Token，
会出现两个问题：一是把 IAM 的调用配额和延迟都压到用户请求路径上；二是并发时多个
请求同时刷新，任何一个先过期都会互相踩踏。

这里用"过期前 10 分钟主动刷新 + 锁内单飞"解决：锁外读缓存，锁内二次检查，
避免惊群（thundering herd）。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

#: 提前刷新窗口：Token 剩余不足 10 分钟就换新的，避免"调用途中恰好过期"。
REFRESH_MARGIN_SECONDS = 600
#: 解析不出 expires_at 时的保守兜底（官方是 24h，这里只敢用一半）。
FALLBACK_TTL_SECONDS = 12 * 3600


class AgentArtsAuthError(RuntimeError):
    """取 Token 失败。`status` 为 0 表示网络层失败。"""

    def __init__(self, message: str, *, status: int = 0, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(message)


def _parse_expires_at(raw: str) -> float | None:
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    # 华为云返回微秒 6 位，fromisoformat 在某些 Python 版本只接受 3 位。
    for candidate in (text, text.replace("+00:00", "")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


class IamTokenProvider:
    """线程安全的 Token 缓存。`http_post` 可注入，便于离线测试。"""

    def __init__(self, config, *, http_post=None, clock=None) -> None:
        self._config = config
        self._post = http_post or self._default_post
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._token = ""
        self._expires_at = 0.0
        self._refreshed_at = 0.0

    # ------------------------------------------------------------ 对外
    def get(self, *, force: bool = False) -> str:
        token = self._token
        if not force and token and self._expires_at - self._clock() > REFRESH_MARGIN_SECONDS:
            return token
        with self._lock:
            # 双重检查：等锁期间可能已被别的线程刷新过。
            if not force and self._token and self._expires_at - self._clock() > REFRESH_MARGIN_SECONDS:
                return self._token
            self._token, self._expires_at = self._fetch()
            self._refreshed_at = self._clock()
            return self._token

    def invalidate(self) -> None:
        """遇到 401 时调用：丢掉缓存，下次强制重取。"""
        with self._lock:
            self._token = ""
            self._expires_at = 0.0

    def snapshot(self) -> dict:
        """给 `/api/v2/agent/runtime` 用：只报状态，不报 Token。"""
        remaining = int(self._expires_at - self._clock()) if self._expires_at else 0
        return {
            "cached": bool(self._token),
            "expiresInSeconds": max(0, remaining),
            "refreshedAt": (
                datetime.fromtimestamp(self._refreshed_at, tz=timezone.utc).isoformat()
                if self._refreshed_at else ""
            ),
        }

    # ------------------------------------------------------------ 内部
    def _fetch(self) -> tuple[str, float]:
        config = self._config
        payload = {
            "auth": {
                "identity": {
                    "methods": ["password"],
                    "password": {
                        "user": {
                            "name": config.iam_user,
                            "password": config.iam_password,
                            "domain": {"name": config.iam_domain},
                        }
                    },
                },
                # AgentArts 需要 **project 级** Token；scope 必须是 project。
                "scope": {"project": {"name": config.region}},
            }
        }
        url = "https://iam.%s.myhuaweicloud.com/v3/auth/tokens" % config.region
        status, headers, body = self._post(url, payload, timeout=20)

        if status not in (200, 201):
            raise AgentArtsAuthError(
                "IAM 取 Token 失败（HTTP %s）" % (status or "网络"), status=status, body=body[:500])
        token = headers.get("x-subject-token", "")
        if not token:
            raise AgentArtsAuthError("IAM 响应缺少 X-Subject-Token", status=status, body=body[:500])

        expires_at = None
        try:
            expires_at = _parse_expires_at((json.loads(body).get("token") or {}).get("expires_at", ""))
        except ValueError:
            expires_at = None
        if not expires_at:
            expires_at = self._clock() + FALLBACK_TTL_SECONDS
        return token, expires_at

    @staticmethod
    def _default_post(url: str, payload: dict, *, timeout: int):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
                return response.status, {k.lower(): v for k, v in response.headers.items()}, text
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", "replace")
            return error.code, {k.lower(): v for k, v in error.headers.items()}, text
        except urllib.error.URLError as error:
            raise AgentArtsAuthError("无法连接 IAM：%s" % (error.reason,), status=0) from error
