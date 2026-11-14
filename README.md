# 知学 Mate · 华为云 AgentArts 版（ICT 赛题 2）

> 面向 **华为 ICT 大赛 创新赛 · 赛题 2「基于华为云 AgentArts 智能体开发平台的 Agent 设计和应用」** 的改造分支。
> 老项目是「HarmonyOS App + Flask 单体后端」，本分支把它重构为：**AgentArts 负责编排与解释，自建服务退化为确定性能力后端（MCP）**。

---

## 1. 一分钟看懂

```text
浏览器（Web Demo，单页）
   │  SSE
   ▼
Flask 网关（会话 / 信封化事件 / 判分聚合 / 本地兜底）
   │                        │
   │ Bearer 运行时空      │ 直连 MCP（确定性计算）
   ▼                        ▼
AgentArts 多智能体控制器    MCP 服务（12 个工具，HTTPS）
   ├── wf-diagnosis          ├── 12 个工具
   ├── wf-planner            ├── SQLite：画像 / 计划 / 题库 / 资料 / 错题 / 课程 / DDL
   ├── wf-exercise           └── 出题模型（DashScope 或 MaaS，可换）
   ├── wf-assessment
   └── wf-chat（建议新增的兜底 Agent）
```

**一句话主张**：*模型只判断，不算数*。分数、预算、掌握度、是否重排全部由工具计算，模型只负责解释与编排。

---

## 2. 现状（截至当前分支）

| 模块 | 状态 | 说明 |
|---|---|---|
| 云资源 / IAM | ✅ | 华为云贵阳一（`cn-southwest-2`），AgentArts 基础版 |
| 网关 `/api/v2/agent/*` | ✅ | 会话映射、SSE 事件归一、判分聚合、本地兜底 |
| MCP 服务（12 工具） | ✅ | 公网 HTTPS + API Key；63 项端到端自检 |
| 多智能体控制器 | ✅ | 4 条能力工作流已跑通；`wf-chat` 兜底待加 |
| Web Demo | ✅ | 三栏（对话 / 时间线 / 运行详情）+ 上传 + 拍题 + 答题卡 |
| 冷启动（课程/知识点） | ✅ | 用户自填课程与知识点，未评估 → 判分后才有真实掌握度 |
| 课程表 / DDL 导入 | ✅ | 课表支持截图识别；DDL 文本确定性解析 |
| 错题本 | ✅ | 交卷自动收集 + 拍题手动加入 + 列表/移出 |
| 知识点归一（kpId） | ✅ | 注册表 + 别名解析；歧义不猜 |
| 效果评估（S6） | ❌ | **唯一完全空白项**，赛题明确要求 |

---

## 3. 仓库结构（本分支新增部分）

```text
ICT/                          # 赛题相关文档与证据（提交材料从这里取）
  README.md                   # 阶段进度表
  项目定位与目标形态.md
  赛题对齐与改动清单.md
  演示对照赛题要求.md
  彩排脚本-三分钟演示.md
  S4-平台资产搭建步骤.md
  S4-工作流布置与测试用例.md
  S4-控制器配置与验收.md
  S5-数据入口与图片诊断方案.md
  S5-依据链.md
  S5-图片错题诊断.md
  S5-错题诊断与学情更新.md
  S5-演示基线复位与固定IP.md
  S6-*                      # 待补：效果评估
  evidence/                   # 可复现证据（自检输出、试运行记录、截图）

server/zhixue-agent-server/
  app/
    api/agentarts_api.py      # 网关主入口（约 20 个端点）
    agentarts/                # 平台客户端：config/client/events/session
    services/
      knowledge_registry.py   # 知识点注册表 + 别名解析（kpId 归一）
      catalog.py              # 课程 / 知识点 CRUD
      study_import.py         # 课表识别 + DDL 解析 + 落库
      wrongbook.py            # 错题本（只记录，不动掌握度）
      wrong_photo.py          # 拍题识别（只识别，不判分）
      mcp_client.py           # 网关 → MCP 通用调用
      knowledge_proxy.py      # 检索代理（引用可点开）
      grounded_exercises.py   # 按资料出题（含校验与拒绝）
    services/llm_runtime.py   # 统一模型客户端（超时/预算）
  zhixue_mcp/                 # MCP 服务（12 个工具）
    server.py registry.py auth.py tools/ contracts/tools.json
  web/index.html              # 单文件 Demo（无构建链）
  data/                       # SQLite / 题库

tools/
  verify_agentarts_s2.py      # 网关 54 项自检
  verify_agentarts_s3.py      # MCP 63 项自检
  reset_demo_data.py          # 演示基线复位
  migrate_knowledge_points.py # 知识点 ID 归一迁移
  check_agentarts_runtime.py  # 运行时连通性
```

---

## 4. 快速开始

### 4.1 本地（只跑网关 + 前端）

```bash
cd server/zhixue-agent-server
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # 填 AgentArts / 模型凭据
python run.py                                          # http://127.0.0.1:5000/demo
```

### 4.2 服务器（网关 + MCP，systemd）

```bash
# 目录约定
/opt/zhixue/{app,zhixue_mcp,web,data,tools,run.py}

# 两个 systemd 单元
systemctl status zhixue-mcp zhixue-gateway
systemctl restart zhixue-mcp zhixue-gateway
```

`/opt/zhixue/mcp.env`（MCP 服务）：

```bash
ZHIXUE_DB=sqlite
MCP_API_KEY=<与平台 MCP 配置一致的 Key>
DASHSCOPE_API_KEY=<出题模型 Key；换 MaaS 时保留变量名、换值>
LLM_MODEL=qwen-plus
```

`/opt/zhixue/gateway.env`（网关）：

```bash
ZHIXUE_DB=sqlite
PORT=5000
ZHIXUE_ENV=development
AGENTARTS_ENABLED=1
AGENTARTS_MODE=cloud
AGENTARTS_API_STYLE=runtime
AGENTARTS_REGION=cn-southwest-2
AGENTARTS_ENDPOINT=<运行时访问域名>
AGENTARTS_RUNTIME_NAME=<运行时名称>
AGENTARTS_API_KEY=<工作负载身份 Key>
MCP_BASE_URL=https://127.0.0.1:8443
MCP_API_KEY=<同 MCP 服务 Key>
MCP_SESSION_REF=<演示会话 ref>
```

> ⚠️ **所有凭据只放服务器环境变量与本地 `.env`，不要提交到仓库。**

---

## 5. 接口一览

### 5.1 MCP 工具（12 个，平台可直接发现）

| 工具 | 读/写 | 作用 |
|---|---|---|
| `get_learning_context` | 读 | 画像 / 计划 / 薄弱点 / **deadlines / courses** |
| `search_learning_materials` | 读 | 检索切片，返回文件名 + 章节 + 片段哈希 |
| `get_learning_evidence` | 读 | 某知识点的掌握度、错题、证据数量 |
| `calculate_learning_priority` | 读 | 五因子排序 |
| `fit_plan_to_budget` | 读 | 按可用分钟裁剪，排不下的进 `unscheduledTasks` |
| `grade_exercise` | 读 | 确定性判分（只认服务端答案键） |
| `get_plan_diff` | 读 | 计划差异 |
| `create_grounded_exercise` | 读 | 按资料出题（含校验、拒绝无依据） |
| `save_learning_plan` | 写 | 乐观锁 + 幂等 |
| `update_mastery` | 写 | 按规则换算掌握度 |
| `request_replan` | 写 | 判断是否重排并落库 |
| `record_wrong_question` | 写 | 记错题 + 下调掌握度 |

### 5.2 网关 API（节选）

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/v2/agent/chat` | 主链路，SSE |
| GET | `/api/v2/agent/runs/<id>` | 事件回放 |
| GET | `/api/v2/agent/runtime` | 配置与降级状态 |
| GET/POST/DELETE | `/api/v2/agent/wrongbook` | 错题本 |
| GET/POST | `/api/v2/agent/exercises` `/grade` | 取题 / 判分聚合（含平台点评） |
| GET/POST/PATCH/DELETE | `/api/v2/agent/catalog` `/courses` `/points` | 课程与知识点管理 |
| POST | `/api/v2/agent/schedule` `/deadlines` | 课表 / DDL 导入 |
| GET/POST | `/api/v2/agent/profile` `/targets` | 学情与冷启动 |
| GET | `/api/v2/agent/knowledge` | 引用检索（依据可点开） |
| POST | `/api/v2/agent/vision` | 拍题识别 |

---

## 6. 关键设计决策（改动前请先读）

1. **模型不算数**：判分、预算、掌握度、重排都是工具；模型只做解释与分派。
2. **降级分层**：数字与解析依赖本机（不依赖平台）；平台挂了只少评语，并且**界面明确标注** `fallback: local` / `（本次不可用：…）`。
3. **知识点是主键**：任何"说法"先经 `resolve()` 归一成 `kpId`，**禁止拿名字当 ID**。简称有歧义时不猜。
4. **知识点必须属于课程**：`courses` → `knowledge_points.course` → `profiles.mastery`。
5. **未评估 ≠ 薄弱**：用户自填的知识点 `assessed=false`，诊断应说"还没测过"，不能排成薄弱点。
6. **引文由服务端截取**：模型只给 `sourceIds`，避免它润色原文导致整批题作废。
7. **JSON 仓储有内存缓存**：外部改文件必须重启；跨进程共享一律用 SQLite。
8. **平台工具 schema 有缓存**：改过工具参数后，工作流节点需**删除重加**。

---

## 7. 自检与证据

```bash
python tools/verify_agentarts_s2.py     # 网关 54 项
python tools/verify_agentarts_s3.py     # MCP 63 项（真起 HTTP）
python tools/reset_demo_data.py         # 复位演示基线
python tools/migrate_knowledge_points.py --dry-run
```

证据归档在 `ICT/evidence/`：自检输出、试运行记录、截图与诊断过程。

---

## 8. 待办（按优先级）

| # | 事项 | 价值 | 预估 |
|---|---|---|---|
| 1 | **S6 效果评估**：20 条标注用例 + 跑分脚本 + 报告 | 赛题明确要求，目前 0 | 半天 |
| 2 | 控制器加 `wf-chat` 兜底 + 提示词去冲突 | 消除空回复 | 1 小时 |
| 3 | Agent 决策可视化（为什么不是别的 Agent） | 创新性加分 | 2 小时 |
| 4 | 资料抽取的知识点走"候选 → 用户确认" | 可解释性 | 半天 |
| 5 | 错题重练（答对出本） | 复习闭环 | 2 小时 |
| 6 | EIP + HTTPS 反代 | 演示稳定性 | 1 小时 |

---

## 9. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `HTTP 404` | 运行时名/域名变了（重新部署后） | 复制运行时详情页的值改 `gateway.env` |
| `HTTP 401` | 工作负载 Key 变了 | 重新取 Key，重启网关 |
| `HTTP 403` + image pull | 平台镜像拉取失败/实例 Error | 重新部署（版本号 +1） |
| `100025` | 平台模型鉴权失效 | 平台模型页补 Key |
| 工具报 `param X is not support default_value` | 工作流节点未映射该参数 | 给参数设默认值或显式映射 |
| 改了 `repository.json` 不生效 | JSON 仓储有内存缓存 | 重启；或改用 SQLite |
| 改了工具参数但页面没变 | 平台 schema 缓存 | 删除该节点重新添加 |

---

## 10. 协作约定

- **分支**：`main` 保持可演示；功能走 `feat/<主题>`，合并前跑两个自检。
- **文件归属**：`app/services/*` 与 `zhixue_mcp/*` 改动需同步更新 `contracts/tools.json` 与自检。
- **凭据**：一律不进仓库；服务器改 Key 用 `/opt/zhixue/set-runtime.sh`（改配置 + 重启 + 裸测）。
- **演示相关**：`ICT/` 下的文档是提交材料来源，改动请同步 `ICT/README.md` 的进度表。

---

## 11. 相关文档索引

| 想了解 | 看这里 |
|---|---|
| 赛题怎么评、我们差什么 | `ICT/赛题对齐与改动清单.md` |
| 定位与和老项目的区别 | `ICT/项目定位与目标形态.md` |
| Demo 要展示什么 | `ICT/演示对照赛题要求.md` |
| 怎么彩排 3 分钟 | `ICT/彩排脚本-三分钟演示.md` |
| 控制器/工作流怎么配 | `ICT/S4-控制器配置与验收.md`、`ICT/S4-工作流布置与测试用例.md` |
| 依据链、错题、导入 | `ICT/S5-*.md` |
| 证据 | `ICT/evidence/` |
