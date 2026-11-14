# 知学 Mate｜华为 ICT 大赛创新赛（赛题 2）改造步骤

| 项目 | 内容 |
|---|---|
| 版本 | v1.1（排期按相对周次表述，不绑定日历日期） |
| 适用仓库 | `ICT-by-xzhzyyheb-main` |
| 适用赛题 | 赛题 2：基于华为云 AgentArts 智能体开发平台的 Agent 设计和应用 |
| 前置文档 | [改造方案](华为ICT-AgentArts改造方案-20261023.md)、[修改任务清单](华为ICT-AgentArts修改任务清单-20261023.md) |
| 本文定位 | 可执行的施工图：讲清每个阶段**改哪些文件、加什么接口、怎么验收**。方案文档回答"为什么"，本文回答"怎么做、做到什么算完" |

> 全文时间一律用 **第 N 周 / 阶段 / "×× 之前"** 表述，不指定具体日历日期；赛事节点（报名、初赛提交、答辩）的实际日期以官网公告为准。

---

## 0. 结论先行

**三条判定**

1. 现在的项目是"一个跑在自己电脑上的 AI 学习助手"，还不是"基于 AgentArts 的作品"。**代码中**检索 `agentarts` / 华为云调用：**0 处**；MCP 服务：**0 个**；平台侧智能体、工作流、知识库、评测、发布运行时：**全部为空**。
2. **不需要推倒重来。** 判分、排程、掌握度、计划差异这些确定性逻辑已经过测试（9 个工具 + 完整 pytest 套件），正确做法是**原样搬进 MCP 让平台调用**，而不是用大模型重写。真正要新写的是三样东西：**网关层、MCP 外壳、Web Demo 页面**。
3. 只有同时完成四件事才算"符合赛题"：控制面上云（AgentArts）、能力上 MCP、证据上 Trace 与评测、演示上 Web 页面。任何一件缺失，评委都能一眼看出是"套壳"。

**必做**

| # | 动作 | 一句话 |
|---|---|---|
| 1 | 编排权交给 AgentArts | 本地"意图识别 + 顺序调度"退化为降级通道，主链路走云上多智能体运行时 |
| 2 | 现有工具 MCP 化 | 一个自定义 MCP 服务承载 10 个核心工具（+1 加分），部署在公网 HTTPS |
| 3 | 补一个 Web Demo | 赛题原文写的是"可视化 Demo **页面**"，线上评审只会打开一个链接 |
| 4 | 留下可核验证据 | 平台 Trace、评测集基线/优化后对照、断链测试，而不是几张控制台截图 |

**不能做**

- 只在 AgentArts 控制台点一遍，主链路仍走本地后端（最常见、也最容易被当场问穿）。
- 把"本地规则兜底"的结果包装成"云端 AgentArts 返回"。
- 把华为云 AK/SK 或长期 Token 放进 App、HAP、前端 JS、仓库、截图。
- 把 MCP 部署在内网或本机（平台侧访问不到，工具永远"调不通"）。
- 用 HAP 安装包、本地 pytest 通过、旧赛事材料来证明赛题完成度。

**工作量**：约 22 人日。3 人并行约 3 周，可在**报名截止与初赛材料提交前**完成"能演示 + 有平台证据"的版本；总决赛前再做知识库扩充与评测打磨。

---

## 1. 现状盘点（全部可查证）

| 维度 | 当前实现 | 证据位置 |
|---|---|---|
| 客户端 | HarmonyOS/ArkTS 20 个页面，聊天、计划、练习、错题、知识库、轨迹齐全 | `app/entry/src/main/ets/pages/`、`main_pages.json` |
| 聊天链路 | `AgentBridge.callAgent` 直连 `POST /api/agent/chat`，**单次 JSON，非流式** | `services/AgentBridge.ets:173-268` |
| 意图识别 | 8 类意图，LLM + 关键词双通道 | `app/agent/chat_llm.py:706`、`:683` |
| 编排 | 自研：`_HANDLERS` 分发 + 工作流步骤机 | `chat_llm.py:1138`、`app/api/workflows.py:238` |
| 工具 | 9 个已注册工具，含权限白名单 | `app/runtime/tool_registry.py:74-89` |
| 业务规则 | 优先级五因子、60 分钟预算裁剪、逐知识点判分、掌握度增量、重排、计划差异 | `app/decision/`、`app/tools/` |
| 知识库 | 自研解析（txt/md/pdf/docx/doc）、切片 800 字/重叠 80、关键词+语义混合检索 | `app/agent/knowledge.py:315`、`:505` |
| 数据 | JSON/SQLite 双后端，Repository 抽象 + 事务 + CAS | `app/repositories/` |
| 凭证 | 仅 DashScope（通义千问）API Key，在服务端 `.env` | `.env.example` |
| 契约 | `api-contract-v0.3`，OpenAPI + 前端镜像 + fixtures | `contracts/openapi.json`、`app/contracts/` |
| 云上资产 | 无 | 仓库检索无 agentarts 代码 |

**差距一句话**：项目**有内容、有逻辑、有数据**，但**没有平台**。赛题考的不是"你能不能做出学习助手"，而是"你能不能在 AgentArts 上把智能体设计、工具编排、知识接入、多 Agent 协作、效果评估这五件事做出可见的工程闭环"。

---

## 2. 赛题要求逐条对照

| 赛题原文要求 | 现在有什么 | 还差什么 | 对应步骤 |
|---|---|---|---|
| 基于华为云 AgentArts 平台完成智能体的**构建、编排与部署** | 自研 Python 编排，在开发机跑 | 平台侧控制器 + 能力工作流 + 已发布运行时；网关真实调用 | S1、S2、S4 |
| 通过**可视化 Demo 页面**展示核心功能与交互流程 | HarmonyOS App 页面（需装 HAP） | 浏览器可打开的 Web 页面，能看到 Agent 路由、工具、引用 | S5 |
| 体现**智能体设计**的使用价值 | 5 个薄 Agent 类（`run(state)` 包装） | 平台上的角色定义、提示词、输出结构、路由边界 | S4 |
| 体现**工具编排**的使用价值 | 9 个本地工具，进程内调用 | 自定义 MCP 服务 + 平台侧工具选择与参数填充 | S3、S4 |
| 体现**知识接入**的使用价值 | 自研切片与检索，引用带 `chunkId/fileName/headingPath` | AgentArts 知识库 + 回答带来源 + 隔离/删除验证 | S4 |
| 体现**多 Agent 协作**的使用价值 | 自研 handoff 记录（`sender → receiver`） | 平台多智能体控制器的真实转交与 Trace | S4 |
| 体现**效果评估**的使用价值 | 只有单元测试/接口测试 | 评测集 + 评估器 + 基线/优化后指标对照 | S6 |
| 形成"场景识别→架构设计→技能编排→调试优化→部署上线"闭环 | 有开发记录，但无平台侧调试与上线 | 版本化发布记录、调试失败样例、上线地址与运维 | S4、S7 |
| 云服务资源（MaaS 等）支持 | 用第三方 DashScope | 改用华为云 MaaS 模型（或平台内置模型），体现云资源使用 | S1、S4 |
| 入围后社区分享帖 | 无 | 方案说明、知识库构建、MCP 说明、操作指引、Demo 视频 | S8 |

---

## 3. 目标形态与四个关键决定

```text
评委浏览器（Web Demo，主演示面）        HarmonyOS App（差异化客户端，可选）
        │  HTTPS + SSE                        │  HTTPS + 事件轮询
        └───────────────┬──────────────────────┘
                        v
        ┌───────────────────────────────────────────────┐
        │ 知学网关（现有 Flask 后端演进）                 │
        │ 身份/会话映射 · 输入适配 · 审计 · 事件归档      │
        │ 凭据：IAM Token 仅存服务端，缓存 24h            │
        └──────┬─────────────────────────────┬──────────┘
               │ runAgent（SSE，必须流式）    │ JSON-RPC over HTTPS
               v                             v
    ┌──────────────────────────┐   ┌─────────────────────────────┐
    │ 华为云 AgentArts          │   │ 自定义 MCP 服务（公网 HTTPS）│
    │  · 多智能体控制器         │◄─►│  · 10 个核心工具             │
    │  · 4 条能力工作流         │工具│  · 复用现有业务函数，不改规则 │
    │  · 3 条主场景闭环         │调用│  · 身份：API Key + sessionRef │
    │  · 知识库（课程资料）      │   │  · 数据：现有 Repository      │
    │  · Trace / 智能体评估      │   └─────────────────────────────┘
    └──────────────────────────┘
```

| 决定 | 选择 | 理由 |
|---|---|---|
| 控制面放哪 | **AgentArts 多智能体应用**（`type=controller`），本地编排降级 | 评委看的是平台资产与 Trace；本地代码再优雅也不算"基于平台" |
| 能力怎么给 | **一个自定义 MCP 服务装 10 个核心工具（+1 加分）** | 平台限制单个应用最多添加 5 个 MCP 服务；聚合成 1 个既不触限，也让"工具编排"集中可见 |
| 主演示面 | **Web 页面**（网关直出静态页，不加 Node 构建链） | 赛题写的是"Demo 页面"；线上初赛/答辩只给链接，不能让评委装 HAP |
| App 怎么办 | **保留**，作为差异化客户端，但降到第二位 | 已有 20 个页面的投入不浪费；分布式/卡片是加分项，不是主线 |

---

## 4. 施工步骤

> 每个步骤都给出：**改什么 → 怎么改 → 验收标准 → 留什么证据**。上一步验收不过，不要开下一步。

### S0 冻结基线、清理口径（约 0.5 人日）

**改什么**

1. 在干净环境跑一次全量测试，把结果落盘为迁移基线：
   ```powershell
   cd server/zhixue-agent-server
   .venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --junitxml=../../evidence/ict-baseline-v1.xml
   cd ../..
   python tools/diff_contracts.py
   ```
2. 检索并清除旧赛事口径（若还有残留）：`C4`、`鸿蒙赛道`、`最对口`、旧比赛名、旧模板文件名。
3. 确定并统一：作品名、队名、3 名队员、指导教师、社区发布账号。这些会出现在包名/README/演示页/视频里，**中途改名成本极高**。

**验收**：`evidence/ict-baseline-*.xml` 存在且用例数与改动前一致；`diff_contracts.py` 返回 0；仓库全文检索旧赛事关键词无命中。

---

### S1 华为云开通与最小权限（约 1 人日，含外部等待）

**前提（真正的阻塞项，第一天就办）**

1. 华为账号实名认证 → 官网报名创新赛 → 队伍审核通过 → 领取代金券。
2. **申请 AgentArts 公测**（审核约 5 个工作日）→ 审核通过后「免费开通基础版」（勾选服务声明即可，限时免费、默认 1 个月自动续期）。

> 也就是说：公测审核约 5 个工作日，通过后才能开始建资产。**代码可以并行写，但平台操作不能等**；若拖到第 2 周才提交申请，S4 只剩不到两周，风险极高。

**做什么**

1. 开通 AgentArts 服务，进入控制台 `https://console.huaweicloud.com/versatile/`，区域只能用 **华北-北京四（cn-north-4）**（目前仅该区域有文档与端点），工作空间用默认 `default`。
2. 创建 **IAM 子用户**（不要用主账号长期密钥）：只授予 AgentArts 使用权限 + 模型服务权限；开启 MFA。
3. 完成三项依赖授权（不做会卡在节点/插件/模型上）：

   | 依赖 | 什么时候需要 | 在哪授权 |
   |---|---|---|
   | FunctionGraph | 工作流用「代码节点」「函数类型插件」 | 首次使用时按提示授权 |
   | ModelArts Studio（MaaS） | 调用平台预置模型（免费 200 万 token 用尽后） | 首次使用时按提示授权 |
   | OBS | 知识库通过 OBS 接入文档、存储附件 | 开通 OBS 并建桶 |

4. **基础版配额要提前算清**（够用，但要有数）：智能体 API 调用 **500 次/天**、应用 30 个、团队成员 3 人、团队空间 1 个、赠送模型 token 200 万、Trace 留存 **15 天**。评测与联调都吃调用次数，别在演示前一晚把额度跑光。
5. 记录 6 个值到 `server/zhixue-agent-server/config/agentarts.json`（**只放 ID 和端点，不放密钥**）：

   | 参数 | 从哪拿 |
   |---|---|
   | `endpoint` | API 参考「终端节点」页；主机形如 `<project_id>.studio.agentarts.cn-north-4.huaweiapaas.com`，**以控制台为准** |
   | `project_id` | 控制台「我的凭证」/ API 参考「获取项目 ID」 |
   | `workspace_id` | 默认 `default`；多空间时用「获取工作空间 ID」 |
   | `agent_id` | 开发中心 → 智能体管理 → 智能体卡片「复制 ID」 |
   | `workflow_id[]` | 工作流应用卡片「复制 ID」 |
   | `version` | 智能体「版本历史」里的发布版本号 |

6. **MCP 公网入口的域名与证书**（这一步容易卡住，提前决策）：

   | 方案 | 说明 | 风险 |
   |---|---|---|
   | A. 已有备案域名 | 最省事，直接解析到 ECS | 多数学生团队没有 |
   | B. 香港/海外节点 ECS | 免 ICP 备案，HTTPS 可用 | 延迟略高；代金券是否覆盖需确认 |
   | C. 大陆 ECS + 新备案 | 合规最稳 | 备案约 1–3 周，可能赶不上初赛材料提交 |

   > 平台要求 MCP 地址是**标准 URL、公网可达、HTTP/HTTPS**，内网 IP 默认不可用。**不要**指望用 `127.0.0.1` 或局域网 IP 打通。

**验收**：公测审核通过、基础版已开通、三项依赖授权完成；能取到 IAM Token；`config/agentarts.json` 已填；域名方案已定并有可用 HTTPS 地址（可先指向一个返回 200 的空服务）。

**证据**：控制台开通截图（脱敏）、Token 获取成功的 curl 输出（**不要贴 Token 全文**）。

> 取 Token（有效期 24 小时，服务端必须缓存复用）：
> ```bash
> curl -X POST https://iam.cn-north-4.myhuaweicloud.com/v3/auth/tokens \
>   -H 'Content-Type: application/json' \
>   -d '{"auth":{"identity":{"methods":["password"],"password":{"user":{"name":"<IAM用户名>","password":"<密码>","domain":{"name":"<账号名>"}}}},"scope":{"project":{"name":"cn-north-4"}}}}'
> # 响应头 X-Subject-Token 即 Token；后续请求带 X-Auth-Token: <Token>
> ```

---

### S2 后端：新增 AgentArts 网关（约 3 人日）

**目的**：让 App / Web 只认自己的网关，网关替它们带云凭据、管会话、翻译流式事件、落审计。**云凭据绝不下发客户端。**

**新增文件**（`server/zhixue-agent-server/`）

| 文件 | 职责 | 关键点 |
|---|---|---|
| `app/agentarts/__init__.py` | 包导出 | — |
| `app/agentarts/config.py` | 读取环境变量、校验必填 | 缺失时返回 `mode=disabled` 并给出原因，**不静默降级** |
| `app/agentarts/auth.py` | `IamTokenProvider.get()` | 缓存 24h，提前 10 分钟刷新；并发下只刷新一次 |
| `app/agentarts/client.py` | `run_agent(...) -> Iterator[dict]`、`run_workflow(...)` | 解析 SSE；超时/重试/错误码翻译 |
| `app/agentarts/events.py` | `normalize(platform_event) -> internal_event` | 未知事件透传为 `raw`，不丢弃 |
| `app/agentarts/session.py` | 生成 `conversation_id`、`sessionRef`，维护映射 | 见下方"会话与身份" |
| `app/api/agentarts_api.py` | 蓝图 `/api/v2/agent/*` | 鉴权、限流、断链保护 |
| `tests/test_agentarts_client.py`、`test_agentarts_gateway.py`、`test_agentarts_degradation.py` | 契约/断链测试 | 断链必须返回 502 且带 `fallback:false` |

**新增端点**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v2/agent/chat` | 主链路，`text/event-stream` 流式返回 |
| GET | `/api/v2/agent/runs/<run_id>` | 事件回放（App 轮询 / 页面刷新后仍能看到时间线） |
| GET | `/api/v2/agent/runtime` | 返回 `{mode, agentId, version, reachable}`，供 Demo 显示"云端/降级"徽标 |
| POST | `/api/v2/agent/feedback` | 收集"有用/无用 + 备注"，直接沉淀为评测样本 |

**事件映射**（平台 → 内部 → 前端）

| 平台 `event` | 内部事件 | 前端表现 |
|---|---|---|
| `start` | `run_started` | 显示"正在思考"、锁定输入 |
| `message` | `agent_message`（`delta`） | 逐字流式输出 |
| `plugin_start` | `tool_started`（`name`、`arguments`） | 时间线出现"调用 xxx 工具" |
| `plugin_end` | `tool_finished` | 工具条目打勾 + 耗时 |
| `statistic_data` | `metrics`（`latency.plugin/model/overall`） | 折叠区显示耗时 |
| `summary_response` | `run_summary`（全量文本） | 作为最终消息落库 |
| `done` | `run_finished` | 解锁输入、写 Trace 记录 |

**会话与身份**（这是最容易被问穿的地方，必须做对）

- 平台的 `conversation_id` 只允许 1–64 位 `[A-Za-z0-9-_]`，**不能直接塞本地 `userId`**（本地可能是中文昵称、手机号）。做法：网关生成 `uuid4().hex`（32 位）作为 `conversation_id`，存 `agentarts_sessions` 集合：`{conversationId, userId, chatSessionId, agentId, sessionRef, createdAt, lastActiveAt}`。
- `sessionRef` 是**一次性随机引用**，通过 `runAgent` 的 `inputs` 传给智能体，再由智能体作为 MCP 工具的首个参数回传。MCP 侧用 `sessionRef → userId` 换真实身份，**模型无法凭空伪造别人的 `sessionRef`**，也不会出现"模型自己填 userId"的越权面。
- 单次调用示例（智能体应用**只支持流式**）：
  ```bash
  curl -N -X POST "https://<endpoint>/v1/<project_id>/agents/<agent_id>/conversations/<conversation_id>?workspace_id=default" \
    -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' \
    -H 'X-Invoke-Mode: published' -H 'stream: true' \
    -d '{"query":"明天交数据结构作业，今晚只有60分钟","inputs":{"sessionRef":"<一次性会话引用>"}}'
  ```

**新增环境变量**（追加到 `.env.example`，真实值只在本机 `.env`）

```
AGENTARTS_ENABLED=1
AGENTARTS_MODE=cloud            # cloud | local（local 仅作降级演示）
AGENTARTS_ENDPOINT=
AGENTARTS_PROJECT_ID=
AGENTARTS_WORKSPACE_ID=default
AGENTARTS_AGENT_ID=
AGENTARTS_AGENT_VERSION=
AGENTARTS_IAM_DOMAIN=
AGENTARTS_IAM_USER=
AGENTARTS_IAM_PASSWORD=          # 或改用 AK/SK，二选一
AGENTARTS_TIMEOUT_SECONDS=120
AGENTARTS_INVOKE_MODE=published
MCP_SERVICE_BASE_URL=
MCP_SERVICE_API_KEY=
```

**改现有文件**

- `app/__init__.py`：注册 `agentarts_api` 蓝图；`/health` 增加 `agentarts: {mode, reachable, agentId}`；`_DEFAULT_CORS_ORIGINS` 增加 Demo 域名。
- `app/api/chat.py:294`：保留 `/api/agent/chat` 但明确标注"**本地降级通道，不作为比赛主链路**"，响应加 `source: "local"`，避免含混。
- `requirements.txt`：新增 `mcp`、`httpx`、`pyjwt`（版本以 `requirements-lock-py312.txt` 复验为准）。
- `run.py`：启动时打印 AgentArts 配置状态与是否可达（**只打 ID 与模式，不打密钥**）。

**验收**

1. `curl -N` 调 `/api/v2/agent/chat`，能看到逐字输出与工具事件。
   > 公测未通过前没有真端点：先用一个本地假 SSE 服务（按上面 7 类事件回放）验证解析器与前端渲染，**这个假服务只用于开发，不得出现在演示环境**。
2. 断开 AgentArts（改错 `agent_id` 或断网）→ 返回 502 + `fallback:false` + 明确错误码；**不得**自动返回本地结果还标"云端"。
3. `pytest tests -q` 全绿，其中 `test_agentarts_degradation.py` 必须覆盖断链。

**证据**：SSE 原始流日志（脱敏）、断链测试输出、`/health` 返回。

---

### S3 后端：MCP 服务化（约 4 人日，最关键）

**为什么非做不可**：赛题点名"工具编排"，平台侧只能通过 MCP（或插件）看到你的工具。工具留在 Python 里自己调，评委会认为你没用平台能力。

**新增目录** `server/zhixue-agent-server/mcp/`

```text
mcp/
  __init__.py
  server.py          # MCP 服务入口：streamable-http，路径 /mcp，API Key 校验
  auth.py            # sessionRef → userId；API Key；幂等键
  envelope.py        # 统一响应 {ok, errorCode, message, data, traceId, version}
  tools/
    context.py       # get_learning_context
    materials.py     # search_learning_materials
    evidence.py      # get_learning_evidence
    priority.py      # calculate_learning_priority
    budget.py        # fit_plan_to_budget
    plan.py          # save_learning_plan
    exercise.py      # create_grounded_exercise
    grading.py       # grade_exercise
    mastery.py       # update_mastery
    replan.py        # request_replan
    diff.py          # get_plan_diff
  contracts/tools.json   # 工具契约（名称/入参/出参/示例/权限），供平台导入与文档引用
  README.md              # 工具清单 + 调用示例 + 错误码
```

**工具映射表**（**复用现有函数，禁止重写业务规则**）

| # | 工具名 | 复用什么 | 关键入参 | 写操作 | 说明 |
|---|---|---|---|---|---|
| 1 | `get_learning_context` | `services/learning_context.snapshot`、`ensure_chat_context` | `sessionRef` | 否 | 目标、课程、DDL、可用时间、画像版本 |
| 2 | `search_learning_materials` | `agent/knowledge.search(chunks, query, top_k=5, …)`、`services/grounded_exercises.retrieve` | `sessionRef, query, topK` | 否 | 必带 `chunkId/fileName/headingPath/contentHash` |
| 3 | `get_learning_evidence` | 错题本 + `profiles.mastery` + `assessments` | `sessionRef, point?` | 否 | 区分"事实/证据不足" |
| 4 | `calculate_learning_priority` | `decision/priority.calculate_learning_priority` | `points[], context` | 否 | 五因子明细一并返回，便于解释 |
| 5 | `fit_plan_to_budget` | `tools/time_budget.fit_plan_to_budget` | `plan, availableMinutes, prioritizePoint?` | 否 | 返回 `{plan, changed}`，未排入项保留 |
| 6 | `save_learning_plan` | 新写（复用 `plans` 集合与 `create_plan_history`） | `sessionRef, plan, expectedVersion, idempotencyKey` | **是** | 版本校验冲突返回 `CONFLICT` |
| 7 | `create_grounded_exercise` | `services/grounded_exercises.generate` + `tools/exercise_tools.select_exercises` | `sessionRef, point, count, sources[]` | 否 | 无资料时返回 `INSUFFICIENT_MATERIALS`，不编造 |
| 8 | `grade_exercise` | `tools/assessment_tools.grade_exercise` | `answers[], exerciseSetId` | 否 | **标准答案只在服务端读取，绝不进入工具出参** |
| 9 | `update_mastery` | `tools/assessment_tools.update_mastery` + `persist_mastery` | `sessionRef, perPoint[]` | **是** | 逐知识点更新，带 evidenceId |
| 10 | `request_replan` | `tools/plan_tools.should_replan` + `replan_learning_path` + `services/assessment_replan` | `sessionRef, assessment` | **是** | 只提重排请求，不在评估步骤里偷改计划 |
| 11 | `get_plan_diff` | `tools/plan_tools.build_plan_diff` | `planId, oldVersion, newVersion` | 否 | 加分项；供前端展示"保留/压缩/延期" |

> 10 个核心工具（1–10）+ 1 个加分工具（11）。6 个是门槛，**10 个是拿分线**；读、算、写三类都要有，才撑得起"工具编排"。

**统一响应信封**（所有工具都返回它，前端与评测才可机器判读）

```json
{"ok": true, "data": {...}, "errorCode": null, "message": null, "traceId": "...", "version": "mcp-1.0"}
```

错误码固定：`UNAUTHORIZED` / `FORBIDDEN` / `NOT_FOUND` / `VALIDATION_ERROR` / `CONFLICT` / `INSUFFICIENT_MATERIALS` / `TOOL_TIMEOUT` / `INTERNAL_ERROR`。

**身份与会话**

- MCP 服务用 **API Key** 校验调用方是 AgentArts（平台创建 MCP 时可选"API Key"鉴权），Key 只在服务端。
- 每个工具的**第一个参数固定是 `sessionRef`**；`auth.py` 查表换 `userId`，查不到一律 `UNAUTHORIZED`。
- 写操作必须带 `idempotencyKey`，重复调用返回同一结果（现有 `submit` 已有此模式，照抄）。

**部署与网络**

1. `Dockerfile`（Python 3.12 + 依赖）+ `docker compose`（网关 + MCP）。
2. Nginx/Caddy 反代：`https://mcp.<域名>/mcp`，开 TLS，关闭缓冲以支持流式。
3. 健康检查 `GET /healthz`；日志脱敏（不记资料全文、不记 token、不记手机号）。

**平台侧接入**（控制台操作）

1. 开发中心 → MCP → 创建 MCP → 选 **streamableHttp**（推荐，协议官方推荐方式）或 SSE → 填 `https://mcp.<域名>/mcp` → 鉴权选 API Key → 测试工具能自动发现 11 个（10 核心 + 1 加分）。
2. 在智能体"技能 → MCP 服务"里"添加全部"（**单应用 ≤5 个 MCP 服务**，所以聚合成 1 个正合适）。
3. 为工具配置输入参数，与"为应用添加记忆/变量"结合，把 `sessionRef` 自动注入。

**验收**

1. 用 `curl` 直连 MCP 能跑通"读上下文 → 排程 → 存计划 → 查差异"四条链路。
2. 平台"工具调试"里能看到真实业务数据（不是 mock）。
3. 越权测试：拿 A 的 `sessionRef` 查不到 B 的数据；伪造 `sessionRef` 返回 `UNAUTHORIZED`。
4. 重复提交同一 `idempotencyKey` 不产生第二条计划。

**证据**：11 个工具的调用记录（入参出参脱敏）、平台工具列表截图、越权测试输出。

---

### S4 云上：知识库 + 能力工作流 + 控制器（约 4 人日）

**4.1 知识库（基础版只允许 1 个，语料要一次选对）**

- 平台限制（基础版）：**每个应用最多关联 1 个知识库**、租户共 5 个、单文档 ≤20 MB、文件类型仅 **doc / pdf / pptx / xlsx / csv**。
  > 现有本地解析支持 `md/txt`，**平台不支持**，上传前必须转成 docx 或 pdf，否则解析直接失败。
- 语料：3–5 份脱敏讲义（数据结构/高数/英语），**不要放个人信息、未授权教材扫描件**。
- 配置：向量模型 `pangu_embedding`、精排模型 `pangu_rerank`；拆分配置按"长度分段"（800 字左右、重叠 80，与现有本地切片保持一致，便于对照）；开页眉页脚/目录页解析，扫描件开 OCR 增强。
- 检索：混合检索 + 相关度阈值 + topk，建完先跑"命中率测试"，把命中/不命中的问题记下来，这本身就是调试证据。
- **引用能力的真实边界（别过度承诺）**：平台"查看来源"给的是**上下文内容 + 文件名称**，没有内联角标，也不保证给出章节号。要展示"文件 + 章节 + 片段"，必须由 MCP 的 `search_learning_materials` 返回 `headingPath`，前端合并展示。

> 职责划分写清楚，别自己打自己：**AgentArts 知识库 = 回答依据与"知识接入"证据；MCP 检索 = 出题依据与精确版本校验**（`chunkId`/`contentHash`）。

**4.2 四条能力工作流（"子 Agent"的真实落地形态）**

> ⚠️ 平台的多智能体应用里，**子节点只能是"工作流"或"多智能体"**（各 ≤30 个，最多 2 级嵌套），**不能直接挂单智能体**。所以"诊断/规划/练习/评估 4 个子 Agent"必须落成 **4 条能力工作流**——这既符合平台模型，也更好单独调试与评测。

每条工作流的通用骨架：`开始（入参含 sessionRef）→ 知识检索 → 大模型节点（角色提示词 + 结构化输出）→ MCP 工具节点 → 判断/分支 → 输出`。

| 工作流 | 角色提示词要点 | 必挂节点 | 输出（结构化） |
|---|---|---|---|
| 学情诊断 | 只陈述有证据的薄弱点；证据不足要明说并建议短测；不得编造掌握度 | 知识检索、`get_learning_evidence`、`create_grounded_exercise` | `{weakPoints[], confidence, evidenceIds[], needsShortTest}` |
| 学习规划 | 处理 DDL、可用时间、优先级与取舍；超预算必须显式"延期" | `get_learning_context`、`calculate_learning_priority`、`fit_plan_to_budget`、`save_learning_plan` | `{plan, budgetSummary, deferred[], reasons[]}` |
| 练习辅导 | 只基于检索到的资料出题；资料不足时明确标"通用生成" | 知识检索、`search_learning_materials`、`create_grounded_exercise` | `{exercises[], sources[]}` |
| 学习评估 | **不得自行给分**，必须调用确定性判分；解释可生成，分数只能来自工具 | `grade_exercise`、`update_mastery`、`request_replan` | `{score, perPoint[], needReplan, reasons[]}` |

可选加分项：把每条工作流再包一层"单智能体"用于单独对话调试与评测（平台支持在单智能体中嵌入工作流）。时间不够就跳过，**不要为了凑数量把同一条流程复制两遍**。

**4.3 多智能体控制器**

- 结构：多智能体画布 → 起始节点是「多Agent控制器」→ 挂 4 条能力工作流；如需再分层，可在其中一条下挂 1 个嵌套多智能体（≤2 级）。
- 路由：**「模型配置」和「意图识别工作流」至少填一个，且优先调用意图识别工作流**。建议写一条 6–10 节点的意图识别工作流（明确目标 / 有资料问知识点 / 要练习 / 要判分 / 改时间 / 闲聊），比纯模型路由稳定得多。
- 子工作流执行动作：**继续 / 终止 / 等待输入**——"等待输入"就是赛题要的澄清闭环，必须用上。
- 全局意图：直接应答（闲聊、越界问题）或流程跳转（用户中途改口径）。
- 高级配置：起始工作流、默认工作流（意图不匹配时的兜底）、结束工作流、最大对话历史轮次（10）、最大跳转次数（默认 9，**设小一点防死循环**，建议 6–8）。
- 对外调用 `type=controller`；简单查询不要五个角色全跑，评委一眼就能看出是硬编码流程。

**4.4 三条主场景闭环（用控制器 + 跳转 + 等待输入组合出来）**

| 闭环 | 实际实现方式 | 必须验证的分支 |
|---|---|---|
| 目标建立与澄清 | 控制器识别"目标类"意图 → 规划工作流；缺期限/可用时间 → 执行动作"等待输入" → 补参后跳回 | 缺参等待、用户取消 |
| 证据不足闭环 | 诊断工作流 → 证据不足 → 练习工作流出短测 → 用户作答（等待输入）→ 评估工作流 → 跳回诊断 | 无资料、作答超时 |
| 约束变化重排 | 规划工作流读旧计划 → 时间/DDL 变更 → 排程 → 输出差异 → 用户确认（等待输入）→ 提交新版本 | 版本冲突、用户拒绝 |

**4.5 发布**

- 每个智能体/工作流发布为 API，记录 `agent_id`、`workflow_id`、`version`、发布时间。
- 控制器整体发布一次，网关固定 `X-Invoke-Mode: published` 调用发布版本，**调试版本只用于本地排障**。

**验收**：控制台"试运行"能逐节点看到意图识别、知识检索、工具调用；四条能力工作流在**运营运维 → 观测 → 调用链管理**里都有职责清晰的 Span（含耗时）；三条闭环的异常分支各跑通一次。

---

### S5 Demo：Web 页面 + App 改造（约 4 人日）

**5.1 Web Demo（优先做，这是评委唯一一定会打开的东西）**

- 形态：静态页（HTML + 原生 JS + `fetch` 流式读取），由网关 `GET /demo` 直出，**不引入 Node 构建链**，减少部署面。
- 页面结构：

  | 区域 | 内容 |
  |---|---|
  | 左：会话 | 用户输入、流式回复、目标卡片 |
  | 中：执行时间线 | 当前由哪个智能体处理、调用了哪个工具、耗时、成功/失败 |
  | 右：证据面板 | 知识引用（文件名/章节/片段）、计划差异（保留/压缩/延期）、Trace 链接 |
  | 顶栏 | `云端 AgentArts` / `本地降级` 徽标、`agentId + 版本`、运行 ID |

- 必须有"失败也看得见"的设计：断链、工具超时、证据不足、等待用户输入，各自有明确样式。**只会演示成功路径的作品，一问就露。**

**5.2 App 改造**（只动 8 个文件，页面结构不重做）

| 文件 | 改动 |
|---|---|
| `api/ApiDefaults.ets` | `CONTRACT_VERSION` → `api-contract-v0.4` |
| `api/AgentApiClient.ets` | 新增 `startAgentRun` / `getAgentRun` / `getAgentRuntime`；SSE 不便时改为**事件轮询** `GET /api/v2/agent/runs/{id}?after=N` |
| `services/AgentBridge.ets` | 新增 `callCloudAgent`；保留 `callAgent` 作降级，并在 UI 标注来源 |
| `models/ChatModels.ets` | `AgentResponse` 增 `runId/agentRole/toolCalls/citations/runtimeVersion/fallback` |
| `api/ApiResponseValidator.ets` | 增加 `/api/v2/**` 校验分支 |
| `utils/ApiDisplayFormatter.ets` | 增加工具名、工作流名、Span 类型的中文映射 |
| `pages/ChatMain.ets`、`pages/AgentTrace.ets` | 渲染智能体徽标、工具 chips、引用卡片、耗时与运行 ID |

**5.3 契约同步**（顺序不能反，否则前端白改）

1. 先改 `contracts/openapi.json`：新增 v2 端点与 schema（`AgentRunStartRequest`、`AgentRunEvent`、`Citation`、`ToolCall`、`AgentRunSummary`），`info.version` → `api-contract-v0.4`。
2. 同步 `app/contracts/openapi.json` 镜像与 `contracts/fixtures/`（新增 3 个：流式事件序列、引用、降级响应）。
3. 跑 `python tools/diff_contracts.py`，必须 0 差异。
4. 更新 DTO 与页面渲染，最后补 `ApiResponseValidator` 分支。
5. 顺手修 `contracts/README.md` 里已失效的引用（`tools/validate_contract.ps1`、`backend/v1_routes.py` 已不存在）。

**5.4 演示前的"去假"处理**（不做会被现场问穿）

- App「离线体验/Fixture」入口：默认关闭，或改名为"离线演示（非云端）"，并保证评委用的演示路径不经过它。
- 错题页的"模拟 AI 识别"（`LocalAgentService.analyzeWrongQuestion` 按 `imageUri.length % 4` 选答案 + 1.2 秒假延迟）：演示设备上**不可达**，或明确标注"演示占位"。
- `LearningFlowData.ets` 里的 8 步演示脚本：不在演示路径出现。

**验收**：陌生人在浏览器输入 URL，3 分钟内能走完"提目标 → 诊断 → 出题 → 判分 → 计划变更"；每一步都能指出"这是哪个智能体、调了哪个工具、依据哪份资料"。

---

### S6 效果评估（约 2 人日）

**评测集**：≥30 条，覆盖四类能力 + 异常。**双轨评测**，不要只做一条：

- **平台轨（语义质量）**：平台评测集只有 `input` + `reference_output` 两列，适合评"答得好不好"。
- **本地轨（工程正确性）**：网关记录每次运行的事件流，用脚本断言"路由到哪条工作流、调了哪些工具、入参是否满足硬约束"——这是平台评测器给不了的，也是评委最认的部分。

```json
{"id":"q-001","input":"明天交数据结构作业，今晚只有60分钟","reference_output":"给出总时长≤60分钟的二叉树复习计划，含取舍说明","assert":{"route":"规划","tools":["get_learning_context","fit_plan_to_budget"],"maxMinutes":60}}
{"id":"q-002","input":"二叉树后序遍历还是不懂","reference_output":"指出证据不足并提出短测，引用讲义章节","assert":{"route":"诊断","tools":["get_learning_evidence"],"mustCite":true}}
{"id":"q-014","input":"帮我安排下周复习，但我不知道每天能学多久","reference_output":"先追问每天可用时间，不直接给计划","assert":{"route":"控制器","behavior":"ask_clarification"}}
```

**指标与目标**

| 指标 | 定义 | 目标 | 由谁算 |
|---|---|---|---|
| 路由准确率 | 进入正确能力工作流的比例 | ≥90% | 本地脚本 |
| 工具选择准确率 | 期望工具被调用的比例 | ≥85% | 本地脚本 |
| 参数正确率 | 工具入参可执行且满足约束 | ≥95% | 本地脚本 |
| 计划约束满足率 | 时长/DDL 不被违反 | 100%（硬约束） | 本地脚本 |
| 知识有据性 | 带引用且引用确实支持结论 | ≥90% | 平台评估器 + 人工抽样 |
| P95 延迟 | 单次端到端 | <15s | `statistic_data` / 调用链 |
| 单次成本 | token 费用 | 记录并对比 | 控制台 |

**做法**：先跑**基线**并**保留失败样本**；然后**一次只改一类**（提示词 / 工具描述 / 知识分块 / 工作流跳转），复测出对照表。

> 额度提醒：基础版 **500 次/天** 调用，一轮 30 条就是 30 次，基线 + 两轮优化 ≈ 100 次，够用但别反复全量重跑。平台评估器自身消耗的 token 由模块专用资源承担，不占赠送的 200 万 token。

**验收**：报告里既有提升数据，也有失败边界（哪类问题仍然答不好、为什么）；失败样本本身要能复现。

---

### S7 部署与演练（约 2 人日）

- 网关与 MCP 用 compose 部署到 ECS，配域名 + TLS + 健康检查 + 预算告警。
- 冻结版本：记录 `agent_id/workflow_id/version/镜像 tag/MCP tag`，写进 `evidence/deploy-<版本>.md`。
- **把关键 Trace 立刻归档**：进「运营运维 → 观测 → 调用链管理」，按 `会话ID/链路ID` 筛选后导出（截图 + 字段表 + 耗时），存 `evidence/trace/<版本>/`。基础版**只保留 15 天**，而答辩通常在材料提交后数月——不归档就等于没有证据。
- 演练三类故障：AgentArts 不可用、MCP 超时、客户端断网。每种都要有**可解释的界面表现 + 恢复步骤**。
- 准备只读演示账号 + 一键重置的脱敏演示数据（沿用现有 `ZHIXUE_ENABLE_DEV_TOOLS` 思路，公开环境保持关闭）。

**验收**：换一台电脑，照文档 30 分钟内能复现演示。

---

### S8 参赛材料与社区分享（约 2 人日）

- 作品说明（用 ICT 模板，不复用旧 C4 模板）。
- 六张图：场景、系统架构、多智能体、工作流、MCP 工具、数据与安全。
- 资产清单：智能体/工作流/知识库/MCP/版本号/发布时间。
- Trace 清单：挑 3 条能讲清"多 Agent 协作"的。
- 演示视频（≤3 分钟）：主场景 + 一次失败与恢复 + 平台 Trace。
- 社区分享帖：场景定义、编排思路、工作流架构、知识库构建方法、MCP 说明、操作指引、视频链接（赛题截图明确要求）。
- 提交包自检：排除 HAP、旧赛事材料、`.env`、`repository.json`、私钥；全包密钥扫描。

---

### 备用方案：如果时间只剩两周（高代码运行时）

平台还有一条**高代码**轨道：把 Agent 作为代码部署成"智能体运行时"容器，经 API 网关调用
`POST https://{endpoint}/runtimes/{runtime_name}/invocations`，请求头用 `Authorization: Bearer <api_key>`
和 `X-Hw-Agentarts-Session-Id`（同一 ID = 同一会话上下文），网关"不解析、不修改"请求体。

**能用它做什么**：把现有 Flask 后端整体包一层壳上线，两三天就能拿到"部署在 AgentArts 运行时 + 平台网关会话 + 平台侧调用记录"。

**它拿不到什么**：可视化编排、多智能体协作、知识库接入、MCP 工具编排、智能体评估 —— 而这五项恰好是赛题逐条点名的考察点。所以它只能当**兜底**，不能当主方案：

| 场景 | 建议 |
|---|---|
| 距提交 ≥3 周 | 走 S2–S4 主方案，不要分心 |
| 距提交 2 周、S4 未开工 | 主方案继续，同时用高代码运行时在 3 天内做出一版可演示的"云端部署"证据 |
| 距提交 ≤5 天、云端资产为零 | 全面转高代码运行时，把材料口径改成"基于 AgentArts 运行时部署 + 自定义 MCP"，**如实写清哪些能力是自研代码**，不要声称做了可视化编排 |

---

## 5. 逐文件改动总表

**A. 新增（后端 / MCP）**

| 路径 | 说明 |
|---|---|
| `app/agentarts/{__init__,config,auth,client,events,session}.py` | 网关核心（6 个文件） |
| `app/api/agentarts_api.py` | `/api/v2/agent/*` 蓝图 |
| `mcp/server.py`、`mcp/auth.py`、`mcp/envelope.py` | MCP 入口、鉴权、信封 |
| `mcp/tools/*.py`（11 个） | 11 个工具封装 |
| `mcp/contracts/tools.json`、`mcp/README.md` | 工具契约与说明 |
| `server/zhixue-agent-server/Dockerfile`、`docker-compose.yml` | 部署 |
| `config/agentarts.json`（只存 ID） | 资源登记 |
| `tests/test_agentarts_*.py`、`tests/test_mcp_*.py`（5 个） | 契约/断链/越权/幂等测试 |
| `web/`（`index.html`、`app.js`、`style.css`） | Web Demo |
| `evidence/ict-baseline-*.xml`、`evidence/deploy-*.md` | 证据 |

**B. 修改（后端）**

| 路径 | 改动 |
|---|---|
| `app/__init__.py` | 注册蓝图；`/health` 增 agentarts 段；CORS 加 Demo 域名 |
| `app/api/chat.py` | 标注为降级通道；响应加 `source:"local"` |
| `requirements.txt` / `requirements-lock-py312.txt` | `mcp`、`httpx`、`pyjwt` |
| `.env.example` | 新增 `AGENTARTS_*`、`MCP_*` 共 14 项 |
| `run.py` | 启动打印 AgentArts 配置状态（脱敏） |
| `README.md`、`仓库结构说明.md` | 增加网关/MCP/Web 三个入口 |

**C. 修改（App）**

`ApiDefaults.ets`、`AgentApiClient.ets`、`AgentBridge.ets`、`ChatModels.ets`、`ApiResponseValidator.ets`、`ChatMain.ets`、`AgentTrace.ets`、`ApiDisplayFormatter.ets`；测试同步 `AgentBridge.test.ets`、`ApiResponseValidator.test.ets`、`ExerciseFlow.test.ets`（含写死的 `baseUrl` 与 mastery 42→58 断言）。

**D. 修改（契约）**

`contracts/openapi.json`、`app/contracts/openapi.json`、`contracts/fixtures/*`（+3）、`contracts/README.md`。

**E. 停用 / 隔离**

`LocalAgentService.analyzeWrongQuestion` 的"模拟识别"演示路径、App「离线体验」默认入口、`LearningFlowData` 演示脚本、`hap/` 目录（不作为提交材料）。

---

## 6. 关键参数速查

| 项 | 值 / 来源 |
|---|---|
| 开通 | 先「申请公测」（约 5 工作日）再「免费开通基础版」；区域仅 `cn-north-4` |
| 运行时调用 | `POST /v1/{project_id}/agents/{agent_id}/conversations/{conversation_id}` |
| 查询参数 | `workspace_id`（默认 `default`）、`version`、`type`（`controller`=多智能体，`agent`=单智能体） |
| 请求头 | `X-Auth-Token`、`X-Invoke-Mode: debug|published`、`stream: true`、`Content-Type` |
| 请求体 | `{"query": "...", "inputs": {...}}`（`inputs` 用于传 `sessionRef` 等变量） |
| 流式 | **智能体应用只支持流式调用**；事件 `start/message/plugin_start/plugin_end/statistic_data/summary_response/done` |
| 工作流调用 | `POST /v1/{project_id}/workflows/{workflow_id}/conversations/{conversation_id}`，body `inputs` + `plugin_configs`（备用） |
| Token | IAM「获取用户 Token」，`auth.scope=project`；有效期 24h，需缓存 |
| `conversation_id` | 1–64 位 `[A-Za-z0-9-_]`；用 `uuid4().hex` |
| 多智能体结构 | 控制器节点 + 子节点只能是**工作流/多智能体**（各 ≤30，≤2 级嵌套）；意图识别工作流与模型配置**至少一个，优先意图识别** |
| 控制器护栏 | 执行动作 `继续/终止/等待输入`；最大对话历史轮次 10；最大跳转次数默认 9（建议 6–8） |
| 知识库 | 基础版：每应用**1 个**、租户 5 个、单文档 ≤20MB、仅 doc/pdf/pptx/xlsx/csv；`pangu_embedding` + `pangu_rerank`；拆分配置 自动/长度/层级；引用=来源内容+文件名 |
| MCP 传输 | 远程用 `streamableHttp`（推荐）或 `SSE`；必须**公网可达**，内网/本机不可用 |
| MCP 上限 | 单个应用最多 5 个 MCP 服务 → 聚合成 1 个（注：该限制写在"添加MCP服务"页） |
| MCP 鉴权 | 无需鉴权 / API Key / OAuth2.0 → 选 API Key；密钥由 KMS 默认密钥加密 |
| 评测 | 模块名「智能体评估」；评测集字段 `input` + `reference_output`；评估器 token 不占赠送额度；10 任务/人、5 评估器/任务 |
| Trace | 运营运维 → 观测 → 调用链管理；字段含链路ID/耗时/Span Type；**留存仅 15 天（基础版）** |
| 配额 | 500 次 API/天、应用 30、成员 3、空间 1、赠送 token 200 万 |
| 控制台 | `https://console.huaweicloud.com/versatile/` |
| 契约版本 | 当前 `api-contract-v0.3` → 改造后 `api-contract-v0.4` |

---

## 7. 时间表与分工

| 阶段 | 目标 | 关键动作 |
|---|---|---|
| 第 1 周（开工即启动） | 报名与资源落地 | S0 完成；**第一天提交公测申请**（审核约 5 工作日）；报名提交；域名方案定案 |
| 第 2 周 | 网关与 MCP 骨架 | S2 完成（先用本地假 SSE 端点验证解析器，平台开通后换真端点）；MCP 服务部署到公网并自测通过 |
| 第 3 周 | 平台资产成型 | 公测通过后 S4：知识库 + 4 条能力工作流 + 控制器；S3 收尾（10 个核心工具在平台调试通过） |
| 第 4 周 | 能演示 | S5 完成；Web Demo 可用；截图/录屏/初版材料 |
| 初赛材料提交窗口 | 材料定稿 | S6（≥30 条评测集 + 基线）、S7 部署与 Trace 归档、S8 材料 |
| 提交后 ~ 总决赛前 | 打磨 | 知识库扩充、评测优化复测、稳定性与成本 |
| 中国总决赛 | 答辩 | 15 分钟展示 + 5 分钟问答的答辩主线 |
| 全球总决赛 | 现场答辩 | 版本冻结、现场环境复现 |

**三人分工建议**：A（平台与材料）负责 S1、S4、S5 的 Web、S8；B（服务端）负责 S2、S3、S6、S7 的部署部分；C（客户端）负责 S5 的 App、S8 的视觉；S0 三人一起做。工作包、交接点与合并规则的完整版见 [三人分工与协作约定](三人分工与协作约定-20261106.md)。

---

## 8. 验收清单

改造完成需要**同时**满足：

- [ ] AgentArts 上存在已发布、可重复调用的控制器运行时（有 `agent_id` + 版本号）
- [ ] Web Demo 主场景真实调用该运行时；断开 AgentArts 后界面明确失败或标注降级
- [ ] 至少 4 条能力工作流在同一次链路（调用链管理）中有职责清晰的 Span 与耗时
- [ ] 至少 6 个（目标 10 个）MCP 工具在平台被真实调用，含读、算、写三类
- [ ] 关键 Trace 已导出归档（平台仅留 15 天，答辩前必然过期）
- [ ] 工具入参不含模型可伪造的 `userId`；越权测试通过
- [ ] 知识库回答与出题可追溯来源（文件名 + 片段；章节由 MCP `headingPath` 补齐）；跨用户隔离与删除验证通过
- [ ] 三条主场景闭环及"等待/失败/取消"分支各跑通一次
- [ ] 评测集 ≥30 条，有基线、优化后对照、失败样本分析
- [ ] 单次 P95 延迟与成本有实测数字
- [ ] 凭据只存在于服务端 `.env`；全包密钥扫描 0 命中
- [ ] 提交材料不含 HAP、旧赛事模板、`repository.json`、私钥
- [ ] 演示设备上不存在"假 AI"入口（模拟识别、假延迟、写死脚本）
- [ ] 社区分享帖已发布（含方案、编排、知识库、MCP、指引、视频）

---

## 9. 官方资料

- AgentArts 产品页：<https://www.huaweicloud.com/product/agentarts.html>
- 用户指南（成长地图）：<https://support.huaweicloud.com/agentarts/index.html>
- 使用流程（单智能体/工作流/多智能体）：<https://support.huaweicloud.com/usermanual-agentarts0/agentarts_05_0001.html>
- 创建多智能体应用：<https://support.huaweicloud.com/usermanual-agentarts0/agentarts_05_0106.html>
- 添加 MCP 服务（含"单应用 ≤5 个"限制）：<https://support.huaweicloud.com/usermanual-agentarts0/agentarts_05_0021.html>
- 创建/接入自定义 MCP：<https://support.huaweicloud.com/usermanual-agentarts0/agentarts_05_0136.html>
- 创建知识库：<https://support.huaweicloud.com/usermanual-agentarts0/agentarts_05_0152.html>
- 为应用添加知识库：<https://support.huaweicloud.com/usermanual-agentarts0/agentarts_05_0025.html>
- 调用智能体应用 `runAgent`：<https://support.huaweicloud.com/api-agentarts0/runAgent.html>
- 调用示例：<https://support.huaweicloud.com/api-agentarts0/agentarts_07_0011.html>
- 认证鉴权（IAM Token）：<https://support.huaweicloud.com/api-agentarts0/agentarts_07_0005.html>
- 智能体评估：<https://support.huaweicloud.com/usermanual-agentarts0/agentarts_05_0207.html>
- 调用链管理（Trace）：<https://support.huaweicloud.com/usermanual-agentarts0/agentarts_05_0203.html>
- 智能体开发平台控制台（华北-北京四）：<https://console.huaweicloud.com/versatile/>

> 赛程节点（报名截止、初赛材料提交、中国区/全球总决赛答辩）以官网公告为准。本文按**相对周次**推进，不对应具体日历日期——拿到官方通知后只替换"周"对应的起始日即可，内容无需改动。
