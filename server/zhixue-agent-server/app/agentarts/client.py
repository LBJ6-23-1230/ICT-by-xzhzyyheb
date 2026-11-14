# -*- coding: utf-8 -*-
"""AgentArts 运行时客户端：只做一件事——把 SSE 流变成一个个平台事件字典。

为什么用标准库而不是 requests/httpx
------------------------------------
后端当前的依赖里没有 HTTP 客户端库（只有 openai SDK 自带的那套）。为了一个流式接口
引入新依赖，意味着部署镜像、离线环境、评审机器都要多装一份东西。`urllib` 足够读 SSE：
`urlopen` 返回的对象本身就是按行可迭代的。

错误分层（很重要）
------------------
* `AgentArtsUnavailable` —— 网络层失败（DNS、超时、连接重置）。网关要回 502，并**明确
  说明是云端不可达**，不能伪装成本地结果。
* `AgentArtsError` —— 平台回了 4xx/5xx。带 `status` / `code` / `body`，网关按状态码给出
  可执行的排查结论（404 多半是 ID 错，401 多半是 Token 或权限）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

#: 平台错误码 → 给开发者的一句话结论。错误码取自平台错误响应体。
#: 注意运行时形态与低代码 runAgent 的 404 含义不同，提示要按实际调用方式给。
HINTS = {
    400: "入参不匹配：核对智能体「开始节点」是否声明了对应变量（query / inputs）",
    401: "凭据无效：运行时形态核对 API Key；runAgent 形态核对 IAM Token 与用户权限",
    403: "权限不足：确认已给用户组授权 AgentArts，且授权范围含所在区域（如 cn-southwest-2）",
    404: "运行时不存在：核对 AGENTARTS_RUNTIME_NAME 与 AGENTARTS_ENDPOINT 是否与运行时详情页一致（实例被删或重建后名称会变）",
    429: "触发限流：基础版为 500 次/天，等配额恢复或降低重试频率",
    500: "平台内部错误：先在控制台「试运行」复现，确认是平台侧还是入参问题",
}


class AgentArtsError(RuntimeError):
    """平台返回了非 2xx。"""

    def __init__(self, message: str, *, status: int = 0, code: str = "", body: str = "") -> None:
        self.status = status
        self.code = code
        self.body = body
        super().__init__(message)

    @property
    def hint(self) -> str:
        return HINTS.get(self.status, "")


class AgentArtsUnavailable(AgentArtsError):
    """连不上平台（网络层），与"平台返回错误"区分开。"""


class AgentArtsClient:
    def __init__(self, config, tokens, *, opener=None) -> None:
        self._config = config
        self._tokens = tokens
        # 注入点：测试里替换 opener 即可完全离线。
        self._opener = opener or urllib.request.urlopen
        #: 运行时形态下最终被平台接受的请求体，排障时很有用（不敏感）。
        self.last_body: dict | None = None

    # ------------------------------------------------------------ 对外
    def ensure_ready(self) -> str:
        """在开始流式响应**之前**先确认凭据可用。

        这样"取不到凭据"会变成一次干净的 502/503，而不是流到一半才断——
        后者在前端表现为"消息发出去没反应"，最难排查。

        运行时形态用静态 API Key，不需要请求 IAM。
        """
        if self._config.is_runtime:
            if not self._config.api_key:
                raise AgentArtsError("未配置 AGENTARTS_API_KEY（工作负载身份 API Key）", status=401)
            return self._config.api_key
        try:
            return self._tokens.get()
        except Exception as error:  # noqa: BLE001 - 统一翻译为网关可处理的错误
            raise AgentArtsError("取 Token 失败：%s" % (error,), status=getattr(error, "status", 0),
                                 body=getattr(error, "body", "")) from error

    def run_agent(self, conversation_id: str, query: str, inputs: dict | None = None, *,
                  timeout: int | None = None):
        if self._config.is_runtime:
            return self._run_runtime(conversation_id, query, inputs, timeout=timeout)
        url = self._config.runtime_url(conversation_id)
        body = {"query": query, "inputs": dict(inputs or {})}
        return self._stream(url, body, timeout=timeout)

    def _run_runtime(self, conversation_id: str, query: str, inputs: dict | None = None, *,
                     timeout: int | None = None):
        """运行时调用：Bearer API Key + 会话头。

        请求体形态在不同版本的控制台里不完全一致（`{"query"}` / `{"inputs":{"query"}}`
        都见过），所以这里按"最可能 → 兜底"的顺序试，**只有 4xx 才换下一个**；
        200 就按流处理，不再重试。这样平台升级时不用改代码也能继续跑。
        """
        url = self._config.invocation_url()
        session_ref = str((inputs or {}).get("sessionRef") or "")

        candidates = [
            {"inputs": {"query": query}},
            {"query": query},
        ]
        if session_ref:
            candidates = [
                {"inputs": {"query": query, "sessionRef": session_ref}},
                {"inputs": {"query": query}},
                {"query": query, "inputs": {"sessionRef": session_ref}},
                {"query": query},
            ]

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % self._config.api_key,
            # 平台的会话标识：1~32 位，仅小写字母/数字/中划线。
            "x-hw-agentarts-session-id": (conversation_id or "")[:32],
            "Accept": "text/event-stream",
            "stream": "true",
        }

        last_error: AgentArtsError | None = None
        for body in candidates:
            stream = self._post_stream(url, body, headers, timeout=timeout)
            try:
                first = next(stream, None)
            except AgentArtsError as error:
                last_error = error
                # 401/403 换 body 没用，直接抛；400/422 才可能是不认字段，值得再试。
                if error.status in (401, 403):
                    raise
                continue
            self.last_body = body
            if first is not None:
                yield first
            yield from stream
            return
        raise last_error or AgentArtsError("运行时调用失败", status=0)

    def run_workflow(self, workflow_id: str, conversation_id: str, inputs: dict | None = None, *,
                     timeout: int | None = None):
        url = self._config.workflow_url(workflow_id, conversation_id)
        body = {"inputs": dict(inputs or {}), "plugin_configs": []}
        return self._stream(url, body, timeout=timeout)

    # ------------------------------------------------------------ 内部
    def _stream(self, url: str, body: dict, *, timeout: int | None = None):
        """低代码 runAgent 形态。"""
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": self._tokens.get(),
            # 智能体应用目前只支持流式调用；这里显式声明，避免依赖平台默认值。
            "stream": "true",
            "X-Invoke-Mode": self._config.invoke_mode,
            "Accept": "text/event-stream",
        }
        yield from self._post_stream(url, body, headers, timeout=timeout)

    def _post_stream(self, url: str, body: dict, headers: dict, *, timeout: int | None = None):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method="POST", headers=headers)

        try:
            response = self._opener(request, timeout=timeout or self._config.timeout_seconds)
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", "replace")
            code = _error_code(text)
            raise AgentArtsError(
                "AgentArts 返回 HTTP %s%s" % (error.code, "（%s）" % code if code else ""),
                status=error.code, code=code, body=text[:500]) from error
        except urllib.error.URLError as error:
            raise AgentArtsUnavailable(
                "无法连接 AgentArts：%s" % (error.reason,), status=0) from error

        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    item = json.loads(chunk)
                except ValueError:
                    # 非 JSON 的 data 段（例如平台探活文本）不阻断主流程。
                    continue
                if isinstance(item, dict):
                    yield item
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()


def _error_code(text: str) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("error_code", "errorCode", "code"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""
