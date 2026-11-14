# -*- coding: utf-8 -*-
"""网关 → MCP 的通用调用器。

为什么网关要直连 MCP
--------------------
判分、更新掌握度、请求重排都是**确定性工具**，不需要模型参与决策。
让前端走一遍工作流反而多两层抽象、多几处参数类型转换（我们已经在
`availableMinutes` / `answers` 上各踩过一次）。

这里的原则：**交互由前端驱动，计算由工具完成，模型只负责出题与讲解。**
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _config() -> dict:
    return {
        "base": (os.getenv("MCP_BASE_URL") or "https://127.0.0.1:8443").rstrip("/"),
        "key": os.getenv("MCP_API_KEY") or "",
        "session": os.getenv("MCP_SESSION_REF") or "",
    }


def configured() -> bool:
    config = _config()
    return bool(config["key"])


def call_tool(name: str, arguments: dict, timeout: int = 20) -> dict:
    """调用一个 MCP 工具，返回工具内部的 JSON（已解包 content[0].text）。"""
    config = _config()
    if not config["key"]:
        return {"ok": False, "errorCode": "MCP_UNCONFIGURED",
                "message": "网关未配置 MCP_API_KEY"}

    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }).encode("utf-8")

    request = urllib.request.Request(
        config["base"] + "/mcp", data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": config["key"]})

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return {"ok": False, "errorCode": "MCP_UNREACHABLE", "message": str(error)}

    content = (body.get("result") or {}).get("content") or []
    text = content[0].get("text") if content and isinstance(content[0], dict) else ""
    try:
        return json.loads(text or "{}")
    except ValueError:
        return {"ok": False, "errorCode": "MCP_BAD_OUTPUT", "message": "工具返回无法解析"}


def call_with_session(name: str, arguments: dict, timeout: int = 20) -> dict:
    """自动带上演示会话的 sessionRef。"""
    payload = dict(arguments)
    payload.setdefault("sessionRef", _config()["session"])
    return call_tool(name, payload, timeout=timeout)
