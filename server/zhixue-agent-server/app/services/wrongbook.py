# -*- coding: utf-8 -*-
"""错题本：收集、列出、删除。

与 MCP 的 `record_wrong_question` 的区别
----------------------------------------
* MCP 工具：**记录 + 下调掌握度**，供 Agent / 工作流在"用户确认做错"时调用。
* 这里：**只记录，不动掌握度**。因为交卷时 `update_mastery` 已经按分数改过一次，
  再调工具会二次扣分（实测会让 45 分掉到 37，明显不合理）。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(user_id: str, exercise_id: str) -> str:
    return "%s:%s" % (user_id, exercise_id)


def record(repository, user_id: str, item: dict) -> dict:
    """写入一条错题（幂等：同题只累加 wrongCount）。"""
    stem = str(item.get("stem") or "").strip()
    point = str(item.get("knowledgePointId") or "").strip() or "未分类"
    exercise_id = str(item.get("exerciseId") or "").strip()
    if not exercise_id:
        digest = hashlib.sha1((stem + point).encode("utf-8")).hexdigest()[:10]
        exercise_id = "wrong-" + digest

    key = _key(user_id, exercise_id)
    existing = repository.get("wrong_book", key) or {}
    record_row = {
        "userId": user_id,
        "exerciseId": exercise_id,
        "knowledgePointId": point,
        "knowledgePointName": str(item.get("knowledgePointName") or point),
        "stem": stem[:2000],
        "options": item.get("options") or existing.get("options") or [],
        "answerKey": str(item.get("correctAnswer") or existing.get("answerKey") or ""),
        "myAnswer": str(item.get("answer") or existing.get("myAnswer") or ""),
        "explanation": str(item.get("reason") or item.get("explanation")
                           or existing.get("explanation") or ""),
        "sources": item.get("sources") or existing.get("sources") or [],
        "source": str(item.get("sourceType") or existing.get("source") or "grade"),
        "correctCount": int(existing.get("correctCount") or 0),
        "wrongCount": int(existing.get("wrongCount") or 0) + 1,
        "addedAt": existing.get("addedAt") or _now(),
        "lastWrongAt": _now(),
    }
    repository.save("wrong_book", key, record_row)
    return record_row


def listing(repository, user_id: str, limit: int = 50) -> list:
    rows = [row for row in repository.list("wrong_book")
            if isinstance(row, dict) and row.get("userId") == user_id]
    rows.sort(key=lambda row: str(row.get("lastWrongAt") or ""), reverse=True)
    return [{
        "exerciseId": row.get("exerciseId"),
        "knowledgePointId": row.get("knowledgePointId"),
        "knowledgePointName": row.get("knowledgePointName"),
        "stem": row.get("stem"),
        "options": row.get("options") or [],
        "answerKey": row.get("answerKey"),
        "myAnswer": row.get("myAnswer"),
        "explanation": row.get("explanation"),
        "sources": row.get("sources") or [],
        "wrongCount": int(row.get("wrongCount") or 0),
        "correctCount": int(row.get("correctCount") or 0),
        "lastWrongAt": row.get("lastWrongAt"),
        "source": row.get("source"),
    } for row in rows[:limit]]


def remove(repository, user_id: str, exercise_id: str) -> bool:
    key = _key(user_id, exercise_id)
    existing = repository.get("wrong_book", key)
    if not isinstance(existing, dict):
        return False
    repository.delete("wrong_book", key)
    return True


def count(repository, user_id: str) -> int:
    return sum(1 for row in repository.list("wrong_book")
               if isinstance(row, dict) and row.get("userId") == user_id)
