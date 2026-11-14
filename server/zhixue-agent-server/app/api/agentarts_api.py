# -*- coding: utf-8 -*-
"""`/api/v2/agent/*` —— AgentArts 网关。

这一层存在的唯一理由：**云凭据不能下发客户端**。
App / Web 只认业务会话令牌（`Authorization: Bearer`），IAM 密码与 Token 全部留在服务端。

四个端点
--------
* `POST /chat`     主链路，SSE 流式。先取 Token 再开流，保证"连不上"是一句干净的错误，
                   而不是流到一半断掉。
* `GET  /runs/<id>` 事件回放。SSE 只在连接存活期有效；页面刷新、App 轮询失败后靠它恢复
                   时间线，评测脚本也靠它离线分析。
* `GET  /runtime`   配置与 Token 缓存状态（**不含任何密钥**），Demo 用它显示"云端/降级"徽标。
* `POST /feedback`  把"有用/无用"沉淀成评测样本——评测集不该靠事后编造。

降级口径
--------
未配置 / 未开启 / 平台不可达，一律返回明确错误码 + `fallback: false`。
**绝不**在云端失败时偷偷返回本地结果还标成云端。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_from_directory, stream_with_context

from app.agentarts import (
    AgentArtsClient,
    AgentArtsConfig,
    AgentArtsError,
    IamTokenProvider,
    MODE_CLOUD,
    normalize,
)
from app.agentarts import session as store
from app.api.identity import can_read_user, current_user_id, resolve_user_id

agentarts_api = Blueprint("agentarts", __name__)

#: 单文件 Demo 页面（web/index.html）。不引入构建链，网关直接托管。
DEMO_DIR = Path(__file__).resolve().parents[2] / "web"

_repository = None
_config: AgentArtsConfig | None = None
_client: AgentArtsClient | None = None
_tokens: IamTokenProvider | None = None

MAX_MESSAGE_CHARS = 4000


def configure_agentarts(repository, *, config: AgentArtsConfig | None = None,
                        client: AgentArtsClient | None = None,
                        tokens: IamTokenProvider | None = None) -> None:
    """由 `create_app()` 调用；测试里可注入假 config / client 完全离线。"""
    global _repository, _config, _client, _tokens
    _repository = repository
    _config = config or AgentArtsConfig.from_env()
    _tokens = tokens or IamTokenProvider(_config)
    _client = client or AgentArtsClient(_config, _tokens)


def _cfg() -> AgentArtsConfig:
    global _config
    if _config is None:
        _config = AgentArtsConfig.from_env()
    return _config


def _cli() -> AgentArtsClient:
    global _client, _tokens
    if _client is None:
        _tokens = _tokens or IamTokenProvider(_cfg())
        _client = AgentArtsClient(_cfg(), _tokens)
    return _client


# ------------------------------------------------------------------ 工具
def _payload() -> dict:
    """宽松解析请求体：兼容带 charset 的 Content-Type（与聊天层同一处理）。"""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    raw = request.get_data() or b""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error(status: int, code: str, message: str, *, details: dict | None = None,
           fallback: bool = False):
    body = {
        "errorCode": code,
        "message": message,
        "details": details or {},
        "fallback": fallback,
    }
    return jsonify(body), status


def _sse(event: dict, index: int) -> str:
    payload = json.dumps(event, ensure_ascii=False)
    return "id: %d\nevent: %s\ndata: %s\n\n" % (index, event.get("type", "message"), payload)


# ------------------------------------------------------------------ 主链路
@agentarts_api.post("/api/v2/agent/chat")
def agent_chat():
    config = _cfg()
    if config.mode != MODE_CLOUD:
        # 未开启或强制本地：这是配置状态，不是故障，但仍要明确告诉调用方。
        return _error(503, "AGENTARTS_DISABLED", config.blocking_reason(),
                      details={"mode": config.mode, "missing": config.missing()})

    data = _payload()
    message = str(data.get("message") or data.get("query") or "").strip()
    if not message:
        return _error(400, "BAD_REQUEST", "message 不能为空")
    if len(message) > MAX_MESSAGE_CHARS:
        return _error(400, "BAD_REQUEST", "message 超过 %d 字" % MAX_MESSAGE_CHARS)

    missing = config.missing()
    if missing:
        return _error(503, "AGENTARTS_CONFIG_INCOMPLETE",
                      "AgentArts 配置不完整：%s" % "、".join(missing),
                      details={"missing": missing})

    user_id = resolve_user_id(data.get("userId"))
    chat_session_id = str(data.get("sessionId") or "").strip()[:128] or ("chat-" + store.new_run_id())
    session = store.bind_session(_repository, user_id=user_id, chat_session_id=chat_session_id,
                                 agent_id=config.agent_id)

    client = _cli()
    try:
        client.ensure_ready()
    except AgentArtsError as error:
        # 连不上就不开流：502 + fallback:false，前端据此显示"云端不可用"，而不是空转。
        return _error(502, "AGENTARTS_UNAVAILABLE", str(error),
                      details={"status": error.status, "hint": error.hint}, fallback=False)

    run = store.start_run(_repository, conversation_id=session["conversationId"], user_id=user_id,
                          chat_session_id=chat_session_id, agent_id=config.agent_id)
    run_id = run["runId"]

    stream = client.run_agent(
        session["conversationId"], message, {"sessionRef": session["sessionRef"]})

    def generate():
        index = 0
        collected: list[dict] = []
        reply_parts: list[str] = []
        tools: list[str] = []
        finished = False
        failure: dict | None = None

        yield _sse({
            "type": "ready",
            "runId": run_id,
            "conversationId": session["conversationId"],
            "agentId": config.agent_id,
            "agentVersion": config.agent_version,
            "mode": config.mode,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }, index)
        index += 1

        try:
            for platform_event in stream:
                event = normalize(platform_event)
                collected.append(event)
                if event.get("type") == "agent_message":
                    delta = event.get("delta") or ""
                    # 长文本是"整段回复"（End 节点 summary），直接替换，
                    # 否则同一段会被 message + summary 各来一次、拼成三份。
                    if len(delta) > 60:
                        reply_parts[:] = [delta]
                    else:
                        reply_parts.append(delta)
                if event.get("type") == "run_summary" and not "".join(reply_parts).strip():
                    # 有些版本只在 summary_response 里给全文，没有逐段 message。
                    # 不回填的话，前端会收到一串事件但"最终回复为空"。
                    reply_parts.append(event.get("text") or "")
                if event.get("type") == "run_failed":
                    # 平台报错后仍会补 done，必须在这里记住失败，不能以"最后事件是 done"判成功。
                    failure = event
                if event.get("type") == "tool_started" and event.get("name") not in tools:
                    tools.append(event["name"])
                if event.get("type") == "run_finished":
                    finished = True
                yield _sse(event, index)
                index += 1
        except AgentArtsError as error:
            collected.append({"type": "run_failed", "errorCode": "AGENTARTS_STREAM_ERROR",
                              "message": str(error), "hint": error.hint})
            # 断流前已经收到的事件必须一起落库：否则回放只剩一条 run_failed，
            # "失败前它其实说到哪一步了"这个最有价值的调试信息就丢了。
            store.append_events(_repository, run_id, collected)
            store.finish_run(_repository, run_id, status="failed", reply="".join(reply_parts),
                             tools=tools, error=str(error))
            yield _sse(collected[-1], index)
            return

        if not finished:
            # 平台提前断流（例如网关超时）。显式补一个结束事件，避免前端一直转圈。
            tail = {"type": "run_finished", "incomplete": True}
            collected.append(tail)
            yield _sse(tail, index)
            index += 1

        # 兜底：若整段没有逐字正文，就取最后一个带 output 的事件（workflow_end / end）。
        # 有些版本只给"工作流最终产出"，不给流式 message，不兜底就只剩空回复。
        # 去重：End 节点常同时给 message(summary) 与 workflow_end(output)，
        # 两条都拼会出现"回复重复两遍"。
        existing = "".join(reply_parts).strip()
        if not existing:
            for item in reversed(collected):
                if item.get("output"):
                    reply_parts.append(str(item["output"]))
                    break
        else:
            for item in reversed(collected):
                candidate = str(item.get("output") or "").strip()
                if candidate and candidate not in existing:
                    reply_parts[:] = [candidate]
                    break

        # 平台"什么都没说"时的本地兜底：控制器对闲聊类输入可能既不转交也不作答
        # （实测 task_start → task_end，无 message）。这时给一句能力引导，
        # 并明确标注 fallback，不冒充模型回答。
        if not "".join(reply_parts).strip() and failure is None:
            hint = ("我是知学 Mate 学习助手，可以帮你：① 看哪里薄弱；② 排学习计划；"
                    "③ 按资料出题；④ 解读练习结果。想从哪一件开始？")
            fallback_event = {"type": "agent_message", "delta": hint, "fallback": "local"}
            collected.append(fallback_event)
            yield _sse(fallback_event, index)
            index += 1
            reply_parts.append(hint)

        store.append_events(_repository, run_id, collected)
        if failure is not None:
            store.finish_run(_repository, run_id, status="failed",
                             reply="".join(reply_parts), tools=tools,
                             error=str(failure.get("message") or "平台执行失败"))
        else:
            store.finish_run(_repository, run_id, status="completed",
                             reply="".join(reply_parts), tools=tools)
        store.touch_session(_repository, session["conversationId"])

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ------------------------------------------------------------------ 回放
@agentarts_api.get("/api/v2/agent/runs/<run_id>")
def get_run(run_id: str):
    run = store.get_run(_repository, run_id)
    if not isinstance(run, dict):
        return _error(404, "NOT_FOUND", "run 不存在", details={"runId": run_id})
    # 与 /api/v1/traces 一致：不属于当前身份的运行一律 404，不泄漏存在性。
    if not can_read_user(str(run.get("userId") or "")):
        return _error(404, "NOT_FOUND", "run 不存在", details={"runId": run_id})

    try:
        after = max(0, int(request.args.get("after") or 0))
    except ValueError:
        after = 0

    events = store.events_since(_repository, run_id, after)
    return jsonify({
        "runId": run_id,
        "conversationId": run.get("conversationId"),
        "status": run.get("status"),
        "startedAt": run.get("startedAt"),
        "finishedAt": run.get("finishedAt"),
        "reply": run.get("reply"),
        "tools": run.get("tools") or [],
        "truncated": bool(run.get("truncated")),
        "total": len(run.get("events") or []),
        "after": after,
        "events": events,
    })


# ------------------------------------------------------------------ 依据链
#: 掌握度换算规则的公开说明。界面要能回答"这个数字怎么来的"，规则必须可读。
MASTERY_RULES = [
    {"id": "R1", "rule": "评估满分（100）→ +29"},
    {"id": "R2", "rule": "评估 <60 → -4（含错题默认 0 分）"},
    {"id": "R3", "rule": "60 ≤ 评估 < 80 → +8"},
    {"id": "R4", "rule": "80 ≤ 评估 < 100 → +16"},
    {"id": "R5", "rule": "结果截断在 0~100，不会越界"},
]


_REGISTRY_READY = {"done": False}


def _ensure_registry_bootstrapped(registry_module) -> None:
    """进程内只做一次：题库 → 知识点注册表。

    不做的话，第一次解析时注册表是空的，历史 ID（binary-tree-traversal）
    会被当成新知识点重建。
    """
    if _REGISTRY_READY["done"]:
        return
    try:
        registry_module.bootstrap(_repository)
        _REGISTRY_READY["done"] = True
    except Exception:  # noqa: BLE001 - 初始化失败不应阻断请求
        pass


@agentarts_api.get("/api/v2/agent/profile")
def profile_evidence():
    """学情 + 错题明细，供前端"点开看依据"。

    只读、脱敏：不带手机号等账号信息，也不带任何密钥。
    """
    user_id = current_user_id() or resolve_user_id(None)
    profile = _repository.get("profiles", user_id) if _repository else None
    profile = profile if isinstance(profile, dict) else {}

    from app.services import knowledge_registry
    _ensure_registry_bootstrapped(knowledge_registry)

    mastery = [{
        "knowledgePointId": row.get("knowledgePointId"),
        # 展示名以注册表为准：同一个 kpId 在任何页面都应显示同一个名字
        "knowledgePointName": knowledge_registry.name_of(
            _repository, str(row.get("knowledgePointId") or ""),
            str(row.get("knowledgePointName") or row.get("knowledgePointId") or "")),
        "masteryScore": row.get("masteryScore"),
        # source=declared 表示用户自己勾选的、还没测过；assessment/wrong_book 才是真实行为
        "source": row.get("source") or "unknown",
        "assessed": bool(row.get("assessed")),
        "attempts": int(row.get("attempts") or 0),
    } for row in (profile.get("mastery") or []) if isinstance(row, dict)]

    wrong = [{
        "exerciseId": row.get("exerciseId"),
        "knowledgePointId": row.get("knowledgePointId"),
        "stem": str(row.get("stem") or "")[:120],
        "wrongCount": int(row.get("wrongCount") or 0),
        "lastWrongAt": row.get("lastWrongAt") or row.get("addedAt"),
        "reason": str(row.get("explanation") or "")[:120],
    } for row in (_repository.list("wrong_book") if _repository else [])
        if isinstance(row, dict) and row.get("userId") == user_id]

    return jsonify({
        "userId": user_id,
        "profileVersion": profile.get("profileVersion"),
        "updatedAt": profile.get("updatedAt"),
        "mastery": mastery,
        "wrongBook": wrong,
        "rules": MASTERY_RULES,
    })


@agentarts_api.post("/api/v2/agent/targets")
def set_targets():
    """用户自己声明课程与知识点（冷启动）。

    这是整个学情的数据起点：用户勾选后写入 `source="declared"`、`assessed=false`、
    掌握度 0——注意 0 的含义是"**还没测过**"，不是"薄弱"。
    第一次判分后 `update_mastery` 会把它改成 assessed=true，之后才谈得上强弱。
    """
    body = request.get_json(silent=True) or {}
    course = str(body.get("courseName") or "").strip()
    goal = str(body.get("goal") or "").strip()
    exam_date = str(body.get("examDate") or "").strip()
    points = body.get("knowledgePoints")

    if not isinstance(points, list) or not points:
        return _error(400, "VALIDATION_ERROR", "knowledgePoints 不能为空")

    user_id = current_user_id() or resolve_user_id(None)
    from app.services import knowledge_registry

    _ensure_registry_bootstrapped(knowledge_registry)

    rows = []
    for item in points[:20]:
        if isinstance(item, str):
            item = {"knowledgePointId": item, "knowledgePointName": item}
        if not isinstance(item, dict):
            continue
        point_id = str(item.get("knowledgePointId") or "").strip()
        if not point_id:
            continue
        display = str(item.get("knowledgePointName") or point_id).strip()
        # 统一走注册表：用户填的名字可能和题库/资料里的说法不同，必须归一到同一个 kpId
        kp_id = knowledge_registry.ensure(_repository, point_id, name=display,
                                          source="declared", course=course)
        rows.append({
            "knowledgePointId": kp_id,
            "knowledgePointName": knowledge_registry.name_of(_repository, kp_id, display),
            "masteryScore": 0,
            "source": "declared",
            "assessed": False,
            "attempts": 0,
        })
    if not rows:
        return _error(400, "VALIDATION_ERROR", "没有有效的知识点")

    profile = _repository.get("profiles", user_id) if _repository else None
    profile = dict(profile) if isinstance(profile, dict) else {"userId": user_id}
    profile["mastery"] = rows
    profile["userId"] = user_id
    profile["courseName"] = course or profile.get("courseName") or ""
    profile["goal"] = goal or profile.get("goal") or ""
    profile["examDate"] = exam_date or profile.get("examDate") or ""
    profile["profileVersion"] = int(profile.get("profileVersion") or 0) + 1
    profile["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _repository.save("profiles", user_id, profile)

    return jsonify({"ok": True, "profileVersion": profile["profileVersion"],
                    "knowledgePointCount": len(rows),
                    "nextStep": "先做一组短测，掌握度才会有真实值"})


@agentarts_api.get("/api/v2/agent/catalog")
def catalog_list():
    """课程 + 知识点目录（用户可维护）。"""
    user_id = current_user_id() or resolve_user_id(None)
    from app.services import catalog, knowledge_registry
    _ensure_registry_bootstrapped(knowledge_registry)
    course = str(request.args.get("course") or "").strip()
    return jsonify({"courses": catalog.list_courses(_repository, user_id),
                    "points": catalog.list_points(_repository, user_id, course),
                    # 未归类条目：题库初始化时没有 course，提示用户归入某门课
                    "unassigned": catalog.unassigned_points(_repository)})


@agentarts_api.post("/api/v2/agent/courses")
def course_save():
    body = request.get_json(silent=True) or {}
    user_id = current_user_id() or resolve_user_id(None)
    from app.services import catalog
    try:
        saved = catalog.save_course(_repository, user_id,
                                    body.get("courseName"),
                                    body.get("examDate") or "",
                                    old_name=str(body.get("oldName") or ""))
    except ValueError as error:
        return _error(400, "VALIDATION_ERROR", str(error))
    return jsonify({"ok": True, "course": saved,
                    "courses": catalog.list_courses(_repository, user_id)})


@agentarts_api.delete("/api/v2/agent/courses/<path:course_name>")
def course_delete(course_name: str):
    user_id = current_user_id() or resolve_user_id(None)
    from app.services import catalog
    ok = catalog.delete_course(_repository, user_id, course_name)
    return jsonify({"ok": ok}), (200 if ok else 404)


@agentarts_api.post("/api/v2/agent/points")
def points_add():
    """批量创建知识点并加入学习范围（一行一个）。已存在的会自动复用。"""
    body = request.get_json(silent=True) or {}
    user_id = current_user_id() or resolve_user_id(None)
    names = body.get("names")
    if isinstance(names, str):
        names = [line for line in names.replace(",", "\n").replace("，", "\n").splitlines()]
    if not isinstance(names, list) or not names:
        return _error(400, "VALIDATION_ERROR", "names 不能为空")
    from app.services import catalog, knowledge_registry
    _ensure_registry_bootstrapped(knowledge_registry)
    try:
        result = catalog.upsert_points(_repository, user_id,
                                       body.get("courseName") or "", names)
    except ValueError as error:
        return _error(400, "VALIDATION_ERROR", str(error))
    return jsonify({"ok": True, **result,
                    "points": catalog.list_points(_repository, user_id,
                                                  body.get("courseName") or "")})


@agentarts_api.patch("/api/v2/agent/points/<kp_id>")
def points_rename(kp_id: str):
    body = request.get_json(silent=True) or {}
    user_id = current_user_id() or resolve_user_id(None)
    from app.services import catalog
    ok = False
    if body.get("name"):
        ok = catalog.rename_point(_repository, user_id, kp_id, body.get("name"))
    if body.get("courseName"):
        # 把未归类的知识点挂到某门课下
        ok = catalog.assign_course(_repository, user_id, kp_id, body.get("courseName")) or ok
    return jsonify({"ok": ok}), (200 if ok else 404)


@agentarts_api.delete("/api/v2/agent/points/<kp_id>")
def points_remove(kp_id: str):
    user_id = current_user_id() or resolve_user_id(None)
    from app.services import catalog
    ok = catalog.remove_point(_repository, user_id, kp_id)
    return jsonify({"ok": ok}), (200 if ok else 404)


@agentarts_api.post("/api/v2/agent/schedule")
def import_schedule():
    """导入课程表：截图识别或直接给课程列表，落库到 `courses`。"""
    body = request.get_json(silent=True) or {}
    user_id = current_user_id() or resolve_user_id(None)

    from app.services import study_import

    courses = body.get("courses")
    if isinstance(courses, list) and courses:
        normalized = [{"courseName": str(item.get("courseName") or item).strip(),
                       "examDate": str(item.get("examDate") or "").strip() if isinstance(item, dict) else "",
                       "priority": str(item.get("priority") or "中").strip() if isinstance(item, dict) else "中"}
                      for item in courses]
    else:
        image = body.get("imageBase64") or body.get("image") or ""
        result = study_import.recognize_schedule(image)
        if not result.get("ok"):
            return jsonify({"errorCode": result.get("error_code"),
                            "message": result.get("message"),
                            "fallback": False}), 400
        normalized = result["courses"]

    saved = study_import.save_courses(_repository, user_id, normalized)
    return jsonify({"ok": True, "saved": saved,
                    "courses": study_import.courses_of(_repository, user_id)})


@agentarts_api.post("/api/v2/agent/deadlines")
def import_deadlines():
    """导入作业/考试 DDL。文本解析是确定性的，不调模型（避免编日期）。"""
    body = request.get_json(silent=True) or {}
    user_id = current_user_id() or resolve_user_id(None)

    from app.services import study_import

    items = body.get("items")
    skipped: list[str] = []
    if not (isinstance(items, list) and items):
        parsed = study_import.parse_deadlines(body.get("text") or "")
        items, skipped = parsed["items"], parsed["skipped"]
    if not items:
        return jsonify({"errorCode": "VALIDATION_ERROR",
                        "message": "没有解析出带日期的条目。每行一条，例如：数据结构 作业3 2026-12-01",
                        "skipped": skipped}), 400

    saved = study_import.save_deadlines(_repository, user_id, items)
    return jsonify({"ok": True, "saved": saved, "skipped": skipped,
                    "deadlines": study_import.upcoming(_repository, user_id)})


@agentarts_api.get("/api/v2/agent/study")
def study_overview():
    """课程与 DDL 概览，供页面显示"已导入什么"。"""
    user_id = current_user_id() or resolve_user_id(None)
    from app.services import study_import
    return jsonify({"courses": study_import.courses_of(_repository, user_id),
                    "deadlines": study_import.upcoming(_repository, user_id)})


@agentarts_api.get("/api/v2/agent/wrongbook")
def wrongbook_list():
    """错题本列表（倒序，最近错的在前）。"""
    user_id = current_user_id() or resolve_user_id(None)
    from app.services import wrongbook
    return jsonify({"items": wrongbook.listing(_repository, user_id),
                    "total": wrongbook.count(_repository, user_id)})


@agentarts_api.post("/api/v2/agent/wrongbook")
def wrongbook_add():
    """手动加入错题本（拍题确认做错、或用户自己说"记一下"）。

    只记录、不动掌握度：掌握度的调整走 `record_wrong_question` 或判分，
    这里再加一次就会重复扣分。
    """
    body = request.get_json(silent=True) or {}
    user_id = current_user_id() or resolve_user_id(None)
    from app.services import wrongbook

    stem = str(body.get("stem") or "").strip()
    if len(stem) < 4:
        return _error(400, "VALIDATION_ERROR", "缺少题干（stem）")
    row = wrongbook.record(_repository, user_id, {
        "exerciseId": body.get("exerciseId"),
        "stem": stem,
        "knowledgePointId": body.get("knowledgePointId"),
        "knowledgePointName": body.get("knowledgePointName"),
        "answer": body.get("answer"),
        "correctAnswer": body.get("correctAnswer"),
        "reason": body.get("reason"),
        "sources": body.get("sources") or [],
        "sourceType": body.get("sourceType") or "manual",
    })
    return jsonify({"ok": True, "exerciseId": row["exerciseId"],
                    "wrongCount": row["wrongCount"],
                    "total": wrongbook.count(_repository, user_id)})


@agentarts_api.delete("/api/v2/agent/wrongbook/<exercise_id>")
def wrongbook_delete(exercise_id: str):
    user_id = current_user_id() or resolve_user_id(None)
    from app.services import wrongbook
    ok = wrongbook.remove(_repository, user_id, exercise_id)
    return jsonify({"ok": ok, "total": wrongbook.count(_repository, user_id)}), (200 if ok else 404)


@agentarts_api.get("/api/v2/agent/knowledge")
def knowledge_evidence():
    """把检索命中的资料片段给前端，用于"引用可点开"。

    浏览器不能直连 MCP（需要 MCP_API_KEY），所以由网关代调、只回传脱敏结果。
    """
    query = str(request.args.get("q") or "").strip()
    try:
        top_k = int(request.args.get("topK") or 5)
    except (TypeError, ValueError):
        top_k = 5

    from app.services import knowledge_proxy

    result = knowledge_proxy.search(query, top_k)
    if not result.get("ok"):
        return jsonify({"errorCode": result.get("error"),
                        "message": result.get("message") or "检索不可用",
                        "hits": []}), 200  # 200 + 空命中：前端显示"无依据"而不是报错
    return jsonify({"query": result.get("query"), "hits": result.get("hits") or [],
                    "retrievalMode": result.get("retrievalMode")})


# ------------------------------------------------------------------ 练习闭环
@agentarts_api.get("/api/v2/agent/exercises")
def recent_exercises():
    """最近生成的题目（不含答案键），给页面渲染成可点的练习卡。"""
    user_id = current_user_id() or resolve_user_id(None)
    try:
        limit = max(1, min(int(request.args.get("limit") or 6), 20))
    except (TypeError, ValueError):
        limit = 6

    rows = [row for row in (_repository.list("exercises") if _repository else [])
            if isinstance(row, dict) and row.get("userId") == user_id]
    rows.sort(key=lambda row: str(row.get("createdAt") or ""), reverse=True)

    items = [{
        "exerciseId": row.get("exerciseId"),
        "stem": row.get("stem"),
        "options": row.get("options") or [],
        "difficulty": row.get("difficulty"),
        "knowledgePointId": row.get("knowledgePointId"),
        "knowledgePointName": row.get("knowledgePointName"),
        "source": row.get("source"),
        "createdAt": row.get("createdAt"),
        "sources": row.get("sources") or [],
    } for row in rows[:limit]]
    return jsonify({"exercises": items, "total": len(rows)})


def _platform_review(prompt: str, session_key: str, result_text: str = "") -> dict:
    """把一段"已算好的结果"交给 AgentArts 控制器，让评估工作流给出评语。

    分工：**数字由 MCP 工具算，评语由平台生成**。
    这样既满足"效果评估要在 AgentArts 里体现"，又不会让模型有机会改数字。

    `resultText` 走 inputs 传给控制器：子工作流节点把它映到自己的入参，
    大模型就能引用这份成绩单，而不是靠从 query 里猜。
    """
    config = _cfg()
    if config.missing():
        return {"ok": False, "errorCode": "AGENTARTS_CONFIG_INCOMPLETE",
                "message": "云端未配置，跳过平台评估"}
    try:
        user_id = resolve_user_id(None)
        # 每次评估用**新会话**：复用同一个 conversationId 时，一旦平台把那次会话
        # 判成异常，后续每轮都会静默结束（实测出现过连续 EMPTY_REPLY）。
        import uuid as _uuid
        fresh_key = "%s-%s" % (session_key[:40], _uuid.uuid4().hex[:6])
        session = store.bind_session(_repository, user_id=user_id,
                                     chat_session_id=fresh_key[:128],
                                     agent_id=config.agent_id)
        client = _cli()
        client.ensure_ready()
        inputs = {"sessionRef": session["sessionRef"]}
        if result_text:
            inputs["resultText"] = result_text[:1500]

        texts: list[str] = []
        failure = None
        seen: list[str] = []
        for raw in client.run_agent(session["conversationId"], prompt, inputs):
            # run_agent 给的是平台原始事件（只有 event 字段），必须先归一化，
            # 否则 type 永远是空——这里漏调过一次，表现为"事件全是 ?"。
            event = normalize(raw) if isinstance(raw, dict) else {"type": "raw"}
            kind = str(event.get("type") or "")
            seen.append(kind or "?")
            if kind in ("agent_message", "run_summary"):
                texts.append(str(event.get("delta") or event.get("text") or ""))
            elif kind in ("subagent_finished", "run_finished"):
                if event.get("output"):
                    texts.append(str(event["output"]))
            elif kind == "run_failed":
                failure = event
                break
            elif kind == "run_finished":
                break
        if failure is not None:
            return {"ok": False,
                    "errorCode": failure.get("errorCode") or "PLATFORM_ERROR",
                    "message": str(failure.get("message") or "")[:200]}
        text = "".join(texts).strip()
        if text:
            return {"ok": True, "text": text}

        # 空回复重试一次：换成更短、更像用户提问的说法。
        # 实测模型对"提问句"的应答率明显高于对"系统指令"的应答率。
        retry_prompt = ("我这次练习的成绩：%s。我接下来该重点补什么？两句话就行。"
                        % (result_text[:200] or "刚交卷"))
        retry_texts: list[str] = []
        try:
            for raw in client.run_agent(session["conversationId"] + "-r",
                                        retry_prompt, inputs):
                event = normalize(raw) if isinstance(raw, dict) else {"type": "raw"}
                kind = str(event.get("type") or "")
                seen.append("retry:" + (kind or "?"))
                if kind in ("agent_message", "run_summary"):
                    retry_texts.append(str(event.get("delta") or event.get("text") or ""))
                elif kind in ("subagent_finished", "run_finished") and event.get("output"):
                    retry_texts.append(str(event["output"]))
                elif kind == "run_finished":
                    break
        except Exception:  # noqa: BLE001 - 重试失败就按空回复处理
            pass

        retry_text = "".join(retry_texts).strip()
        if retry_text:
            return {"ok": True, "text": retry_text, "retried": True}
        return {"ok": False, "errorCode": "EMPTY_REPLY",
                "message": "平台未返回正文；事件序列=%s" % ",".join(seen[-10:]),
                "events": seen[-12:]}
    except AgentArtsError as error:
        return {"ok": False, "errorCode": "AGENTARTS_UNAVAILABLE", "message": str(error)[:200]}
    except Exception as error:  # noqa: BLE001 - 评估失败不能连带判分失败
        return {"ok": False, "errorCode": "REVIEW_FAILED",
                "message": "%s: %s" % (type(error).__name__, error)}


def _log_review(payload: dict) -> None:
    """把评估失败写进网关日志，方便 journalctl 定位。"""
    try:
        from flask import current_app
        current_app.logger.warning("platform review failed: %s", payload)
    except Exception:  # noqa: BLE001 - 日志失败不影响主流程
        pass


@agentarts_api.post("/api/v2/agent/grade")
def grade_answers():
    """提交作答 → 判分 → 更新掌握度 → 判断是否重排 → 平台评估给评语。

    数字全部由 MCP 工具算（确定性）；评语交给 AgentArts 控制器，
    这样"效果评估"这一环节确实跑在平台上，而不是被网关绕过。
    """
    body = request.get_json(silent=True) or {}
    answers = body.get("answers")
    if not isinstance(answers, list) or not answers:
        return _error(400, "VALIDATION_ERROR", "answers 不能为空数组")

    from app.services import mcp_client

    user_id = current_user_id() or resolve_user_id(None)

    graded = mcp_client.call_with_session("grade_exercise", {"answers": answers}, timeout=25)
    if not graded.get("ok"):
        return jsonify({"errorCode": graded.get("errorCode") or "GRADE_FAILED",
                        "message": graded.get("message") or "判分失败",
                        "details": graded.get("details")}), 400

    try:
        return _grade_pipeline(graded, mcp_client, user_id)
    except Exception as error:  # noqa: BLE001 - 任何异常都要变成可读 JSON，不能吐 500 页面
        return jsonify({"errorCode": "GRADE_PIPELINE_FAILED",
                        "message": "判分后续处理失败：%s: %s" % (type(error).__name__, error)}), 500


def _grade_pipeline(graded: dict, mcp_client, user_id: str):
    """判分之后的掌握度/重排/平台评估。单独成函数，便于整体兜异常。"""
    data = graded.get("data") or {}
    updates = [{"knowledgePointId": row.get("knowledgePointId"),
                "assessmentScore": round(float(row.get("accuracy") or 0) * 100, 2)}
               for row in (data.get("masteryUpdates") or []) if row.get("knowledgePointId")]

    mastery = mcp_client.call_with_session("update_mastery", {"updates": updates}) \
        if updates else {"ok": True, "data": {"mastery": []}}

    rows = ((mastery.get("data") or {}).get("mastery") or []) if mastery.get("ok") else []

    # 交卷自动收集错题（只入错题本，不再动掌握度——上面已经改过一轮）
    from app.services import wrongbook
    collected = 0
    for row in (data.get("perQuestion") or []):
        if row.get("correct"):
            continue
        wrongbook.record(_repository, user_id, {
            "exerciseId": row.get("exerciseId"),
            "stem": row.get("stem") or "",
            "knowledgePointId": row.get("knowledgePointId"),
            "answer": row.get("answer"),
            "correctAnswer": row.get("correctAnswer"),
            "reason": row.get("explanation"),
            "sources": row.get("sources") or [],
            "sourceType": "grade",
        })
        collected += 1
    lowest = min((float(row.get("newScore") or 0) for row in rows), default=0)
    first_point = (rows[0].get("knowledgePointId") if rows else "") or ""

    assessment = {"masteryScore": lowest, "assessmentScore": float(data.get("score") or 0),
                  "incompleteTasks": 0,
                  "repeatedError": float(data.get("score") or 0) < 80,
                  "knowledgePointId": first_point}
    replan = mcp_client.call_with_session("request_replan", {"assessment": assessment}) \
        if first_point else {"ok": False, "errorCode": "NO_POINT"}

    # 平台评估：把已经算好的数字原样交给控制器，只要评语，不要它重算。
    wrong_note = "；".join(
        "第%d题选%s错，正确答案%s" % (index + 1, row.get("answer") or "—", row.get("correctAnswer"))
        for index, row in enumerate(data.get("perQuestion") or []) if not row.get("correct"))
    mastery_note = "；".join(
        "%s %s→%s" % (row.get("knowledgePointId"), row.get("oldScore"), row.get("newScore"))
        for row in rows)
    # 提交答案 → 走评估工作流。用"用户口吻的提问"更容易被控制器正确分派，
    # 系统指令口吻容易被判成"无需回答"而返回空。
    result_text = (
        "得分 %s；错题：%s；掌握度：%s；计划：%s"
        % (data.get("score"), wrong_note or "无", mastery_note or "无",
           "已重排" if (replan.get("data") or {}).get("changed") else "无需调整"))
    review_prompt = (
        "我这次练习刚交卷，成绩单如下（这些数字是系统算好的，不要重算）：\n%s\n"
        "帮我点评两句：一句说最该补的点，一句说接下来具体做什么。" % result_text)
    evaluation = _platform_review(review_prompt, "assess-" + str(user_id)[-8:], result_text)
    if not evaluation.get("ok"):
        _log_review(evaluation)

    # 解析单独成一条"回答"：卡片只留分数与对错，讲解正文放进对话流。
    # 内容取自出题时落库的 explanation，不是提交后现编的。
    def _num(value):
        try:
            return "%g" % float(value)
        except (TypeError, ValueError):
            return "—"

    reply_lines = ["本次得分 %s 分。" % _num(data.get("score"))]
    for index, row in enumerate(data.get("perQuestion") or []):
        head = "%s 第 %d 题：你选 %s" % ("✅" if row.get("correct") else "❌",
                                        index + 1, row.get("answer") or "—")
        if not row.get("correct"):
            head += "，正确答案 %s" % (row.get("correctAnswer") or "—")
        reply_lines.append(head)
        if row.get("explanation"):
            reply_lines.append("　解析：" + str(row["explanation"]))
        source = (row.get("sources") or [{}])[0]
        if source.get("fileName"):
            reply_lines.append("　依据：" + str(source["fileName"]) +
                               (" · " + str(source["headingPath"]) if source.get("headingPath") else ""))
    for row in rows:
        reply_lines.append("掌握度 %s：%s → %s" % (row.get("knowledgePointId"),
                                                 _num(row.get("oldScore")), _num(row.get("newScore"))))
    if (replan.get("data") or {}).get("changed"):
        reply_lines.append("计划已重排（%s）。" % "、".join((replan.get("data") or {}).get("reasons") or []))
    if evaluation.get("ok") and evaluation.get("text"):
        reply_lines.append("平台评估：" + str(evaluation["text"]).strip())
    else:
        # 失败也要写出来：静默省略会让人以为"这个功能没做"，而不是"这次没通"。
        reply_lines.append("平台评估：（本次不可用：%s%s）" % (
            evaluation.get("errorCode") or "NO_TEXT",
            ("，" + str(evaluation.get("message"))[:60]) if evaluation.get("message") else ""))

    return jsonify({
        "score": data.get("score"),
        "perKnowledgeAccuracy": data.get("perKnowledgeAccuracy") or {},
        "errorTypes": data.get("errorTypes") or [],
        # 逐题对错与正确答案：页面用来标"哪题错了、正确的是哪个"
        "perQuestion": data.get("perQuestion") or [],
        "mastery": rows,
        "replan": {
            "ok": bool(replan.get("ok")),
            "needReplan": (replan.get("data") or {}).get("needReplan"),
            "changed": (replan.get("data") or {}).get("changed"),
            "reasons": (replan.get("data") or {}).get("reasons") or [],
            "planDiff": (replan.get("data") or {}).get("planDiff"),
            "errorCode": replan.get("errorCode"),
        },
        # 平台评估结果：评语来自 AgentArts，数字仍来自工具
        "evaluation": evaluation,
        # 供前端直接作为一条对话回答渲染（解析正文）
        "reply": "\n".join(reply_lines),
    })


# ------------------------------------------------------------------ 状态
@agentarts_api.get("/demo")
def demo_page():
    """可视化 Demo 页面（单文件，无构建链）。

    放在网关而不是单独部署前端：演示时只暴露一个端口，少一个会挂的组件。
    """
    return send_from_directory(DEMO_DIR, "index.html")


# ------------------------------------------------------------------ 图片错题
@agentarts_api.post("/api/v2/agent/vision")
def vision_analyze():
    """把一张拍照题转成结构化文本。

    只做识别，不做诊断结论：识别出的题干由前端填进输入框，
    再由用户确认后走正常的云端诊断链路（见 wf-diagnosis）。
    这样"识别错"和"诊断错"在日志里能分开定位。
    """
    body = request.get_json(silent=True) or {}
    image = body.get("imageBase64") or body.get("image") or ""
    hint = str(body.get("hint") or "")

    from app.services import wrong_photo

    result = wrong_photo.analyze(image, hint)
    if not result.get("ok"):
        return jsonify({"fallback": False,
                        "errorCode": result.get("error_code"),
                        "message": result.get("message")}), 400
    payload = result["data"]
    payload["fallback"] = False
    return jsonify(payload)


@agentarts_api.get("/api/v2/agent/runtime")
def runtime_status():
    config = _cfg()
    payload = config.describe()
    payload["auth"] = _tokens.snapshot() if _tokens is not None else {"cached": False}
    payload["contractNote"] = "v2 端点暂不校验 X-API-Contract-Version，契约升到 v0.4 后统一"
    return jsonify(payload)


# ------------------------------------------------------------------ 反馈
@agentarts_api.post("/api/v2/agent/feedback")
def submit_feedback():
    data = _payload()
    rating = str(data.get("rating") or "").strip()
    if rating not in ("helpful", "unhelpful"):
        return _error(400, "BAD_REQUEST", "rating 只能是 helpful / unhelpful")

    run_id = str(data.get("runId") or "").strip()
    run = store.get_run(_repository, run_id) if run_id else None
    if not isinstance(run, dict):
        return _error(404, "NOT_FOUND", "run 不存在", details={"runId": run_id})

    user_id = current_user_id() or resolve_user_id(data.get("userId"))
    if not can_read_user(str(run.get("userId") or "")):
        return _error(404, "NOT_FOUND", "run 不存在", details={"runId": run_id})

    record = store.save_feedback(_repository, run_id=run_id, user_id=user_id, rating=rating,
                                note=str(data.get("note") or ""))
    return jsonify({"status": "ok", "feedbackId": record["feedbackId"]}), 201
