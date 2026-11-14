# -*- coding: utf-8 -*-
"""课程表 / 作业考试 DDL 导入。

设计取舍
--------
* 课表：图片识别沿用原项目的 VL 提示词（课表是表格，模型比规则靠谱）。
* DDL：**不调模型**，用确定性解析。学生贴进来的通常是
  "数据结构 作业3 2026-12-01" 这种一行一条，正则足够；
  让模型解析反而可能编出并不存在的截止日期。
* 落库：课程进 `courses`，DDL 进 `deadlines`，key 都是 `{userId}:{标识}`，
  和其它集合的隔离口径一致。
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime, timezone

DATE_PATTERN = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_base64(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    return "".join(value.split())


def validate_image(image_base64: str) -> tuple[str, str]:
    """返回 (清洗后的 base64, 错误信息)。"""
    payload = _clean_base64(image_base64)
    if not payload:
        return "", "缺少图片数据"
    try:
        raw = base64.b64decode(payload[:256], validate=True)
    except (binascii.Error, ValueError):
        return "", "图片 base64 不合法"
    if len(payload) > MAX_IMAGE_BYTES * 4 // 3:
        return "", "图片过大，请压缩到 5MB 以内"
    del raw
    return payload, ""


def recognize_schedule(image_base64: str) -> dict:
    """课表截图 → 课程列表。复用原项目提示词，失败如实返回。"""
    payload, error = validate_image(image_base64)
    if error:
        return {"ok": False, "error_code": "VALIDATION_ERROR", "message": error}

    from app.agent import chat_llm

    if not chat_llm.llm_ready():
        return {"ok": False, "error_code": "LLM_NOT_CONFIGURED",
                "message": "识别课表需要配置 DASHSCOPE_API_KEY"}
    try:
        response = chat_llm._call_llm_with_image(  # noqa: SLF001
            "你是课表截图识别助手。只提取图片中真实可见的课程，不得补造。"
            "只输出 JSON，不要 markdown。无法识别的考试日期留空。",
            "请识别全部课程，输出 {\"courses\":[{\"courseName\":\"课程名\","
            "\"examDate\":\"YYYY-MM-DD 或空字符串\",\"priority\":\"高/中/低\","
            "\"studyHours\":0}]}。同一课程只保留一条；看不清的课程不要输出。",
            payload, response_json=True, cache_scope="schedule")
        parsed = chat_llm.extract_json(response) or {}
    except Exception as error:  # noqa: BLE001 - 识别失败要给可操作信息
        return {"ok": False, "error_code": "SCHEDULE_OCR_FAILED",
                "message": "课表识别失败：%s" % (type(error).__name__)}

    rows = parsed.get("courses")
    if not isinstance(rows, list):
        return {"ok": False, "error_code": "SCHEDULE_OCR_INVALID_RESPONSE",
                "message": "没认出课程列表，请换一张更清晰的课表截图"}
    courses = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("courseName") or "").strip()
        if not name:
            continue
        courses.append({
            "courseName": name[:60],
            "examDate": str(row.get("examDate") or "").strip()[:20],
            "priority": str(row.get("priority") or "中")[:4],
        })
    if not courses:
        return {"ok": False, "error_code": "SCHEDULE_OCR_EMPTY",
                "message": "没从图片里识别到课程，请裁剪到课表区域后重试"}
    return {"ok": True, "courses": courses}


def parse_deadlines(text: str) -> dict:
    """把"一行一条"的 DDL 文本解析成结构化数据（不调模型）。

    支持格式（日期可在行首、行中、行尾）：
        数据结构 作业3 2026-12-01
        2026/12/15 计算机网络 期末
        概率论, 习题2, 2026.12.20
    解析不出来的行进 `skipped`，让用户知道哪些没识别，而不是静默丢掉。
    """
    items, skipped = [], []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = DATE_PATTERN.search(line)
        if not match:
            skipped.append(line[:60])
            continue
        year, month, day = match.groups()
        due = "%s-%02d-%02d" % (year, int(month), int(day))
        title = (line[:match.start()] + " " + line[match.end():]).strip(" ，,、;；")
        title = re.sub(r"\s{2,}", " ", title).strip()
        if not title:
            skipped.append(line[:60])
            continue

        lowered = title.casefold()
        kind = "exam" if re.search(r"考试|期末|期中|exam|final|midterm", lowered) else "assignment"
        parts = re.split(r"[\s，,、;；]+", title)
        course = parts[0] if parts else ""
        items.append({
            "title": title[:120],
            "courseName": course[:40],
            "dueDate": due,
            "type": kind,
            "source": "text",
        })
    return {"ok": True, "items": items, "skipped": skipped}


def save_courses(repository, user_id: str, courses: list) -> int:
    saved = 0
    for course in courses:
        name = str(course.get("courseName") or "").strip()
        if not name:
            continue
        key = "%s:%s" % (user_id, name)
        record = dict(course)
        record.update({"userId": user_id, "courseName": name, "updatedAt": _now()})
        repository.save("courses", key, record)
        saved += 1
    return saved


def save_deadlines(repository, user_id: str, items: list) -> int:
    saved = 0
    for item in items:
        title = str(item.get("title") or "").strip()
        due = str(item.get("dueDate") or "").strip()
        if not title or not due:
            continue
        key = "%s:%s:%s" % (user_id, due, title[:40])
        record = dict(item)
        record.update({"userId": user_id, "title": title, "dueDate": due,
                       "createdAt": _now()})
        repository.save("deadlines", key, record)
        saved += 1
    return saved


def upcoming(repository, user_id: str, limit: int = 10) -> list:
    """按截止日期升序返回未过期（或最近）的 DDL。"""
    today = datetime.now(timezone.utc).date().isoformat()
    rows = [row for row in repository.list("deadlines")
            if isinstance(row, dict) and row.get("userId") == user_id]
    rows.sort(key=lambda row: str(row.get("dueDate") or "9999"))
    result = []
    for row in rows[:limit]:
        due = str(row.get("dueDate") or "")
        result.append({
            "title": row.get("title"),
            "courseName": row.get("courseName"),
            "dueDate": due,
            "type": row.get("type"),
            # 负数表示已过期，前端可以直接标红
            "daysLeft": (datetime.fromisoformat(due).date()
                         - datetime.fromisoformat(today).date()).days if due else None,
        })
    return result


def courses_of(repository, user_id: str) -> list:
    return [{"courseName": row.get("courseName"), "examDate": row.get("examDate"),
             "priority": row.get("priority")}
            for row in repository.list("courses")
            if isinstance(row, dict) and row.get("userId") == user_id]
