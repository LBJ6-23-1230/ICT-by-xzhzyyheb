# wf-assessment 试运行证据（工具级）

| 项 | 值 |
|---|---|
| 工作流 | `wf-assessment`（学习评估） |
| 工具链 | `grade_exercise` → `update_mastery` → `request_replan` |
| 数据 | `sessionRef=sref-demo-0000000000000001`；题 `ex-demo-1`（答案 B）；计划 `plan-demo` v1（两任务各 30 分钟） |

> 评估类能力的关键不是"能判分"，而是**判完之后该不该改计划**。
> 所以这里同时保存"满分不改计划"与"低分触发重排"两个方向。

---

## A1 判分：满分

**请求**：`answersText=[{"exerciseId":"ex-demo-1","answer":"B"}]`

**输出**

```json
{"ok": true, "data": {
  "score": 100.0,
  "perKnowledgeAccuracy": {"binary-tree-traversal": 1.0},
  "errorTypes": [],
  "oldMastery": 42,
  "suggestedNewMastery": 71,
  "masteryUpdates": [{"knowledgePointId": "binary-tree-traversal",
                      "oldScore": 42, "newScore": 71, "accuracy": 1.0,
                      "questionCount": 1, "correctCount": 1}]
}}
```

**结论**

1. 分数只来自服务端答案键，请求里即使带 `answerKeys` 也会被忽略（见 S3 自检第 4 组）。
2. 掌握度按固定规则换算：满分 **+29**（42 → 71），不是模型估计。
3. 逐知识点给出 `questionCount / correctCount / accuracy`，可作为画像更新的依据。

---

## A2 掌握度落库

**请求**：`updatesText=[{"knowledgePointId":"binary-tree-traversal","assessmentScore":100}]`

**输出**

```json
{"ok": true, "data": {"mastery": [{"knowledgePointId": "binary-tree-traversal",
  "oldScore": 42.0, "newScore": 71.0, "assessmentScore": 100.0}],
  "profileVersion": 2}}
```

**结论**

1. 画像版本号递增（1 → 2），说明写入是可追溯的，不是覆盖。
2. `oldScore` 来自库里的真实值，不是请求方传进来的——避免"自己声明旧分"。

---

## A3 不该重排：满分 + 无未完成任务

**请求**

```json
{"masteryScore":71,"assessmentScore":100,"incompleteTasks":0,
 "repeatedError":false,"knowledgePointId":"binary-tree-traversal"}
```

**输出**

```json
{"ok": true, "data": {"needReplan": false, "reasons": [], "changed": false}}
```

**结论**

1. 满分、无未完成任务、无重复错误 → **不动计划**，避免"每做一次题就改一次安排"。
2. `reasons` 为空数组，说明判定依据是显式的，不是黑盒。

---

## A4 该重排：掌握度低于阈值 + 有未完成任务 + 重复出错

**请求**

```json
{"masteryScore":40,"assessmentScore":33.33,"incompleteTasks":1,
 "repeatedError":true,"knowledgePointId":"binary-tree-traversal"}
```

**输出（节选）**

```json
{"ok": true, "data": {
  "needReplan": true, "changed": true,
  "reasons": ["mastery_below_threshold", "incomplete_tasks", "repeated_error"],
  "plan": {"planId": "plan-demo", "version": 2,
           "reason": "mastery_below_threshold;incomplete_tasks;repeated_error;score_33.33",
           "tasks": [
             {"taskId": "task-postorder", "durationMinutes": 50},
             {"taskId": "task-graph", "durationMinutes": 15}]},
  "planDiff": {"oldVersion": 1, "newVersion": 2, "changedTasks": [ ...两条... ]},
  "history": {"oldVersion": 1, "newVersion": 2, "reason": "mcp_replan",
              "evidenceIds": [], "timestamp": "..."}
}}
```

**结论**

1. 三条触发原因**逐条列出**，不是笼统的"需要调整"。
2. 计划版本 1 → 2，薄弱点 **30 → 50 分钟**，另一任务 **30 → 15 分钟**——按得分分级加时，不是固定 ±15。
3. `planDiff` 给出改动前后对照，`history` 落库，前端可展示"为什么改"。

---

## A5 负样本：题号不存在

**请求**：`answersText=[{"exerciseId":"not-exist","answer":"B"}]`

**输出**

```json
{"ok": false, "errorCode": "NOT_FOUND",
 "message": "这些题目在服务端找不到标准答案，拒绝判分",
 "details": {"exerciseIds": ["not-exist"]}}
```

**结论**

1. 题号不认识时**拒绝判分**，不会默认给 0 分——"答错"和"没有这道题"必须区分。
2. 同理，`request_replan` 在没有当前计划时返回 `NOT_FOUND` 而非静默成功。

> 演示时若把 `NOT_FOUND` 说成"答错了"，会被追问穿。这两个语义要分清。

---

## A6 工作流级（端到端）

**输入**：`answers=[{"exerciseId":"ex-demo-1","answer":"B"}]`，真实 `sessionRef`

**链路**：`grade_exercise` → 大模型A（拆 updates/assessment）→ `update_mastery` → `request_replan` → 大模型B（总结）

**输出（用户可见）**

> 本次得分 100 分，答对 1 题。当前唯一涉及的知识点是二叉树遍历（binary-tree-traversal），
> 准确率 100%，掌握度维持 100，无薄弱项。评估结论为无需调整计划（needReplan=false、
> changed=false），学习路径与掌握度均保持不变，可继续按原计划推进。

**结论**

1. 四项关键信息齐全：分数、题数、知识点准确率、是否调整计划——不是一段泛泛的鼓励话。
2. 分数与准确率来自工具返回，掌握度来自画像更新结果，**没有一项是模型自算**。
3. 结论明确给出"无需调整"及其依据（无未完成、无重复错误），可被追问。
4. 一条工作流内完成"判分 → 画像 → 重排判断"，构成赛题要求的评估闭环。

> 踩坑记录：首次运行时大模型 B 只接了 `request_replan` 的输出，于是回答"未包含得分"。
> 原因是**下游节点只拿得到直接上游的数据**；把 `grade`、`mastery`、`replan` 三个工具的
> 输出都显式接进大模型 B 后才正确。

---

## 已修的产品缺口

| 现象 | 根因 | 处理 |
|---|---|---|
| 出题后判分报 `NOT_FOUND` | 生成题目只返回、**不落库**，判分查不到 `answerKey` | `create_grounded_exercise` 现在写入 `exercises` 集合（含标准答案） |
| 判分用旧题号失败 | 旧生成结果未持久化 | 用种子题 `ex-demo-1`，或重新出题后取新 `llm-...` ID |

## 复现步骤

1. 种子：`profiles.mastery=42`、`exercises.ex-demo-1(answerKey=B)`、`plans.plan-demo(v1, 两任务 30 分钟)`
2. 重启 MCP（JSON 仓储启动时读盘）
3. 依次调用 A1 → A2 → A3 → A4 → A5，逐条比对输出
4. 若 A4 的 `changed=false`，说明计划已被之前的测试改过，重新种 `plan-demo` 再试
