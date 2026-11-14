# -*- coding: utf-8 -*-
"""工具注册表：声明式定义 + 统一校验 + 统一分发。

一条纪律：**注册表里只描述"怎么调已有的函数"，不实现业务规则。**
优先级、预算裁剪、判分、掌握度、重排的算法都在 `app/decision` 与 `app/tools` 里，
那些代码有测试覆盖；这里复制一份就等于制造第二份真源，两边迟早算得不一样。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from . import envelope

logger = logging.getLogger("zhixue.mcp")

#: 类型名 → 校验函数。只做粗校验：MCP 的 inputSchema 由平台侧保证结构，
#: 这里防的是"模型把字符串塞进整数位"这类明显错误。
_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass
class ToolContext:
    repository: Any
    user_id: str
    session_ref: str = ""
    conversation_id: str = ""
    idempotency_key: str = ""


@dataclass
class ToolSpec:
    name: str
    title: str
    description: str
    properties: dict[str, dict]
    handler: Callable[[dict, ToolContext], dict]
    required: list[str] = field(default_factory=lambda: ["sessionRef"])
    write: bool = False

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": self.properties,
            "required": list(self.required),
            "additionalProperties": False,
        }

    def validate(self, arguments: dict) -> str:
        for key in self.required:
            value = arguments.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                return "缺少必填参数：%s" % key
        for key, spec in self.properties.items():
            if key not in arguments:
                continue
            value = arguments[key]
            expected = spec.get("type")
            python_type = _TYPES.get(expected)
            if python_type is None:
                continue
            # 工作流会把未配置的可选参数显式传成 null（实测 `"plan": null`）。
            # null 应当等价于"没传"，交给 handler 的备用参数去处理，不能判定为类型错误。
            if value is None:
                continue
            # bool 是 int 的子类，单独挡一下，避免 true 被当成 1。
            if expected in ("integer", "number") and isinstance(value, bool):
                return "参数 %s 类型应为 %s" % (key, expected)
            if not isinstance(value, python_type):
                return "参数 %s 类型应为 %s" % (key, expected)
        return ""

    def describe(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("工具名重复")

    # ------------------------------------------------------------ 查询
    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def describe(self) -> list[dict]:
        return [tool.describe() for tool in self._tools.values()]

    def writes(self) -> list[str]:
        return [tool.name for tool in self._tools.values() if tool.write]

    # ------------------------------------------------------------ 调用
    def call(self, name: str, arguments: dict, context: ToolContext) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            return envelope.fail(envelope.NOT_FOUND, "未知工具：%s" % name,
                                 details={"available": list(self._tools)})

        # 越权防线（第一层）：模型不能自己声称身份。
        if "userId" in arguments:
            return envelope.fail(
                envelope.VALIDATION_ERROR,
                "工具入参不允许 userId；身份只能由 sessionRef 推导",
                details={"parameter": "userId"})

        # 先做类型还原，再校验：工作流传进来的是 JSON 文本，
        # 直接按"数组/对象"校验会误报"请求参数数据格式有误"。
        coerced, problem = _coerce_arguments(tool, arguments)
        if problem:
            return envelope.fail(envelope.VALIDATION_ERROR, problem,
                                 details={"tool": name})

        problem = tool.validate(coerced)
        if problem:
            return envelope.fail(envelope.VALIDATION_ERROR, problem,
                                 details={"tool": name})

        try:
            return tool.handler(coerced, context)
        except Exception as error:  # noqa: BLE001 - 工具边界必须吞异常并结构化
            logger.exception("mcp tool failed: %s", name)
            return envelope.fail(envelope.INTERNAL_ERROR, "工具执行失败：%s" % (error,),
                                 details={"tool": name})


def _coerce_arguments(tool: ToolSpec, arguments: dict) -> tuple[dict, str]:
    """把工作流传来的 JSON 字符串还原成对象/数组/数字。

    为什么需要：工作流节点之间传的是**文本**，MCP 工具要的是结构化参数。
    实测平台会把上一个工具的输出（一段 JSON 文本）原样塞进下一个工具的参数位，
    于是 `knowledgePoints` 收到的是字符串而不是数组，直接报"请求参数数据格式有误"。

    这里做**宽松解析**：类型对就原样用；是字符串就按 JSON 解；
    解不出来才报错。对模型与工作流更宽容，但不放松业务校验。
    """
    coerced = dict(arguments)
    for key, spec in tool.properties.items():
        if key not in coerced:
            continue
        value = coerced[key]
        expected = spec.get("type")
        if not isinstance(value, str) or expected not in ("array", "object", "integer", "number"):
            continue
        text = value.strip()
        if not text:
            # 平台对"未配置"的结构化参数会填空字符串（实测 `"answers": ""`），
            # 空串必须等价于没传，否则会被判定为类型错误，把整次调用挡在门外。
            coerced[key] = None
            continue
        try:
            if expected in ("array", "object"):
                coerced[key] = json.loads(_clean_json_text(text))
            elif expected == "integer":
                coerced[key] = int(float(text))
            else:
                coerced[key] = float(text)
        except (ValueError, TypeError):
            return coerced, "参数 %s 需要 %s，但收到的文本无法解析：%s" % (
                key, expected, text[:80])
    return coerced, ""


def _clean_json_text(raw: str) -> str:
    """去掉模型爱加的 Markdown 代码围栏。

    实测：模型经常输出 ```json {...} ```，工作流把它原样当参数传过来，
    `json.loads` 直接失败，最终表现为"plan 必须是计划对象"这类看似参数错的报错。
    统一在这里剥掉围栏，比要求模型"不要加围栏"可靠得多。
    """
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_value(text: str):
    """从"带解释文字的文本"里抠出第一个完整的 JSON 对象/数组。

    模型经常输出「下面是计划：{...}」或「{...}\\n以上是计划」，直接 json.loads 会失败。
    这里按括号配平扫描，找到第一个能解析成功的片段。
    """
    starts = [i for i, ch in enumerate(text) if ch in "{["]
    for start in starts:
        opening = text[start]
        closing = "}" if opening == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            ch = text[index]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == opening:
                depth += 1
            elif ch == closing:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except ValueError:
                        break
    return None


def parse_json_arg(raw, expected_type):
    """把「JSON 文本」还原成对象/数组；若外面套着信封，自动取 `data`。

    供工具内部处理 `planText` / `answersText` / `assessmentText` 这类替代入参：
    工作流节点只能传文本，而工具需要结构化数据。

    三级容错：纯 JSON → 去代码围栏 → 从长文本里抠 JSON。
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = _clean_json_text(raw)
    value = None
    try:
        value = json.loads(text)
    except ValueError:
        value = _extract_json_value(text)
    if value is None:
        return None
    if isinstance(value, dict) and isinstance(value.get("data"), (dict, list)):
        value = value["data"]
    return value if isinstance(value, expected_type) else None
