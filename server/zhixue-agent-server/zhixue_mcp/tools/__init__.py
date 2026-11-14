# -*- coding: utf-8 -*-
"""工具声明：11 个工具的元数据在这里汇总。

描述（description）不是注释，而是**模型选工具的依据**。写不清楚的后果很具体：
规划 Agent 该调 `fit_plan_to_budget` 的时候去调了 `calculate_learning_priority`，
于是计划时长永远是默认值。所以每条描述都写三件事：什么时候用、返回什么、什么时候别用。
"""

from __future__ import annotations

from ..registry import ToolRegistry, ToolSpec
from . import compute, read, write

SESSION_REF = {"type": "string", "description": "网关签发的一次性会话引用，用于换取真实 userId"}

TOOL_SPECS = [
    # ---------------------------------------------------------------- 只读
    ToolSpec(
        name="get_learning_context",
        title="读取学习上下文",
        description=("任何规划/诊断动作之前先调用。返回用户目标、考试日期、可用时间、"
                     "当前计划版本与最薄弱的 5 个知识点。用于判断'有没有计划''时间够不够'，"
                     "不要凭对话内容猜测用户课程。"),
        properties={"sessionRef": SESSION_REF},
        handler=read.get_learning_context,
    ),
    ToolSpec(
        name="search_learning_materials",
        title="检索课程资料",
        description=("需要引用讲义原文时调用。返回带文件名、章节路径、片段哈希的命中列表。"
                     "回答知识点问题、或为出题找依据时必须先调用它；"
                     "返回空表示该用户没有资料，此时应如实说明，不要编造引用。"),
        properties={
            "sessionRef": SESSION_REF,
            "query": {"type": "string", "description": "检索问题或关键词，中文原句即可"},
            "kbId": {"type": "string", "description": "限定某个知识库，可省略"},
            "topK": {"type": "integer", "description": "返回条数 1~10，默认 5"},
        },
        required=["sessionRef", "query"],
        handler=read.search_learning_materials,
    ),
    ToolSpec(
        name="get_learning_evidence",
        title="读取学习证据",
        description=("诊断薄弱点前调用。返回掌握度、错题记录、证据数量。"
                     "`hasEvidence` 为 false 时**不得**给出薄弱点结论，应改为建议一次短测。"),
        properties={
            "sessionRef": SESSION_REF,
            "knowledgePointId": {"type": "string", "description": "只看某个知识点，可省略"},
        },
        handler=read.get_learning_evidence,
    ),
    # ---------------------------------------------------------------- 计算
    ToolSpec(
        name="calculate_learning_priority",
        title="计算学习优先级",
        description=("在候选知识点之间排序时调用。输入知识点数组（含 masteryScore / "
                     "errorIntensity / importance），返回五因子拆解与总分。"
                     "也可用 contextText 直接传 get_learning_context 的输出文本，工具会提取 weakPoints。"
                     "只排序、不排时间；要落到具体分钟数请用 fit_plan_to_budget。"),
        properties={
            "sessionRef": SESSION_REF,
            "knowledgePoints": {"type": "array", "items": {"type": "object"},
                                "description": "知识点数组，每项含 knowledgePointId"},
            "contextText": {"type": "string",
                            "description": "可直接传 get_learning_context 的输出文本，工具会提取 weakPoints"},
            "context": {"type": "object", "description": "如 {\"daysLeft\": 3}，影响紧迫度因子"},
        },
        required=["sessionRef"],
        handler=compute.calculate_learning_priority,
    ),
    ToolSpec(
        name="fit_plan_to_budget",
        title="按可用时间裁剪计划",
        description=("用户说了'只有 N 分钟'时必须调用。整任务排入，排不下的进入 "
                     "unscheduledTasks 并带原因，不拆分任务。返回新计划与预算汇总。"
                     "plan 可传对象，也可用 planText 传 JSON 文本。"),
        properties={
            "sessionRef": SESSION_REF,
            "plan": {"type": "object", "description": "待裁剪的计划对象（含 tasks）"},
            "planText": {"type": "string", "description": "计划的 JSON 文本，与 plan 二选一"},
            "availableMinutes": {"type": "integer", "description": "可用分钟数 0~1440"},
            "prioritizePoint": {"type": "string", "description": "优先保留的知识点 ID，可省略"},
        },
        required=["sessionRef", "availableMinutes"],
        handler=compute.fit_plan_to_budget,
    ),
    ToolSpec(
        name="grade_exercise",
        title="确定性判分",
        description=("用户提交答案后调用。分数只来自服务端标准答案，**不接受调用方提供答案键**。"
                     "返回总分、逐知识点正确率与建议的新掌握度。不要自己算分。"),
        properties={
            "sessionRef": SESSION_REF,
            "answers": {"type": "array", "items": {"type": "object"},
                        "description": "作答数组，每项 {\"exerciseId\": \"...\", \"answer\": \"B\"}"},
            "answersText": {"type": "string", "description": "作答数组的 JSON 文本，与 answers 二选一"},
            "exerciseResultId": {"type": "string", "description": "本次作答记录 ID，可省略"},
        },
        required=["sessionRef"],
        handler=compute.grade_exercise,
    ),
    ToolSpec(
        name="get_plan_diff",
        title="对比两版计划",
        description=("向用户解释'改了什么'时调用。返回保留/压缩/延期/移除的任务及版本号。"
                     "只读，不落库。oldPlan/newPlan 可传对象，也可用 oldPlanText/newPlanText 传文本。"),
        properties={
            "sessionRef": SESSION_REF,
            "oldPlan": {"type": "object", "description": "旧计划对象"},
            "newPlan": {"type": "object", "description": "新计划对象"},
            "oldPlanText": {"type": "string", "description": "旧计划 JSON 文本"},
            "newPlanText": {"type": "string", "description": "新计划 JSON 文本"},
        },
        required=["sessionRef"],
        handler=compute.get_plan_diff,
    ),
    # ---------------------------------------------------------------- 写
    ToolSpec(
        name="save_learning_plan",
        title="保存计划（带版本与幂等）",
        description=("计划确认后调用。必须带 expectedVersion（首次为 0）与 idempotencyKey；"
                     "版本不一致会返回 CONFLICT，此时应重新读取上下文再生成，不要重试同一份计划。"),
        properties={
            "sessionRef": SESSION_REF,
            "plan": {"type": "object", "description": "计划对象（含 tasks）"},
            "planText": {"type": "string", "description": "计划 JSON 文本，与 plan 二选一"},
            "expectedVersion": {"type": "integer", "description": "期望的当前版本，首次传 0"},
            "idempotencyKey": {"type": "string", "description": "本次提交的唯一键，重试时保持不变"},
            "reason": {"type": "string", "description": "生成/调整原因，写入版本历史"},
            "evidenceIds": {"type": "array", "items": {"type": "string"},
                            "description": "支撑本次计划的证据 ID"},
        },
        required=["sessionRef", "expectedVersion", "idempotencyKey"],
        write=True,
        handler=write.save_learning_plan,
    ),
    ToolSpec(
        name="update_mastery",
        title="更新知识点掌握度",
        description=("判分之后调用，逐知识点更新画像。输入评估分（0~100），"
                     "服务端按固定规则换算成新掌握度并累加版本号。不要直接写掌握度数值。"
                     "updates 可传数组，也可用 updatesText 传 JSON 文本。"),
        properties={
            "sessionRef": SESSION_REF,
            "updates": {"type": "array", "items": {"type": "object"},
                        "description": "数组，每项 {\"knowledgePointId\": \"...\", "
                                       "\"assessmentScore\": 66.67}"},
            "updatesText": {"type": "string", "description": "updates 的 JSON 文本"},
        },
        required=["sessionRef"],
        write=True,
        handler=write.update_mastery,
    ),
    ToolSpec(
        name="request_replan",
        title="请求重排计划",
        description=("评估后判断是否需要调整计划时调用。输入评估结果（masteryScore / "
                     "incompleteTasks / repeatedError / assessmentScore / knowledgePointId），"
                     "服务端决定是否重排并返回差异。不要自己判断'要不要改计划'。"
                     "assessment 可传对象，也可用 assessmentText 传 JSON 文本。"),
        properties={
            "sessionRef": SESSION_REF,
            "assessment": {"type": "object", "description": "评估结果对象"},
            "assessmentText": {"type": "string", "description": "评估结果的 JSON 文本"},
        },
        required=["sessionRef"],
        write=True,
        handler=write.request_replan,
    ),
    ToolSpec(
        name="create_grounded_exercise",
        title="按资料出题",
        description=("只基于用户已上传资料出题，返回题目与来源片段。"
                     "knowledgePointId 可省略：省略时由**资料里抽出的知识点**决定考什么"
                     "（可用 topic 传用户原话，命中其中的知识点会优先），"
                     "并在 pointSource 里注明来源（explicit / auto_from_topic / auto_from_material）。"
                     "资料里没有知识点会返回 INSUFFICIENT_MATERIALS 并给出 candidates——"
                     "此时应请用户选一个，不要改用通用题库冒充'依据资料'。"),
        properties={
            "sessionRef": SESSION_REF,
            "knowledgePointId": {"type": "string", "description": "目标知识点 ID，可省略"},
            "topic": {"type": "string", "description": "用户原话，用于在资料知识点里匹配"},
            "count": {"type": "integer", "description": "题量 1~5，默认 3"},
            "kbId": {"type": "string", "description": "限定知识库，可省略"},
        },
        required=["sessionRef"],
        write=False,
        handler=write.create_grounded_exercise,
    ),
    ToolSpec(
        name="record_wrong_question",
        title="记录错题并更新学情",
        description=("用户拍题/贴题并确认做错后调用。把错题写入错题本，并按与判分一致的"
                     "规则下调该知识点掌握度（默认 assessmentScore=0）。"
                     "返回旧/新掌握度与当前错误次数，供答复引用；不要自己算新掌握度。"),
        properties={
            "sessionRef": SESSION_REF,
            "knowledgePointId": {"type": "string", "description": "这道题考查的知识点 ID"},
            "knowledgePointName": {"type": "string",
                                   "description": "知识点中文名，可省略（省略时用 ID）"},
            "stem": {"type": "string", "description": "题干原文（识别结果或用户输入）"},
            "reason": {"type": "string", "description": "错因/解析，可省略"},
            "sourceType": {"type": "string",
                           "description": "来源：photo / manual / bank，默认 photo"},
            "exerciseId": {"type": "string",
                           "description": "已有题号，可省略；省略时按题干哈希生成"},
            "assessmentScore": {"type": "integer",
                                "description": "本次评估分 0~100，默认 0（做错）"},
        },
        required=["sessionRef", "knowledgePointId", "stem"],
        write=True,
        handler=write.record_wrong_question,
    ),
]


def build_registry() -> ToolRegistry:
    return ToolRegistry(TOOL_SPECS)
