# 知学 Mate MCP 服务

把已过测的确定性能力暴露给 AgentArts 的**自定义 MCP 服务**。平台侧创建 MCP 时填本服务的
公网 HTTPS 地址，工具会被**自动发现**，不需要在平台上逐个手写接口。

## 为什么独立成一个进程

| 原因 | 说明 |
|---|---|
| 依赖面最小 | 只用标准库 + `app/domain`、`app/tools`、`app/decision` 等纯逻辑模块，**不需要 Flask** |
| 可独立部署 | 平台要求公网可达；把整个 Web 后端暴露到公网，攻击面远大于只开一个 `/mcp` |
| 故障隔离 | MCP 挂了不影响聊天与计划页；反之亦然 |
| 可自测 | 不装完整依赖也能起服务、跑端到端（见 `tools/verify_agentarts_s3.py`） |

## 工具清单（11 个，写操作 3 个）

| 工具 | 读/写 | 说明 |
|---|---|---|
| `get_learning_context` | 读 | 目标、考试日期、可用时间、画像版本、最薄弱知识点、当前计划 |
| `search_learning_materials` | 读 | 资料检索，带文件名/章节/片段哈希 |
| `get_learning_evidence` | 读 | 掌握度、错题、证据数；`hasEvidence=false` 时不得下薄弱点结论 |
| `calculate_learning_priority` | 算 | 五因子排序，返回因子明细 |
| `fit_plan_to_budget` | 算 | 按可用分钟裁剪，整任务排入，排不下进延期 |
| `grade_exercise` | 算 | 确定性判分，**答案键只在服务端读取** |
| `get_plan_diff` | 算 | 两版计划的保留/压缩/延期差异 |
| `save_learning_plan` | **写** | 乐观锁（expectedVersion）+ 幂等（idempotencyKey） |
| `update_mastery` | **写** | 逐知识点按固定规则更新画像 |
| `request_replan` | **写** | 判断是否重排并落新版本 |
| `create_grounded_exercise` | 算 | 只按资料出题，无资料返回 `INSUFFICIENT_MATERIALS` |

契约文件：[`contracts/tools.json`](contracts/tools.json)（由 `--dump-contracts` 生成，别手改）。

## 本地运行

```powershell
cd server/zhixue-agent-server

# 只看工具清单
python -m zhixue_mcp.server --check

# 起服务（本机调试允许无 Key）
$env:MCP_ALLOW_NO_KEY="1"
python -m zhixue_mcp.server --port 8765
```

自测（会真的发 HTTP 请求，含 48 项断言）：

```powershell
python tools/verify_agentarts_s3.py
```

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `MCP_API_KEY` | 生产必填 | 平台创建 MCP 时填同一个值；**未配置时默认拒绝所有请求** |
| `MCP_ALLOW_NO_KEY` | 否 | 仅本机调试：置 `1` 才允许无 Key |
| `MCP_HOST` / `MCP_PORT` | 否 | 默认 `127.0.0.1:8765`；容器里用 `0.0.0.0` |
| `MCP_TLS_CERT` / `MCP_TLS_KEY` | 否 | PEM 证书与私钥；同时给出才启用 HTTPS（也可用 `--cert/--key`） |
| `ZHIXUE_DB` | 否 | `json`（默认）或 `sqlite[:path]` |
| `ZHIXUE_REPOSITORY` | 否 | JSON 仓储路径，默认 `data/repository.json` |

### 直接开 HTTPS（不装 Nginx）

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout key.pem -out cert.pem \
  -subj "/CN=<公网IP或域名>" \
  -addext "subjectAltName=IP:<公网IP>"      # 用域名时改成 DNS:<域名>

MCP_API_KEY=*** python -m zhixue_mcp.server \
  --host 0.0.0.0 --port 8443 --cert cert.pem --key key.pem
```

TLS 在进程内完成，反代只用于"要 443 端口或要正式证书"的场合。

> ⚠️ **JSON 仓储不适合并发写**：MCP 与 Flask 同时写同一文件会出现后写覆盖。
> 演示阶段可接受（一条链路同一时刻只有一个写者）；一旦两人同时操作，切 `ZHIXUE_DB=sqlite`。

## 部署到公网（这是唯一必须解决的问题）

平台侧只能访问**公网地址**，内网/本机一律不通。三条路，按推荐顺序：

| 方案 | 做法 | 成本 | 注意 |
|---|---|---|---|
| **A. 华为云 ECS + 域名 + 证书** | ECS 起容器，Nginx/Caddy 反代 443 | 代金券可能覆盖 | 大陆节点域名需 ICP 备案（1–3 周） |
| **B. 任意云厂商（阿里云/腾讯云）** | 同上，平台不关心主机在哪 | 几十元/月 | 技术上无差别；**评委可能问"为什么不用华为云"**，答：平台主体在 AgentArts，MCP 只是工具服 |
| **C. 香港/海外节点** | 免备案，直接 HTTPS | 略高 | 延迟稍高，演示够用 |

> 临时联调可以用内网穿透（cloudflared / frp）拿到 https 域名，但**比赛期间不要依赖它**：
> 隧道断一次，演示就断一次。

最小反代配置（Caddy，自动签证书）：

```caddy
mcp.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

## 平台侧接入步骤

1. 开发中心 → MCP → **创建 MCP** → 选 **streamableHttp**。
2. 地址填 `https://mcp.<你的域名>/mcp`。
3. 鉴权选 **API Key**，值与服务器上的 `MCP_API_KEY` 一致。
4. 保存后应能**自动发现 11 个工具**（`tools/list`）。
5. 在智能体的"技能 → MCP 服务"里添加全部。
   ⚠️ 单个应用最多 5 个 MCP 服务 —— 所以这里把 11 个工具聚成一个服务，正好不触限。
6. 为工具配置入参时，把 `sessionRef` 绑定到应用变量（由网关通过 `inputs` 注入）。

## 手工验证（不依赖平台）

```bash
# 健康检查
curl -s http://127.0.0.1:8765/healthz -H "X-API-Key: $MCP_API_KEY"

# 工具发现
curl -s -X POST http://127.0.0.1:8765/mcp \
  -H "Content-Type: application/json" -H "X-API-Key: $MCP_API_KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 调用工具（sessionRef 由网关签发）
curl -s -X POST http://127.0.0.1:8765/mcp \
  -H "Content-Type: application/json" -H "X-API-Key: $MCP_API_KEY" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_learning_context","arguments":{"sessionRef":"sref-..."}}}'
```

## 安全边界（这些点会被评委问）

1. **两层身份**：API Key 证明"请求来自我们的应用"，`sessionRef` 证明"是哪个学生"。
2. **模型不能自报身份**：工具入参含 `userId` 会直接 `VALIDATION_ERROR`；身份只由
   `sessionRef` 在服务端换出，随机串无法伪造。
3. **答案键不出服务端**：`grade_exercise` 的 schema 里没有 `answerKeys`，传了也忽略。
4. **无资料不出题**：`create_grounded_exercise` 在资料不足时返回错误，不编造引用。
5. **写操作三件套**：幂等键、版本号、身份换绑，缺一不可。

## 已知边界（如实写）

- 协议实现的是 MCP 的**必需子集**（initialize / tools/list / tools/call / ping），
  没有 resources、prompts、sampling；平台用不到的就不假装支持。
- 目前只回普通 JSON（`application/json`），不提供 SSE 下行；工具都是短耗时调用，
  不需要流式进度。
- `create_grounded_exercise` 依赖 `openai` 与本地大模型配置；缺依赖时返回
  `TOOL_UNAVAILABLE` 而不是 500。
