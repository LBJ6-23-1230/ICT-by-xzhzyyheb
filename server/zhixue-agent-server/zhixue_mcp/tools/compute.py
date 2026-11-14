# -*- coding: utf-8 -*-
"""计算类工具：优先级、预算裁剪、判分、计划差异。

这一类全部是**纯函数 + 服务端数据读取**，不写库、不调模型。
判分尤其关键：分数只能来自确定性规则，标准答案只在服务端读取，
**绝不作为工具入参**（否则模型可以自己"提供"一份答案键，分数就失去意义）。
"""

from __future__ import annotations

from .. import envelope
from ..auth import require_actor
from ..business import modules
from ..registry import parse_json_arg

SESSION_REF = {"type": "string", "description": "网关签发的一次性会话引用，用于换取真实 userId"}


def calculate_learning_priority(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error

    points = arguments.get("knowledgePoints")
    if not isinstance(points, list) or not points:
        # 工作流里更常见的用法：把上一个工具的输出整段丢进来，
        # 由这里提取 `data.weakPoints`，省掉一次"模型重新抄数组"的易错步骤。
        points = _points_from_context_text(arguments.get("contextText"))
    if not isinstance(points, list) or not points:
        return envelope.fail(envelope.VALIDATION_ERROR,
                             "knowledgePoints 不能为空数组（也可传 contextText 让我提取）")
    for item in points:
        if not isinstance(item, dict) or not item.get("knowledgePointId"):
            return envelope.fail(envelope.VALIDATION_ERROR,
                                 "knowledgePoints 每项都必须含 knowledgePointId")

    context = arguments.get("context") if isinstance(arguments.get("context"), dict) else {}
    ranked = modules().priority.calculate_learning_priority(points, context)
    return envelope.ok({"ranked": ranked, "count": len(ranked)}, trace_id=envelope.new_trace_id())


def _points_from_context_text(raw) -> list:
    """从 `get_learning_context` 的信封文本里取出 weakPoints。"""
    payload = parse_json_arg(raw, dict)
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), (dict, list)) else payload
    if isinstance(data, dict):
        for key in ("weakPoints", "knowledgePoints", "points"):
            value = data.get(key)
            if isinstance(value, list) and value:
                return value
    if isinstance(data, list):
        return data
    return []


def fit_plan_to_budget(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error

    plan = arguments.get("plan")
    if not isinstance(plan, dict):
        # 允许直接传 JSON 文本（工作流节点之间传的通常是文本，且模型常带 ```json 围栏）。
        plan = parse_json_arg(arguments.get("planText"), dict)
        if isinstance(plan, dict) and isinstance(plan.get("data"), dict):
            plan = plan["data"].get("plan") or plan
    if not isinstance(plan, dict):
        return envelope.fail(envelope.VALIDATION_ERROR, "plan 必须是计划对象（或 planText 传 JSON 文本）")
    minutes = arguments.get("availableMinutes")
    if isinstance(minutes, str) and minutes.strip().isdigit():
        minutes = int(minutes.strip())
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        return envelope.fail(envelope.VALIDATION_ERROR, "availableMinutes 必须是 0~1440 的整数")
    point = str(arguments.get("prioritizePoint") or "").strip() or None

    # 直接复用已过测的裁剪规则：整任务排入 / 排不下就进 unscheduledTasks，
    # 不做"拆半"这种会破坏用户预期的事。
    result = modules().budget.fit_plan_to_budget(plan, minutes, point)
    return envelope.ok({"plan": result.get("plan"), "changed": bool(result.get("changed")),
                        "budgetSummary": (result.get("plan") or {}).get("budgetSummary")})


def grade_exercise(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error
    user_id = actor["userId"]

    answers = arguments.get("answers")
    if not isinstance(answers, list) or not answers:
        # 工作流里更常见的传法：上一节点输出的 JSON 文本。
        answers = parse_json_arg(arguments.get("answersText"), list)
    if not isinstance(answers, list) or not answers:
        return envelope.fail(envelope.VALIDATION_ERROR,
                             "answers 不能为空数组（也可传 answersText 文本）")

    # 标准答案与知识点映射只从服务端读取；请求里若带 answerKeys 一律忽略。
    answer_keys: dict[str, str] = {}
    knowledge_points: dict[str, str] = {}
    names: dict[str, str] = {}
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        exercise_id = str(answer.get("exerciseId") or "")
        if not exercise_id:
            continue
        record = ctx.repository.get("exercises", exercise_id)
        if not isinstance(record, dict):
            continue
        if record.get("answerKey"):
            answer_keys[exercise_id] = str(record["answerKey"])
        if record.get("knowledgePointId"):
            knowledge_points[exercise_id] = str(record["knowledgePointId"])
            names[exercise_id] = str(record.get("knowledgePointName") or "")

    if not answer_keys:
        return envelope.fail(envelope.NOT_FOUND,
                             "这些题目在服务端找不到标准答案，拒绝判分",
                             details={"exerciseIds": [item.get("exerciseId") for item in answers
                                                      if isinstance(item, dict)]})

    profile = ctx.repository.get("profiles", user_id) or {}
    old_by_point = {row.get("knowledgePointId"): row.get("masteryScore") or 0
                    for row in (profile.get("mastery") or []) if isinstance(row, dict)}

    result = modules().assessment.grade_exercise(
        answers=answers,
        answer_keys=answer_keys,
        knowledge_points=knowledge_points,
        old_mastery_by_point=old_by_point,
        knowledge_point_names={key: names.get(key, "") for key in names},
        exercise_result_id=str(arguments.get("exerciseResultId") or "") or None,
    )
    payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)

    # 逐题对错 + 解析：解析与来源在出题时就已生成并落库，
    # 直接回传即可——比让模型"再讲一遍"更准，也不会超时或编造。
    submitted = {}
    for answer in answers:
        if isinstance(answer, dict) and answer.get("exerciseId"):
            submitted[str(answer["exerciseId"])] = str(answer.get("answer") or "").strip().upper()

    rows = []
    for exercise_id, key in answer_keys.items():
        record = ctx.repository.get("exercises", exercise_id) or {}
        rows.append({
            "exerciseId": exercise_id,
            "stem": str(record.get("stem") or ""),
            "options": record.get("options") or [],
            "answer": submitted.get(exercise_id, ""),
            "correctAnswer": key,
            "correct": submitted.get(exercise_id, "") == key,
            "knowledgePointId": knowledge_points.get(exercise_id, ""),
            "explanation": str(record.get("explanation") or ""),
            "supportingQuote": str(record.get("supportingQuote") or "")[:300],
            "sources": record.get("sources") or [],
        })
    payload["perQuestion"] = rows

    return envelope.ok(payload)


def get_plan_diff(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error

    old_plan = arguments.get("oldPlan")
    new_plan = arguments.get("newPlan")
    if not isinstance(old_plan, dict):
        old_plan = parse_json_arg(arguments.get("oldPlanText"), dict)
    if not isinstance(new_plan, dict):
        new_plan = parse_json_arg(arguments.get("newPlanText"), dict)
    if not isinstance(old_plan, dict) or not isinstance(new_plan, dict):
        return envelope.fail(envelope.VALIDATION_ERROR,
                             "oldPlan 与 newPlan 都必须是计划对象（或用 oldPlanText/newPlanText 传文本）")

    diff = modules().plans.build_plan_diff(old_plan, new_plan)
    return envelope.ok(diff, trace_id=envelope.new_trace_id())
