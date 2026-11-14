# -*- coding: utf-8 -*-
"""统一响应信封。

为什么所有工具都必须返回同一个信封
--------------------------------
MCP 工具的返回会直接进入模型上下文。如果每个工具各写一套 `{"data": ...}` /
`{"result": ...}` / 裸列表，模型在"下一步该做什么"上就会摇摆，评测脚本也没法统一断言。
信封固定为：

    {"ok": bool, "data": Any, "errorCode": str|null, "message": str|null,
     "details": Any, "traceId": str, "version": str}

`ok=false` 时 `data` 必须为空——不允许"部分成功但字段残缺"的模糊状态。
"""

from __future__ import annotations

import uuid

VERSION = "mcp-1.0"

#: 错误码是**对外契约**，不要在工具里临时造新词。新增要先改这里与 README。
UNAUTHORIZED = "UNAUTHORIZED"
FORBIDDEN = "FORBIDDEN"
NOT_FOUND = "NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
CONFLICT = "CONFLICT"
INSUFFICIENT_MATERIALS = "INSUFFICIENT_MATERIALS"
TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
TOOL_TIMEOUT = "TOOL_TIMEOUT"
INTERNAL_ERROR = "INTERNAL_ERROR"


def new_trace_id() -> str:
    return "mcp-" + uuid.uuid4().hex[:16]


def ok(data=None, *, trace_id: str = "", **details) -> dict:
    return {
        "ok": True,
        "data": data,
        "errorCode": None,
        "message": None,
        "details": details or None,
        "traceId": trace_id or new_trace_id(),
        "version": VERSION,
    }


def fail(error_code: str, message: str, *, trace_id: str = "", details=None) -> dict:
    return {
        "ok": False,
        "data": None,
        "errorCode": error_code,
        "message": message,
        "details": details,
        "traceId": trace_id or new_trace_id(),
        "version": VERSION,
    }


def as_text(envelope: dict) -> str:
    """MCP 的 `content` 必须是文本块；这里统一序列化，保证中文不被转义成 \\uXXXX。"""
    import json

    return json.dumps(envelope, ensure_ascii=False)
