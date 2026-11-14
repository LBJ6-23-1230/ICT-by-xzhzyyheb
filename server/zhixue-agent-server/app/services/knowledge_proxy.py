# -*- coding: utf-8 -*-
"""网关侧的知识检索代理：把 MCP 的 `search_learning_materials` 借给前端用。

为什么网关要代调 MCP
---------------------
演示要求"点开看依据"。依据（文件名、章节、片段哈希）只有 MCP 侧的检索工具才有；
但浏览器不能直连 MCP——那需要 `MCP_API_KEY`，而密钥绝不能下发到前端。

所以由网关带上密钥调用，前端只拿到脱敏后的命中结果。
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

#: 本机 MCP 用自签证书，网关只连 127.0.0.1，这里不做主机名校验。
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _config() -> dict:
    return {
        "base": (os.getenv("MCP_BASE_URL") or "https://127.0.0.1:8443").rstrip("/"),
        "key": os.getenv("MCP_API_KEY") or "",
        "session": os.getenv("MCP_SESSION_REF") or "",
    }


def available() -> bool:
    config = _config()
    return bool(config["key"] and config["session"])


def search(query: str, top_k: int = 5) -> dict:
    """调用 MCP 的知识检索，返回 `{"ok": bool, "hits": [...]}`。"""
    config = _config()
    if not available():
        return {"ok": False, "error": "MCP_UNCONFIGURED",
                "message": "网关未配置 MCP_BASE_URL / MCP_API_KEY / MCP_SESSION_REF"}
    if not (query or "").strip():
        return {"ok": True, "hits": []}

    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "search_learning_materials",
                   "arguments": {"sessionRef": config["session"],
                                 "query": query.strip()[:200],
                                 "topK": max(1, min(int(top_k), 10))}},
    }).encode("utf-8")

    request = urllib.request.Request(
        config["base"] + "/mcp", data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": config["key"]})

    try:
        with urllib.request.urlopen(request, timeout=8, context=_SSL_CONTEXT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return {"ok": False, "error": "MCP_UNREACHABLE", "message": str(error)}

    result = body.get("result") or {}
    content = result.get("content") or []
    text = content[0].get("text") if content and isinstance(content[0], dict) else ""
    try:
        inner = json.loads(text or "{}")
    except ValueError:
        return {"ok": False, "error": "MCP_BAD_OUTPUT", "message": "检索结果无法解析"}

    if not inner.get("ok"):
        return {"ok": False, "error": inner.get("errorCode") or "MCP_ERROR",
                "message": inner.get("message") or ""}

    data = inner.get("data") or {}
    hits = [{
        "chunkId": hit.get("chunkId"),
        "fileName": hit.get("fileName"),
        "headingPath": hit.get("headingPath"),
        "contentHash": hit.get("contentHash"),
        "score": hit.get("score"),
        "text": hit.get("text"),
    } for hit in (data.get("hits") or [])]

    return {"ok": True, "hits": hits, "query": data.get("query"),
            "retrievalMode": data.get("retrievalMode")}
