# -*- coding: utf-8 -*-
"""只读工具：读上下文、检索资料、取学习证据。

三个工具都只依赖 repository 与纯逻辑模块，不调用大模型——诊断与规划的证据基础
必须可复现，不能用"再问一次模型"来生成"证据"。
"""

from __future__ import annotations

from .. import envelope
from ..auth import require_actor
from ..business import modules

SESSION_REF = {"type": "string", "description": "网关签发的一次性会话引用，用于换取真实 userId"}


def _recent_plan(repository, user_id: str):
    plans = [item for item in repository.list("plans")
             if isinstance(item, dict) and item.get("userId") == user_id]
    if not plans:
        return None
    return max(plans, key=lambda item: int(item.get("version") or 0))


def get_learning_context(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error
    user_id = actor["userId"]

    profile = ctx.repository.get("profiles", user_id) or {}
    plan = _recent_plan(ctx.repository, user_id)
    mastery = [row for row in (profile.get("mastery") or []) if isinstance(row, dict)]
    weakest = sorted(mastery, key=lambda row: float(row.get("masteryScore") or 0))[:5]
    tasks = (plan or {}).get("tasks") or []

    # 课程表与 DDL：排计划必须先知道"什么时候有课、什么时候交作业"。
    # 复用同一个工具返回，避免新增工具导致平台侧 schema 再刷新一次。
    try:
        from app.services import study_import
        deadlines = study_import.upcoming(ctx.repository, user_id, limit=8)
        courses = study_import.courses_of(ctx.repository, user_id)
    except Exception:  # noqa: BLE001 - 导入模块缺失不应影响上下文
        deadlines, courses = [], []

    return envelope.ok({
        "userId": user_id,
        "goal": profile.get("goal") or "",
        "examDate": profile.get("examDate") or "",
        "freeTimeSlots": profile.get("freeTimeSlots") or [],
        "profileVersion": profile.get("profileVersion"),
        # 最近 8 条 DDL（按截止日期升序）；daysLeft 为负表示已过期
        "deadlines": deadlines,
        "deadlineCount": len(deadlines),
        "courses": courses,
        "weakPoints": [
            {"knowledgePointId": row.get("knowledgePointId"),
             "knowledgePointName": row.get("knowledgePointName") or "",
             "masteryScore": row.get("masteryScore")}
            for row in weakest
        ],
        "plan": None if plan is None else {
            "planId": plan.get("planId"),
            "version": plan.get("version"),
            "taskCount": len(tasks),
            "totalMinutes": sum(int(task.get("durationMinutes") or 0) for task in tasks
                                if isinstance(task, dict)),
            "budgetSummary": plan.get("budgetSummary"),
        },
        "availableMinutes": ((plan or {}).get("constraints") or {}).get("availableMinutes"),
        "generatedAt": (plan or {}).get("generatedAt") or "",
    })


def search_learning_materials(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error
    user_id = actor["userId"]

    query = str(arguments.get("query") or "").strip()
    kb_id = str(arguments.get("kbId") or "").strip()
    try:
        top_k = max(1, min(int(arguments.get("topK") or 5), 10))
    except (TypeError, ValueError):
        top_k = 5

    chunks = [row for row in ctx.repository.list("chunks")
              if isinstance(row, dict) and row.get("userId") == user_id
              and (not kb_id or row.get("kbId") == kb_id)]
    if not chunks:
        # 没资料时如实说"没有"，不要编一段看起来像引用的话。
        return envelope.ok({"query": query, "retrievalMode": "none", "hits": [],
                            "note": "该用户还没有可用资料切片"}, trace_id=envelope.new_trace_id())

    result = modules().knowledge.search(chunks, query, top_k=top_k)
    hits, mode = result if isinstance(result, tuple) else (result, "keyword")

    # 文件名/解析时间在 documents 表，切片表里没有 —— 不 join 的话
    # 前端只能显示"来源：空"，"知识接入"就失去可视化证据。
    documents = {row.get("documentId"): row for row in ctx.repository.list("documents")
                 if isinstance(row, dict) and row.get("userId") == user_id}

    import hashlib

    rows = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("text") or "")
        doc = documents.get(hit.get("documentId")) or {}
        rows.append({
            "chunkId": hit.get("chunkId") or "",
            "documentId": hit.get("documentId") or "",
            "kbId": hit.get("kbId") or "",
            "fileName": hit.get("fileName") or doc.get("fileName") or "",
            "headingPath": hit.get("headingPath") or "",
            # 切片里没存哈希，就按文本现算一个，保证引用可校验。
            "contentHash": hit.get("contentHash") or hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "parsedAt": doc.get("parsedAt") or "",
            "score": hit.get("score"),
            "text": text[:600],
        })

    return envelope.ok({"query": query, "retrievalMode": mode, "hits": rows,
                        "hitCount": len(rows)}, trace_id=envelope.new_trace_id())


def get_learning_evidence(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error
    user_id = actor["userId"]
    point = str(arguments.get("knowledgePointId") or "").strip()

    profile = ctx.repository.get("profiles", user_id) or {}
    mastery = [row for row in (profile.get("mastery") or []) if isinstance(row, dict)]
    if point:
        mastery = [row for row in mastery if row.get("knowledgePointId") == point]

    wrong_book = [row for row in ctx.repository.list("wrong_book")
                  if isinstance(row, dict) and row.get("userId") == user_id]
    if point:
        wrong_book = [row for row in wrong_book if row.get("knowledgePointId") == point]

    evidence = [row for row in ctx.repository.list("evidences")
                if isinstance(row, dict) and row.get("userId") == user_id]

    weak = [{"knowledgePointId": row.get("knowledgePointId"),
             "masteryScore": row.get("masteryScore"),
             "source": row.get("source") or ""}
            for row in sorted(mastery, key=lambda item: float(item.get("masteryScore") or 0))[:5]]

    recent = [{"exerciseId": row.get("exerciseId"),
               "knowledgePointId": row.get("knowledgePointId") or "",
               "wrongCount": row.get("wrongCount") or 0,
               "lastPracticedAt": row.get("lastPracticedAt") or ""}
              for row in wrong_book[:5]]

    return envelope.ok({
        "userId": user_id,
        # `hasEvidence` 是给诊断工作流做分支判断用的：没有证据时它应当去要一次短测，
        # 而不是直接给"薄弱点"结论。
        "hasEvidence": bool(weak or wrong_book),
        "weakPoints": weak,
        "wrongBookCount": len(wrong_book),
        "recentWrong": recent,
        "evidenceCount": len(evidence),
        "evidenceIds": [row.get("evidenceId") for row in evidence[:10]
                        if row.get("evidenceId")],
    })
