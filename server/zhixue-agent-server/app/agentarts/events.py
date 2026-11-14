# -*- coding: utf-8 -*-
"""平台事件 → 网关内部事件。

为什么不在前端直接消费平台事件
------------------------------
平台事件是"给开发者看的"（`plugin_start` / `summary_response`），而前端要的是
"给用户看的"（工具开始、工具完成、指标、最终消息）。中间这层归一化还能吃掉两类差异：

1. 平台可能新增事件类型 —— 未知事件统一包成 `raw` 透传，**不丢弃**，否则调试时
   会发现"平台明明调了工具，前端什么都没有"。
2. 多智能体场景可能追加节点/转交事件 —— 同样落进 `raw`，等确认字段后再提升为正式事件。
"""

from __future__ import annotations

#: 内部事件类型。前端与评测脚本只依赖这一组，不依赖平台命名。
RUN_STARTED = "run_started"
AGENT_MESSAGE = "agent_message"
#: 模型思维链增量。不是答案，前端应折叠为"思考中"，不要拼进回复。
AGENT_THINKING = "agent_thinking"
TOOL_STARTED = "tool_started"
TOOL_FINISHED = "tool_finished"
METRICS = "metrics"
RUN_SUMMARY = "run_summary"
RUN_FINISHED = "run_finished"
RUN_FAILED = "run_failed"
RAW = "raw"
#: 控制器转交给了哪个子工作流。这是"多 Agent 协作"的可视化证据。
SUBAGENT_STARTED = "subagent_started"
SUBAGENT_FINISHED = "subagent_finished"


def _pick_text(payload: dict) -> str:
    """从平台事件里尽力取出正文。

    为什么不能只读 `content`：实测（贵阳一 · 2026-11）多智能体返回的
    `message` 事件里正文位置不固定，可能是 content / text / delta，
    也可能嵌在 `data` 或 `content` 对象里。取错就得到一串空 delta，
    前端表现为"那个 Agent 在干活，但一个字都不说"。
    """
    if not isinstance(payload, dict):
        return ""
    # 实测平台把正文放在 `data` 里（{"event":"message","data":{"content":"…"}}），
    # 所以除了顶层字段，还要往下钻一层。漏了 `data` 就会出现"一堆空 delta"。
    for key in ("content", "text", "delta", "message", "answer", "output",
                "summary", "data", "payload", "message_data"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for inner in ("content", "text", "delta", "value", "answer", "output", "summary"):
                nested = value.get(inner)
                if isinstance(nested, str) and nested:
                    return nested
            # 再深一层（data.content.text 这类嵌套）
            for inner in value.values():
                if isinstance(inner, dict):
                    for deep in ("content", "text", "delta", "value", "summary"):
                        text = inner.get(deep)
                        if isinstance(text, str) and text:
                            return text
    return ""


def _deep_text(payload, depth: int = 3) -> str:
    """在嵌套结构里找一段像"答案"的文本。

    平台不同版本把最终产出放在 output / result / text / content / answer 下，
    层级也不固定。这里做有限深度扫描：只认长度 > 4 的字符串，避免把
    "true"、"v1" 这类短值当成正文。
    """
    if depth <= 0 or not isinstance(payload, (dict, list)):
        return ""
    items = payload.values() if isinstance(payload, dict) else payload
    for item in items:
        if isinstance(item, str):
            if len(item.strip()) > 4 and any(
                    ch in item for ch in "。！？，"):
                return item.strip()
        elif isinstance(item, (dict, list)):
            found = _deep_text(item, depth - 1)
            if found:
                return found
    return ""


def normalize(event: dict) -> dict:
    """单个平台事件 → 内部事件。纯函数，不抛异常（坏数据退化为 raw）。"""
    if not isinstance(event, dict):
        return {"type": RAW, "rawEvent": str(event)[:200]}

    name = str(event.get("event") or "")
    created = event.get("createdTime")

    if name == "start":
        return {"type": RUN_STARTED, "createdTime": created}

    if name == "workflow_start":
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        return {"type": SUBAGENT_STARTED,
                "workflowId": str(data.get("workflow_id") or ""),
                "workflowName": str(data.get("workflow_name") or ""),
                "createdTime": created}

    if name == "workflow_end":
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        payload = {"type": SUBAGENT_FINISHED,
                   "workflowId": str(data.get("workflow_id") or ""),
                   "workflowName": str(data.get("workflow_name") or ""),
                   "createdTime": created}
        # 工作流的最终产出常在这里（output / result / text…），
        # 不接的话前端只会看到一串"思考中"然后结束、一个字都没有。
        output = _deep_text(data)
        if output:
            payload["output"] = output
        else:
            payload["rawEvent"] = event
        return payload

    if name == "end":
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        output = _deep_text(data)
        payload = {"type": RUN_FINISHED, "createdTime": created}
        if output:
            payload["output"] = output
        return payload

    if name == "error":
        # 平台把"模型鉴权失败""工具异常"这类致命错误放在 `error` 事件里，
        # 之后仍会补一个 `done`。**不能**因为它有 done 就当成成功——
        # 否则前端收到空回复、后台记为 completed，排查时完全没有线索。
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        return {
            "type": RUN_FAILED,
            "errorCode": str(data.get("code") or event.get("code") or "PLATFORM_ERROR"),
            "message": str(data.get("message") or event.get("error_msg") or "平台执行失败")[:500],
            "errorMsg": str(data.get("error_msg") or ""),
            "createdTime": created,
        }

    if name == "message":
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        text = _pick_text(event)
        if text:
            return {"type": AGENT_MESSAGE, "delta": text, "createdTime": created,
                    "nodeName": str(data.get("node_name") or "")}
        # 实测多智能体的 LLM 节点先流"思维链"（data.reasoning_content），
        # 此时 data.text 为空。把它当答案会给用户看一堆英文推理过程，
        # 所以单独成事件：前端折叠显示"思考中"即可。
        reasoning = str(data.get("reasoning_content") or "")
        if reasoning:
            return {"type": AGENT_THINKING, "delta": reasoning, "createdTime": created}
        return {"type": RAW, "platformEvent": "message", "rawEvent": event}

    if name == "plugin_start":
        plugin = event.get("plugin") if isinstance(event.get("plugin"), dict) else {}
        return {
            "type": TOOL_STARTED,
            "name": str(plugin.get("name") or ""),
            "arguments": plugin.get("arguments") if isinstance(plugin.get("arguments"), dict) else {},
            "createdTime": created,
        }

    if name == "plugin_end":
        plugin = event.get("plugin") if isinstance(event.get("plugin"), dict) else {}
        latency = event.get("latency") if isinstance(event.get("latency"), dict) else {}
        return {
            "type": TOOL_FINISHED,
            "name": str(plugin.get("name") or ""),
            "ok": bool(event.get("success", True)),
            "latencyMs": latency.get("plugin"),
            "createdTime": created,
        }

    if name == "statistic_data":
        latency = event.get("latency") if isinstance(event.get("latency"), dict) else {}
        return {
            "type": METRICS,
            "latency": {key: latency.get(key) for key in ("plugin", "model", "overall")},
            "createdTime": created,
        }

    if name == "summary_response":
        return {"type": RUN_SUMMARY, "text": _pick_text(event),
                "role": str(event.get("role") or "assistant"), "createdTime": created}

    if name == "done":
        return {"type": RUN_FINISHED, "createdTime": created}

    return {"type": RAW, "platformEvent": name or "unknown", "rawEvent": event}


def collect_text(events) -> str:
    """从已归一化的事件里拼出完整回复（用于落库与评测比对）。"""
    return "".join(item.get("delta") or "" for item in events if item.get("type") == AGENT_MESSAGE)


def tool_names(events) -> list[str]:
    """按出现顺序去重收集工具名，评测脚本用它断言"该调的工具调了没有"。"""
    seen: list[str] = []
    for item in events:
        if item.get("type") == TOOL_STARTED:
            name = str(item.get("name") or "")
            if name and name not in seen:
                seen.append(name)
    return seen
