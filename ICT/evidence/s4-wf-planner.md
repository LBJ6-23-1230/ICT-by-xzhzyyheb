# wf-planner 试运行证据

| 项 | 值 |
|---|---|
| 工作流 | `wf-planner`（学习规划） |
| 工具链 | `get_learning_context` → `fit_plan_to_budget` |
| 平台 | 华为云 AgentArts · 西南-贵阳一 |
| 数据 | `sessionRef=sref-demo-0000000000000001`；画像含 `binary-tree-traversal`(42)、`graph-algorithm`(55) |

> 规划类能力的关键不是"能生成计划"，而是**超预算时会不会说谎**。
> 所以这里分两层取证：工作流级证明端到端可跑，工具级证明裁剪规则确定成立。

---

## P1 工作流级：60 分钟预算

**输入**：`query="今晚只有60分钟怎么安排"`，`availableMinutes=60`，真实 sessionRef

**输出**（结束节点，模型整理后的自然语言 + 计划）：

> 今晚共安排 60 分钟、1 个任务，正好用完 60 分钟；没有任务被延期或压缩。
> 二叉树遍历：60 分钟（待完成）

**结论**

1. 端到端链路成立：`get_learning_context` → 大模型 A（生成候选计划）→ `fit_plan_to_budget` → 大模型 B（解释）。
2. 计划时长等于可用时长（60/60），未出现超出预算的排期。
3. 只有 1 个任务，是**模型生成候选计划时自行合并**的结果，见下方"已知偏差"。
4. 全链路可重复运行，工具节点耗时稳定在百毫秒级。

---

## P2 工具级：30 分钟预算装两个 30 分钟任务

**为什么单独用 curl 取证**：模型会提前删任务，绕过工具。裁剪规则必须脱离模型单独验证。

**请求**

```bash
curl -sk --max-time 15 -X POST https://<MCP地址>:8443/mcp \
  -H "Content-Type: application/json" -H "X-API-Key: <Key>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"fit_plan_to_budget","arguments":{
    "sessionRef":"sref-demo-0000000000000001",
    "availableMinutes":30,
    "planText":"{\"planId\":\"p-p2\",\"version\":1,\"tasks\":[
      {\"taskId\":\"t1\",\"knowledgePointId\":\"binary-tree-traversal\",\"durationMinutes\":30,\"status\":\"pending\"},
      {\"taskId\":\"t2\",\"knowledgePointId\":\"graph-algorithm\",\"durationMinutes\":30,\"status\":\"pending\"}]}"
  }}}'
```

**输出（节选）**

```json
{"ok": true, "data": {
  "plan": {
    "planId": "p-p2", "version": 1,
    "tasks": [
      {"taskId": "t1", "knowledgePointId": "binary-tree-traversal",
       "durationMinutes": 30, "status": "pending"}
    ],
    "unscheduledTasks": [
      {"taskId": "t2", "knowledgePointId": "graph-algorithm",
       "durationMinutes": 30, "status": "pending",
       "deferredReason": "预计需30分钟，当前剩余0分钟，暂未安排"}
    ],
    "constraints": {"availableMinutes": 30},
    "budgetSummary": {"baseTotalMinutes": 60, "allocationMode": "under_budget",
      "availableMinutes": 30, "scheduledMinutes": 30, "unallocatedMinutes": 0,
      "conflictMinutes": 0, "unscheduledMinutes": 30}
  },
  "changed": true
}, "errorCode": null}
```

**结论**

1. 60 分钟的任务量在 30 分钟预算下**只排 1 个**，另一个进入 `unscheduledTasks`。
2. 延期带有**具体原因**（"预计需30分钟，当前剩余0分钟"），不是无声丢弃。
3. `budgetSummary` 三数自洽：`scheduled 30 + unscheduled 30 = baseTotal 60`，`conflictMinutes=0`。
4. `changed=true`，说明裁剪确实改变了计划，而不是原样返回。

---

## 已知偏差（如实记录）

| 现象 | 原因 | 处理 |
|---|---|---|
| P1 只生成 1 个任务 | 大模型 A 在生成候选计划时自行合并/删减 | 提示词加"weakPoints 有几条就生成几条任务，不要提前删任务，超出预算由工具裁掉" |
| P1 说"原计划总时长就是 60 分钟" | 模型把 `budgetSummary.scheduledMinutes` 误读成原始时长 | 提示词限定"数值只能引用 budgetSummary 与 tasks 字段，不要推断原始时长" |

> 这两条不是缺陷隐藏，而是**调试过程的一部分**：模型倾向"替工具做决定"，需要靠提示词把职责边界划清。

## 复现步骤

1. 服务器 `curl /healthz` 确认 `tools=11`
2. P1：平台工作流试运行，`availableMinutes=60`，用真实 sessionRef
3. P2：按上面 curl 直接调工具，`availableMinutes=30`，候选计划含两个 30 分钟任务
4. 判定：P2 必须出现 `unscheduledTasks` 且带 `deferredReason`；否则裁剪规则失效
