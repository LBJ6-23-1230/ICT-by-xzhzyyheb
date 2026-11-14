# S5 Web Demo 首版：端到端跑通证据

| 项 | 值 |
|---|---|
| 页面 | `http://<ECS 公网 IP>:5000/demo`（网关托管单文件，无构建链） |
| 后端 | 阿里云 ECS：Flask 网关 :5000 + MCP :8443（同一份 SQLite） |
| 平台 | AgentArts 多智能体控制器 `agent-arts-90c91797…`（cn-southwest-2） |
| 模型 | 控制器路由 + 子工作流模型（平台侧） |

## 一、页面结构（三栏）

| 区域 | 数据来源 | 展示内容 |
|---|---|---|
| 对话 | `POST /api/v2/agent/chat`（SSE） | 用户气泡 + 助手回答（支持 **加粗** 与换行） |
| 执行时间线 | SSE 事件流 | 开始处理 → 交给 `wf-planner` → 完成 → 结束 |
| 证据 | `ready` 事件 + 上传接口返回 | 运行 ID、会话 ID、新上传资料的文件名与 documentId |

顶栏徽标来自 `GET /api/v2/agent/runtime`，显示 `云端 AgentArts` 与运行时名——
**不是写死的**，可据实反映"云端 / 降级"。

## 二、真实运行结果（截图 `planner-run.png`）

输入：`我今晚只有60分钟怎么安排`

回答：

```text
今晚已安排 1 个任务，共 45 分钟。
- 二叉树后序遍历：45 分钟，保留。
因预算不足延期：
- 图算法：40 分钟，延期。
- 哈希冲突：30 分钟，延期。
已安排 45 分钟，延期共 70 分钟。
```

时间线：

```text
开始处理   runId run-13fb61c3779e4098be70
交给 wf-planner   workflow 92ad3d57-84ec-460d-9909-bda7856c5a3d
wf-planner 完成
结束
```

## 三、这张图同时证明了四件事

1. **真的调了云平台**：徽标与运行时名来自 `/api/v2/agent/runtime`，不是前端写死。
2. **多 Agent 路由生效**：`subagent_started` 事件带 `workflowName=wf-planner`。
3. **确定性工具真的执行**：`fit_plan_to_budget` 返回"45 分钟排入、70 分钟延期"，
   与工具级证据（`s4-wf-planner.md` P2）一致。
4. **用户可上传资料**（截图 `upload-and-plan.png`）：上传 `06_TCP_notes.txt` 后，
   证据面板出现文件名与 `documentId`，且 SQLite 让 MCP 无需重启即可检索。

## 四、过程中修掉的三个平台兼容问题

| 现象 | 根因 | 修法 |
|---|---|---|
| 一连串空 `agent_message` | 平台把正文放在 `data.text`，答案在 **`data.summary`**（End 节点） | `_pick_text` 递归取 content/text/delta/summary，并把 End 节点 summary 转成 `agent_message` |
| 回复重复两三遍 | End 节点同时给 `message(summary)` 与 `workflow_end(output)` | 前端：长文本（>60 字）视为整段**替换**；后端：同样规则覆盖 `reply_parts` |
| 工具校验失败 `VALIDATION_ERROR` | 大模型 `raw_output` 带解释文字，`planText` 解析失败 | `parse_json_arg` 三级容错：纯 JSON → 去围栏 → 从长文本里抠完整 JSON |

另外记录了平台侧的一个坑：**MCP 节点里未映射的必填参数会被平台塞"默认值"，导致
`param xxx is not support default_value`**。修法是在工作流里给该参数设一个显式默认值
（本例 `availableMinutes=60`），或把它映射到开始节点入参。

## 五、已知限制

| 项 | 现状 | 后续 |
|---|---|---|
| 资料引用（`contentHash`/章节） | 工具返回里有，但 SSE 事件只带工具名与参数，不带结果 | 网关在 `tool_finished` 后再查一次检索接口，或让工作流把引用写进回答文本 |
| 回答是否流式 | End 节点整段返回，表现为"思考中… → 整段出现" | 若需逐字，需要在工作流末尾接一个流式输出的模型节点 |
| 公网暴露 | 5000 端口 http，无 TLS | 演示期可接受；正式版用 Caddy 反代 443 |
| 图片错题 | 未实现 | 见 `S5-数据入口与图片诊断方案.md` 方案 B（网关识别 → 文本 → 云端诊断） |

## 六、复现步骤

1. 服务器：`systemctl status zhixue-gateway zhixue-mcp`，两者均 active
2. 浏览器打开 `http://<IP>:5000/demo`，确认徽标为「云端 AgentArts」
3. 输入「我今晚只有60分钟怎么安排」，观察时间线出现 `wf-planner`
4. 点「上传讲义」选一个 `.md/.txt`，确认提示"已解析：xxx（N 个切片）"
5. 关闭页面重进，`GET /api/v2/agent/runs/<runId>` 仍可回放同一批事件
