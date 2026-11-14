# -*- coding: utf-8 -*-
"""写操作工具：存计划、更新掌握度、请求重排、按资料出题。

写操作的三条硬约束（缺一条就会在演示里出事）
--------------------------------------------
1. **幂等**：必须带 `idempotencyKey`。模型重试、网关重连都很常见，重复落库会让
   计划版本凭空 +2。
2. **乐观锁**：必须带 `expectedVersion`，与当前版本不一致返回 `CONFLICT`，
   由规划 Agent 重新读一遍再提交——不允许"后写覆盖"。
3. **身份来自 sessionRef**：`userId` 只能由服务端换出来，模型传给什么都会被拒。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from .. import envelope
from ..auth import idempotency_get, idempotency_put, require_actor
from ..business import modules
from ..registry import parse_json_arg

SESSION_REF = {"type": "string", "description": "网关签发的一次性会话引用，用于换取真实 userId"}


def _registry():
    """知识点注册表（懒加载：缺依赖时不影响其它工具）。"""
    from app.services import knowledge_registry
    return knowledge_registry


def _kp_id(repository, raw: str, name: str = "", source: str = "declared") -> str:
    """把任意说法换成 kpId —— **所有写掌握度的入口都必须经过它**。

    以前直接把用户/资料给的名字当 ID，导致同一知识点在画像里出现两条。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return _registry().ensure(repository, text, name=name, source=source)
    except Exception:  # noqa: BLE001 - 注册表异常时退回原值，不让写操作整体失败
        return text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recent_plan(repository, user_id: str):
    plans = [item for item in repository.list("plans")
             if isinstance(item, dict) and item.get("userId") == user_id]
    if not plans:
        return None
    return max(plans, key=lambda item: int(item.get("version") or 0))


_CN_NUMBERS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _count_from_text(text: str):
    """从用户原话里数出题量："出两道""来 3 题""五道"。

    为什么不靠工作流传 count：平台节点里的 count 是固定值，
    而用户说的是自然语言。topic 里就是原话，这里解析最省事也最准。
    """
    if not text:
        return None
    match = re.search(r"(\d+|[一两二三四五六七八九十])\s*(?:道|题|个题)", text)
    if not match:
        return None
    token = match.group(1)
    value = int(token) if token.isdigit() else _CN_NUMBERS.get(token, 0)
    return value or None


def _material_points(repository, user_id: str, kb_id: str) -> list:
    """资料解析时抽出的知识点（documents.knowledgePoints），去重保序。"""
    points, seen = [], set()
    for doc in repository.list("documents"):
        if not isinstance(doc, dict):
            continue
        if doc.get("userId") != user_id:
            continue
        if kb_id and doc.get("kbId") != kb_id:
            continue
        for name in (doc.get("knowledgePoints") or []):
            name = str(name or "").strip()
            if name and name not in seen:
                seen.add(name)
                points.append(name)
    return points


def _name_to_point_id(repository, user_id: str, name: str) -> str:
    """把资料里的知识点名对回 kpId。

    先问注册表（它有别名，能把"二叉树遍历基础"对到题库 ID），
    再退回画像/错题本里的同名记录，最后才用名字本身。
    """
    try:
        resolved = _registry().resolve(repository, name)
    except Exception:  # noqa: BLE001 - 注册表异常时退回旧逻辑
        resolved = None
    if resolved:
        return resolved

    for row in ((repository.get("profiles", user_id) or {}).get("mastery") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("knowledgePointName") or "") == name:
            return str(row.get("knowledgePointId") or name)
    for row in repository.list("wrong_book"):
        if isinstance(row, dict) and row.get("userId") == user_id \
                and str(row.get("knowledgePointName") or "") == name:
            return str(row.get("knowledgePointId") or name)

    # 都没命中：以"资料抽取"的身份登记，保证后续判分/错题能落到同一条
    try:
        return _registry().ensure(repository, name, name=name, source="extracted")
    except Exception:  # noqa: BLE001
        return name


def _topic_matches(name: str, topic: str) -> bool:
    """判断用户原话是否提到了这个知识点。

    只用 `name in topic` 太严：资料抽出的是"二叉树遍历基础"，
    用户说的是"二叉树遍历"，子串判断会失败。这里再加一条：
    知识点的前缀（≥3 字）出现在原话里也算命中。
    """
    if not name or not topic:
        return False
    if name.casefold() in topic.casefold():
        return True
    lowered = topic.casefold()
    return any(name[:size].casefold() in lowered for size in range(3, len(name)))


def _resolve_point(repository, module, user_id: str, kb_id: str, topic: str):
    """决定"出哪个知识点的题"。返回 (kpId, source, candidates, display_name)。

    检索用**显示名**（资料是按文字匹配的），落库用 **kpId**（保证链路一致）。
    """
    candidates = _material_points(repository, user_id, kb_id)

    ordered = []
    if topic:
        ordered = [name for name in candidates if _topic_matches(name, topic)]
        if not ordered:
            # 用户点名了主题（如"出两道 TCP 的题"）但资料里没有对应知识点：
            # 直接说"没有"，并把资料里实际有的候选带出去，让上层问一句
            # "要不要改出二叉树的题"。悄悄换成别的知识点是答非所问。
            return "", "topic_not_in_materials", candidates, ""
    ordered += [name for name in candidates if name not in ordered]

    for name in ordered:
        # 检索按名字（章节/正文里出现的就是它）
        try:
            sources, source_error = module.retrieve(repository, user_id, name, kb_id)
        except Exception:  # noqa: BLE001 - 单个候选失败不应中断
            continue
        if not source_error and sources:
            kp_id = _name_to_point_id(repository, user_id, name)
            source = "auto_from_topic" if (topic and name.casefold() in topic.casefold()) \
                else "auto_from_material"
            return kp_id, source, candidates, name

    return "", "none", candidates, ""


def _first_kb_id(repository, user_id: str) -> str:
    """没传 kbId 时，取该用户的第一个资料库。

    优先用 knowledge_bases（正式记录），退一步用 documents 里出现过的 kbId，
    这样即便早期数据只灌了文档，也不会误判成"没有资料"。
    """
    for kb in repository.list("knowledge_bases"):
        if isinstance(kb, dict) and kb.get("userId") == user_id and kb.get("kbId"):
            return str(kb["kbId"])
    for doc in repository.list("documents"):
        if isinstance(doc, dict) and doc.get("userId") == user_id and doc.get("kbId"):
            return str(doc["kbId"])
    return ""


def _store_plan(ctx, user_id: str, plan: dict, old: dict | None, reason: str,
                evidence_ids: list) -> dict:
    """落库计划 + 版本历史。两个集合必须一起写，否则差异页会指向不存在的版本。

    `old` 必须由调用方在**写入前**取好：写完之后再查"最近版本"拿到的是新计划，
    差异会恒为空——这个 bug 只在"保存后立刻看差异"时才暴露，很难从报错里看出来。
    """
    ctx.repository.save("plans", plan["planId"], plan)
    base = old if isinstance(old, dict) and old.get("planId") == plan["planId"] else {
        "planId": plan["planId"], "version": 0, "tasks": []}
    history = modules().plans.create_plan_history(
        base, plan, reason or "mcp_save_plan", list(evidence_ids or []))
    ctx.repository.save("plan_histories", "%s:v%s" % (plan["planId"], plan.get("version")),
                        history)
    return history


def save_learning_plan(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error
    user_id = actor["userId"]

    plan_in = arguments.get("plan")
    if not isinstance(plan_in, dict):
        plan_in = parse_json_arg(arguments.get("planText"), dict)
    if not isinstance(plan_in, dict):
        return envelope.fail(envelope.VALIDATION_ERROR, "plan 必须是计划对象（或用 planText 传文本）")
    expected = arguments.get("expectedVersion")
    if isinstance(expected, bool) or not isinstance(expected, int):
        return envelope.fail(envelope.VALIDATION_ERROR,
                             "expectedVersion 必须是整数（首次保存传 0）")
    key = str(arguments.get("idempotencyKey") or "").strip()
    if not key:
        return envelope.fail(envelope.VALIDATION_ERROR, "缺少 idempotencyKey")

    cached = idempotency_get(ctx.repository, user_id, key)
    if isinstance(cached, dict) and isinstance(cached.get("response"), dict):
        # 重复提交：返回首次结果，并显式标记，便于前端与评测识别"这是重放"。
        replay = dict(cached["response"])
        replay["details"] = {"replayed": True, "idempotencyKey": key}
        return replay

    current = _recent_plan(ctx.repository, user_id)
    current_version = int((current or {}).get("version") or 0)
    if expected != current_version:
        return envelope.fail(
            envelope.CONFLICT, "计划版本已变化，请重新读取后再提交",
            details={"expectedVersion": expected, "currentVersion": current_version})

    plan_id = str(plan_in.get("planId") or (current or {}).get("planId") or "").strip()
    if not plan_id:
        plan_id = "plan-" + key[:16]

    new_plan = dict(plan_in)
    new_plan.update({
        "planId": plan_id,
        "version": current_version + 1,
        "userId": user_id,
        "generatedAt": plan_in.get("generatedAt") or _now(),
    })

    history = _store_plan(ctx, user_id, new_plan, current,
                          str(arguments.get("reason") or ""),
                          arguments.get("evidenceIds") or [])
    diff = modules().plans.build_plan_diff(current or {"tasks": [], "version": 0}, new_plan)

    response = envelope.ok({
        "planId": plan_id,
        "version": new_plan["version"],
        "plan": new_plan,
        "planDiff": diff,
        "history": history,
    }, trace_id=envelope.new_trace_id())
    idempotency_put(ctx.repository, user_id, key, response)
    return response


def update_mastery(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error
    user_id = actor["userId"]

    updates = arguments.get("updates")
    if not isinstance(updates, list) or not updates:
        # 模型常输出外层包装 {"updates":[...], "assessment":{...}}（一次给两个工具供数），
        # 这里按字段名拆包；纯数组或信封 data 也接受。
        parsed = parse_json_arg(arguments.get("updatesText"), (dict, list))
        if isinstance(parsed, dict):
            updates = parsed.get("updates")
        elif isinstance(parsed, list):
            updates = parsed
    if not isinstance(updates, list) or not updates:
        return envelope.fail(envelope.VALIDATION_ERROR,
                             "updates 不能为空数组（也可传 updatesText 文本）")

    profile = ctx.repository.get("profiles", user_id)
    if not isinstance(profile, dict):
        profile = {"userId": user_id, "mastery": [], "profileVersion": 0}
    rows = [row for row in (profile.get("mastery") or []) if isinstance(row, dict)]
    index = {row.get("knowledgePointId"): row for row in rows}

    changed = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        point_id = str(item.get("knowledgePointId") or "").strip()
        if not point_id:
            return envelope.fail(envelope.VALIDATION_ERROR,
                                 "updates 每项都必须含 knowledgePointId")
        raw_name = str(item.get("knowledgePointName") or "").strip()
        # 关键：先换成注册表里的 kpId，避免同一知识点被写成两条掌握度
        point_id = _kp_id(ctx.repository, point_id, raw_name, source="declared") or point_id
        try:
            score = float(item.get("assessmentScore"))
        except (TypeError, ValueError):
            return envelope.fail(envelope.VALIDATION_ERROR,
                                 "assessmentScore 必须是 0~100 的数字")
        old = float((index.get(point_id) or {}).get("masteryScore") or 0)
        try:
            new_score = modules().assessment.update_mastery(old, score)
        except ValueError as invalid:
            return envelope.fail(envelope.VALIDATION_ERROR, str(invalid),
                                 details={"knowledgePointId": point_id})

        row = index.get(point_id)
        display_name = raw_name or _registry().name_of(ctx.repository, point_id, point_id)
        if row is None:
            row = {"knowledgePointId": point_id, "knowledgePointName": display_name,
                   "masteryScore": new_score,
                   "source": "assessment", "assessed": True, "attempts": 1}
            rows.append(row)
            index[point_id] = row
        else:
            row["masteryScore"] = new_score
            row["knowledgePointName"] = row.get("knowledgePointName") or display_name
            row["source"] = "assessment"
            # 用户自己勾选的知识点初始 assessed=false（含义是"还没测过"）；
            # 第一次判分后才有资格谈"薄弱"。
            row["assessed"] = True
            row["attempts"] = int(row.get("attempts") or 0) + 1
        changed.append({"knowledgePointId": point_id, "oldScore": old, "newScore": new_score,
                        "assessmentScore": score})

    profile = dict(profile)
    profile["mastery"] = rows
    profile["userId"] = user_id
    profile["profileVersion"] = int(profile.get("profileVersion") or 0) + 1
    profile["updatedAt"] = _now()
    ctx.repository.save("profiles", user_id, profile)

    return envelope.ok({"mastery": changed, "profileVersion": profile["profileVersion"]},
                       trace_id=envelope.new_trace_id())


def request_replan(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error
    user_id = actor["userId"]

    assessment = arguments.get("assessment")
    if not isinstance(assessment, dict):
        assessment = parse_json_arg(arguments.get("assessmentText"), dict)
    # 工作流里上一步的大模型通常输出 {"updates":[...], "assessment":{...}}，
    # 这里自动拆一层，免得模型/编排必须"只输出内层对象"。
    if isinstance(assessment, dict) and isinstance(assessment.get("assessment"), dict):
        assessment = assessment["assessment"]
    if not isinstance(assessment, dict):
        return envelope.fail(envelope.VALIDATION_ERROR,
                             "assessment 必须是评估结果对象（或用 assessmentText 传文本）")

    current = _recent_plan(ctx.repository, user_id)
    if current is None:
        return envelope.fail(envelope.NOT_FOUND, "当前没有可重排的计划",
                             details={"userId": user_id})

    state = {
        "masteryScore": assessment.get("masteryScore"),
        "incompleteTasks": int(assessment.get("incompleteTasks") or 0),
        "repeatedError": bool(assessment.get("repeatedError")),
        "assessmentScore": assessment.get("assessmentScore"),
        "knowledgePointId": assessment.get("knowledgePointId"),
    }
    decision = modules().plans.should_replan(state)
    if not decision.get("needReplan"):
        return envelope.ok({"needReplan": False, "reasons": decision.get("reasons") or [],
                            "changed": False})

    result = modules().plans.replan_learning_path(current, state)
    new_plan = result.get("plan") or current
    changed_tasks = result.get("changedTasks") or []

    if changed_tasks:
        history = _store_plan(ctx, user_id, new_plan, current, "mcp_replan", [])

        diff = modules().plans.build_plan_diff(current, new_plan)
        return envelope.ok({
            "needReplan": True,
            "changed": True,
            "reasons": decision.get("reasons") or [],
            "plan": new_plan,
            "planDiff": diff,
            "changedTasks": changed_tasks,
            "history": history,
        }, trace_id=envelope.new_trace_id())

    # 需要重排、但本轮没有可改的任务（例如练的知识点不在当前计划里）。
    # 如实返回"未改动"，不要为了看起来有产出而凭空造任务。
    return envelope.ok({"needReplan": True, "changed": False,
                        "reasons": decision.get("reasons") or [],
                        "note": "该知识点不在当前计划中，已保留原计划"}) 


def create_grounded_exercise(arguments: dict, ctx) -> dict:
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error
    user_id = actor["userId"]

    topic = str(arguments.get("topic") or arguments.get("query") or "").strip()

    # 题量优先取"用户原话里的数字"（"出两道"），其次才是 count 参数。
    # 平台节点的 count 是固定值，用户说什么都不会变——所以以原话为准。
    try:
        count = max(1, min(int(arguments.get("count") or 3), 5))
    except (TypeError, ValueError):
        count = 3
    spoken = _count_from_text(topic)
    if spoken:
        count = max(1, min(spoken, 5))

    kb_id = str(arguments.get("kbId") or "").strip()
    if not kb_id:
        # 模型通常不会主动传 kbId。不自动解析的话，retrieve 会拿空字符串去查
        # knowledge_bases，直接返回 NOT_FOUND —— 表现为"没有资料"，其实是有资料的。
        kb_id = _first_kb_id(ctx.repository, user_id)

    from ..business import grounded_exercises

    try:
        module = grounded_exercises()
    except Exception as unavailable:  # noqa: BLE001 - 依赖缺失要说清，不要 500
        return envelope.fail(envelope.TOOL_UNAVAILABLE,
                             "出题能力不可用（需要完整后端依赖）：%s" % (unavailable,))

    point = str(arguments.get("knowledgePointId") or "").strip()
    # 平台工具节点会把"必填"参数强制要求非空（schema 缓存没刷新时尤其如此）。
    # 约定这些占位值等价于"未指定"，免得工作流为了绕开校验去编一个知识点。
    if point.casefold() in {"auto", "auto-detect", "auto_from_material", "自动", "自动选择"}:
        point = ""
    point_source = "explicit"
    point_name = ""
    if point:
        # 显式指定也要过注册表：判分/错题才落回同一条掌握度
        try:
            point_name = _registry().name_of(ctx.repository, point, point)
        except Exception:  # noqa: BLE001
            point_name = point
        point = _kp_id(ctx.repository, point, point_name, source="declared") or point
    if not point:
        # 用户常说"根据这份资料出两道题"而不指定知识点。
        # 这时考什么应由**资料**决定，而不是由"谁最弱"决定：
        # 资料里抽出的知识点优先，其次看用户原话里提到了哪个。
        point, point_source, candidates, point_name = _resolve_point(
            ctx.repository, module, user_id, kb_id, topic)
        if not point:
            if point_source == "topic_not_in_materials":
                message = ("资料里没有与「%s」相关的知识点；现有：%s。"
                           "需要我基于其中一个出题吗？"
                           % (topic[:20], "、".join(candidates[:5]) or "（无）"))
                reason = "TOPIC_NOT_IN_MATERIALS"
            else:
                message = ("这份资料里没有可出题的知识点：请指定知识点，"
                           "或换一份含知识点的讲义")
                reason = "NO_POINT_IN_MATERIALS"
            return envelope.fail(envelope.INSUFFICIENT_MATERIALS, message,
                                 details={"reason": reason,
                                          "candidates": candidates[:10]})

    # 检索按"名字"匹配（资料里出现的是文字）；没有显示名时退回 kpId
    retrieval_term = point_name or point

    try:
        sources, source_error = module.retrieve(ctx.repository, user_id, retrieval_term, kb_id)
    except ModuleNotFoundError as missing:
        # 出题链路里的题库读取会顺带导入 app.api（依赖 Flask）。
        # 与其抛 INTERNAL_ERROR，不如明确告诉运维该装什么。
        return envelope.fail(envelope.TOOL_UNAVAILABLE,
                             "出题链路缺少依赖：%s。请在服务器执行 "
                             "pip3 install flask flask-cors openai" % (missing,))
    if source_error or not sources:
        # 没有资料就不出题——这是"有据可依"的底线，宁可让诊断去补资料。
        return envelope.fail(envelope.INSUFFICIENT_MATERIALS,
                             "该知识点没有可用资料，拒绝无依据出题",
                             details={"knowledgePointId": point, "reason": source_error or ""})

    # 生成前的两道自检：把"没有模型 Key""知识点名不合法"从"生成被拒"里拆出来。
    # 这三种都叫 INSUFFICIENT_MATERIALS 时，排查只能靠猜。
    try:
        from app.agent import exercise_gen as _gen
        from app.agent import knowledge as _knowledge
    except Exception:  # noqa: BLE001 - 缺依赖交给下面的 generate 报
        _gen = _knowledge = None

    if _knowledge is not None and not _knowledge.valid_knowledge_term(point):
        return envelope.fail(envelope.VALIDATION_ERROR,
                             "知识点名不合法，无法据此出题：%s" % point,
                             details={"knowledgePointId": point, "pointSource": point_source,
                                      "requestedCount": count,
                                      "reason": "INVALID_TERM"})
    if _gen is not None and not _gen.generation_available():
        # 即便生成能力缺失，也把"选点与题量"带出来：调用方与自检都要能看见解析结果。
        return envelope.fail(envelope.TOOL_UNAVAILABLE,
                             "服务器未配置出题模型（DASHSCOPE_API_KEY 缺失或无效）",
                             details={"knowledgePointId": point, "pointSource": point_source,
                                      "requestedCount": count,
                                      "reason": "LLM_UNAVAILABLE"})

    try:
        exercises = module.generate(point, count, sources)
    except ModuleNotFoundError as missing:
        return envelope.fail(envelope.TOOL_UNAVAILABLE,
                             "出题模型依赖缺失：%s。请安装 openai 并配置 DASHSCOPE_API_KEY" % (missing,))
    if not exercises:
        reject = getattr(module, "LAST_REJECT", None) or {}
        return envelope.fail(envelope.INSUFFICIENT_MATERIALS,
                             "资料不足以生成可用题目",
                             details={"knowledgePointId": point, "pointSource": point_source,
                                      "requestedCount": count,
                                      "reason": "GENERATION_REJECTED",
                                      # 具体哪一道校验没过：出题排障全靠它
                                      "rejectStep": reject.get("step") or "",
                                      "rejectDetail": reject.get("detail") or ""})

    public = []
    for item in exercises:
        if not isinstance(item, dict):
            continue
        exercise_id = str(item.get("exerciseId") or "")
        if not exercise_id:
            continue

        # 关键：把生成的题目连同标准答案落库。
        # 不落库的话，判分时按 exerciseId 查不到 answerKey，只能返回 NOT_FOUND——
        # "出题"与"判分"之间的闭环就断了（实测踩到过）。
        record = {
            "exerciseId": exercise_id,
            "userId": user_id,
            "knowledgePointId": item.get("knowledgePointId") or point,
            "knowledgePointName": item.get("knowledgePointName") or point,
            "stem": item.get("stem") or "",
            "options": item.get("options") or [],
            "answerKey": str(item.get("answer") or item.get("answerKey") or "").strip().upper(),
            "explanation": item.get("explanation") or "",
            "difficulty": item.get("difficulty") or "medium",
            "source": item.get("source") or "materials",
            "sources": item.get("sources") or [],
            "grounding": item.get("grounding"),
            "createdAt": _now(),
        }
        if record["answerKey"]:
            ctx.repository.save("exercises", exercise_id, record)

        public.append({key: record.get(key) for key in
                       ("exerciseId", "stem", "options", "difficulty", "knowledgePointId",
                        "knowledgePointName", "source")})

    return envelope.ok({"knowledgePointId": point,
                        "pointSource": point_source,
                        "exercises": public,
                        "persisted": sum(1 for item in public),
                        "sources": module.public_sources(sources)
                        if hasattr(module, "public_sources") else []},
                       trace_id=envelope.new_trace_id())


def record_wrong_question(arguments: dict, ctx) -> dict:
    """记一道错题，并让它**真的影响学情**。

    为什么一个工具里同时做两件事
    ----------------------------
    错题落库不更新掌握度，学情就永远停在种子值；而拆成两个工具意味着工作流要多
    一个节点、多一次结构化参数传递——我们在 `fit_plan_to_budget` 上已经为此付出过
    代价。这里用"记录 + 减分"一次完成，且参数全是字符串，工作流好接。

    减分口径与 `update_mastery` 完全一致（复用同一个规则函数），
    错题默认按 `assessmentScore=0` 计算，可由调用方覆盖。
    """
    actor, error = require_actor(ctx.repository, arguments)
    if error:
        return error
    user_id = actor["userId"]

    point_id = str(arguments.get("knowledgePointId") or "").strip()
    stem = str(arguments.get("stem") or "").strip()
    if not point_id:
        return envelope.fail(envelope.VALIDATION_ERROR, "缺少 knowledgePointId")
    if len(stem) < 4:
        return envelope.fail(envelope.VALIDATION_ERROR,
                             "缺少题干（stem），至少要能看出是哪道题")

    point_name = str(arguments.get("knowledgePointName") or "").strip() or point_id
    # 统一走注册表：错题本与画像必须用同一个 kpId，否则提示里会出现两条同名知识点
    point_id = _kp_id(ctx.repository, point_id, point_name, source="declared") or point_id
    point_name = _registry().name_of(ctx.repository, point_id, point_name)
    reason = str(arguments.get("reason") or "").strip()
    source_type = str(arguments.get("sourceType") or "").strip() or "photo"

    raw_score = arguments.get("assessmentScore")
    try:
        score = float(raw_score) if raw_score not in (None, "") else 0.0
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(100.0, score))

    # 题号：调用方给了就用，没给就按内容哈希生成，保证同题重复录入不会刷屏。
    exercise_id = str(arguments.get("exerciseId") or "").strip()
    if not exercise_id:
        digest = hashlib.sha1((stem + point_id).encode("utf-8")).hexdigest()[:10]
        exercise_id = "wrong-" + digest

    key = "%s:%s" % (user_id, exercise_id)
    existing = ctx.repository.get("wrong_book", key) or {}
    record = {
        "userId": user_id,
        "exerciseId": exercise_id,
        "courseName": existing.get("courseName") or "",
        "courseConfirmed": bool(existing.get("courseConfirmed", False)),
        "knowledgePointId": point_id,
        "knowledgePointName": point_name,
        "difficulty": existing.get("difficulty") or "medium",
        "stem": stem[:2000],
        "options": existing.get("options") or [],
        "source": source_type,
        "sources": existing.get("sources") or [],
        "answerKey": existing.get("answerKey") or "",
        "explanation": reason or existing.get("explanation") or "",
        "correctCount": int(existing.get("correctCount") or 0),
        # 同一道题重复录入只累加次数，不重复建记录
        "wrongCount": int(existing.get("wrongCount") or 0) + 1,
        "addedAutomatically": True,
        "addedAt": existing.get("addedAt") or _now(),
        "lastWrongAt": _now(),
    }
    ctx.repository.save("wrong_book", key, record)

    # ---- 复用的减分规则：与 update_mastery 同一个函数，避免两套口径
    profile = ctx.repository.get("profiles", user_id)
    if not isinstance(profile, dict):
        profile = {"userId": user_id, "mastery": [], "profileVersion": 0}
    rows = [row for row in (profile.get("mastery") or []) if isinstance(row, dict)]
    index = {row.get("knowledgePointId"): row for row in rows}
    row = index.get(point_id)
    old_score = float((row or {}).get("masteryScore") or 0)

    try:
        new_score = modules().assessment.update_mastery(old_score, score)
    except ValueError as invalid:
        return envelope.fail(envelope.VALIDATION_ERROR, str(invalid),
                             details={"knowledgePointId": point_id})

    if row is None:
        row = {"knowledgePointId": point_id, "knowledgePointName": point_name,
               "masteryScore": new_score, "source": "wrong_book",
               "assessed": True, "attempts": 1}
        rows.append(row)
    else:
        row["masteryScore"] = new_score
        row["knowledgePointName"] = row.get("knowledgePointName") or point_name
        row["source"] = "wrong_book"
        # 有错题就说明测过了，诊断才有资格把它算进"薄弱"
        row["assessed"] = True
        row["attempts"] = int(row.get("attempts") or 0) + 1

    profile = dict(profile)
    profile["mastery"] = rows
    profile["userId"] = user_id
    profile["profileVersion"] = int(profile.get("profileVersion") or 0) + 1
    profile["updatedAt"] = _now()
    ctx.repository.save("profiles", user_id, profile)

    return envelope.ok({
        "wrongBookId": key,
        "exerciseId": exercise_id,
        "knowledgePointId": point_id,
        "knowledgePointName": point_name,
        "wrongCount": record["wrongCount"],
        "assessmentScore": score,
        "oldScore": old_score,
        "newScore": new_score,
        "profileVersion": profile["profileVersion"],
    }, trace_id=envelope.new_trace_id())
