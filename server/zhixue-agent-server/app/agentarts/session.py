# -*- coding: utf-8 -*-
"""会话与运行记录的映射层。

两个"ID"必须分清（这是接入期最容易做错的地方）
--------------------------------------------
* `conversationId` —— 平台的会话标识，**只允许 1~64 位 `[A-Za-z0-9-_]`**。
  本地 `userId` 可能是中文昵称、手机号，直接塞进去必然被平台拒绝，所以这里统一生成
  `conv<32位hex>`（36 字符），再用集合里的映射反查真实用户。
* `sessionRef` —— **一次性随机引用**，随 `runAgent` 的 `inputs` 传给智能体，再由智能体
  作为 MCP 工具的首个参数回传。MCP 侧凭它换 `userId`。
  为什么不让模型直接传 `userId`：模型可能被提示词注入影响，`userId` 一旦成为工具入参，
  就等于把"能不能查别人的数据"交给了模型。`sessionRef` 是随机串，模型无从伪造。

运行记录（`agentarts_runs`）存在的意义：SSE 只在连接存活期间有效，页面一刷新、
App 一次轮询失败，时间线就没了。把事件落库后，`GET /api/v2/agent/runs/<id>` 可以
回放，评测脚本也能离线分析——这是"效果评估"能不能做的前提。
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

SESSIONS = "agentarts_sessions"
RUNS = "agentarts_runs"
FEEDBACK = "agentarts_feedback"

#: 单次运行最多保留的事件数。超出后保留头部与尾部，中间丢弃并标记，避免单条记录无限膨胀。
MAX_EVENTS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ 新建标识
def new_conversation_id() -> str:
    """32 位十六进制。

    平台约束：`conversation_id` 1~64 位，但**运行时形态的会话头只有 1~32 位**，
    且只允许小写字母/数字/中划线。统一取 32 位，两种形态都能直接用。
    """
    return uuid.uuid4().hex


def new_session_ref() -> str:
    return "sref" + secrets.token_hex(16)


def new_run_id() -> str:
    return "run-" + uuid.uuid4().hex[:20]


def new_feedback_id() -> str:
    return "fb-" + uuid.uuid4().hex[:16]


# ------------------------------------------------------------------ 会话
def bind_session(repository, *, user_id: str, chat_session_id: str, agent_id: str) -> dict:
    """按 (userId, chatSessionId) 复用或新建平台会话。

    同一个聊天会话必须复用同一个 `conversationId`，否则平台侧的多轮上下文会断，
    表现为"上一轮说了上传了讲义，下一轮它不记得"。
    """
    for existing in repository.list(SESSIONS):
        if (isinstance(existing, dict)
                and existing.get("userId") == user_id
                and existing.get("chatSessionId") == chat_session_id):
            return existing

    conversation_id = new_conversation_id()
    record = {
        "conversationId": conversation_id,
        "userId": user_id,
        "chatSessionId": chat_session_id,
        "agentId": agent_id,
        "sessionRef": new_session_ref(),
        "createdAt": _now(),
        "lastActiveAt": _now(),
    }
    repository.save(SESSIONS, conversation_id, record)
    return record


def resolve_session(repository, conversation_id: str) -> dict | None:
    return repository.get(SESSIONS, conversation_id)


def resolve_by_session_ref(repository, session_ref: str) -> dict | None:
    if not session_ref:
        return None
    for item in repository.list(SESSIONS):
        if isinstance(item, dict) and item.get("sessionRef") == session_ref:
            return item
    return None


def touch_session(repository, conversation_id: str) -> None:
    record = repository.get(SESSIONS, conversation_id)
    if not isinstance(record, dict):
        return
    repository.save(SESSIONS, conversation_id, dict(record, lastActiveAt=_now()))


# ------------------------------------------------------------------ 运行
def start_run(repository, *, conversation_id: str, user_id: str,
              chat_session_id: str, agent_id: str) -> dict:
    run_id = new_run_id()
    record = {
        "runId": run_id,
        "conversationId": conversation_id,
        "userId": user_id,
        "chatSessionId": chat_session_id,
        "agentId": agent_id,
        "status": "running",
        "startedAt": _now(),
        "finishedAt": "",
        "events": [],
        "reply": "",
        "tools": [],
        "error": "",
    }
    repository.save(RUNS, run_id, record)
    return record


def append_events(repository, run_id: str, events: list[dict]) -> dict | None:
    """批量追加事件；单条记录有上限，超限时保留首尾并置 `truncated`。"""
    record = repository.get(RUNS, run_id)
    if not isinstance(record, dict) or not events:
        return record
    merged = list(record.get("events") or [])
    merged.extend(events)
    truncated = bool(record.get("truncated"))
    if len(merged) > MAX_EVENTS:
        head = merged[: MAX_EVENTS // 4]
        tail = merged[-(MAX_EVENTS - MAX_EVENTS // 4):]
        merged = head + tail
        truncated = True
    updated = dict(record, events=merged, truncated=truncated)
    repository.save(RUNS, run_id, updated)
    return updated


def finish_run(repository, run_id: str, *, status: str, reply: str = "",
               tools: list[str] | None = None, error: str = "") -> dict | None:
    record = repository.get(RUNS, run_id)
    if not isinstance(record, dict):
        return None
    updated = dict(
        record,
        status=status,
        finishedAt=_now(),
        reply=reply[:8000],
        tools=list(tools or []),
        error=error[:500],
    )
    repository.save(RUNS, run_id, updated)
    return updated


def get_run(repository, run_id: str) -> dict | None:
    return repository.get(RUNS, run_id)


def events_since(repository, run_id: str, after: int = 0) -> list[dict]:
    record = repository.get(RUNS, run_id)
    if not isinstance(record, dict):
        return []
    events = list(record.get("events") or [])
    return events[max(0, after):]


# ------------------------------------------------------------------ 反馈
def save_feedback(repository, *, run_id: str, user_id: str, rating: str,
                  note: str = "") -> dict:
    feedback_id = new_feedback_id()
    record = {
        "feedbackId": feedback_id,
        "runId": run_id,
        "userId": user_id,
        "rating": rating,
        "note": note[:1000],
        "createdAt": _now(),
    }
    repository.save(FEEDBACK, feedback_id, record)
    return record
