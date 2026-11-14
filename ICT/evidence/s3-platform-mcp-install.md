# S3 平台接入实测结论

## 结论

**公网 IP + 自签证书 + 8443 可用，不需要域名、不需要备案。**

AgentArts（西南-贵阳一）能直接连到 `https://<公网IP>:8443/mcp`，自动发现全部 11 个工具。

## 实测环境

| 项 | 值 |
|---|---|
| AgentArts 区域 | 西南-贵阳一（`cn-southwest-2`），账号 `heb_2186578344`，IAM 用户 `zhixue` |
| 控制台版本 | 新版（无“个人空间”、无一级 MCP 菜单） |
| MCP 入口 | 单智能体配置 → 技能 → **MCP服务** → `+` → 空白创建 |
| 传输方式 | HTTP（Streamable HTTP） |
| 服务地址 | `https://47.100.237.167:8443/mcp`（**该 IP 为临时分配；ECS 重启后变为 `139.196.188.253`**） |
| 证书 | openssl 自签，`subjectAltName=IP:47.100.237.167`（IP 变更后需重签） |
| 鉴权 | API Key，位置 Header，参数名 `X-API-Key` |
| 结果 | **已添加 11/11**，工具名与描述均正确显示 |

## 对方案的影响

1. **域名/备案这条路可以放弃**（至少现阶段）。省下 1–3 周备案时间。
2. 之前担心的“平台不信任自签证书”**不成立**——该版本不校验自签证书。
3. 仍需保留域名方案作为**长期备选**：证书过期、IP 变更、或后续版本收紧校验时会用到。

## 必须跟进的五件事

| 事项 | 原因 | 处理 |
|---|---|---|
| **ECS 不能释放** | 它现在是平台依赖，释放即断链 | 把按量付费转包月/包年，并记录到期时间 |
| **公网 IP 会变** | 分配型公网 IP 在**重启实例**后重新分配（已实测两次：`47.100.237.167` → `139.196.188.253` → 再次变更） | **绑定弹性公网 IP（EIP）**；否则每次重启要重签证书 + 改平台 MCP 地址 |
| **只重启服务，不重启实例** | 更新代码/配置只需 `systemctl restart zhixue-mcp`；重启实例才会换 IP | 把这条写进团队操作纪律，别在控制台点“重启实例” |
| **服务不会自启** | `nohup` 进程随重启消失 | 配 systemd：`systemctl enable zhixue-mcp` |
| **自签证书 365 天过期** | 过期后平台调用全部失败 | 在日历上设提前 1 个月提醒；或改用正式证书 |
| **JSON 仓储单进程** | 网关 + MCP 同时跑会抢锁 | 双进程运行时切 `ZHIXUE_DB=sqlite` |

## 复现步骤（换机器时用）

1. ECS 起 `zhixue_mcp.server`，`--host 0.0.0.0 --port 8443 --cert cert.pem --key key.pem`
2. 自签证书必须带 `subjectAltName=IP:<公网IP>`
3. 控制台：单智能体 → 技能 → MCP服务 → 空白创建 → HTTP → 填地址 → API Key（Header `X-API-Key`）
4. 点「安装」→ 应显示 11/11

## 待补

- [x] 智能体发布后拿到运行时调用地址（见下节）
- [x] 网关 `.env` 切到 `cn-southwest-2` + runtime 形态（已验证 `ready=True`）
- [ ] 平台侧实际调用一次工具（需先种 `sessionRef` 数据）
- [ ] 智能体提示词补全后，确认回复正文走 `message` 还是 `summary_response`

---

## 运行时调用（已打通）

低代码 `runAgent` 那套（`/v1/{project}/agents/{agent_id}/conversations/...` + IAM Token）
**在当前控制台版本取不到地址**（`查看API` 的 URL / Path 均为空），改走「部署到智能体运行时」。

| 项 | 值 |
|---|---|
| 运行时名称 | `agent-arts-2fa786ab339c425b835c784e21a12166` |
| 访问域名 | `defaultgw-gnsgmnbygy.cn-southwest-2.huaweicloud-agentarts.com` |
| 调用地址 | `https://{域名}/runtimes/{运行时名称}/invocations` |
| 鉴权 | `Authorization: Bearer <工作负载身份 API Key>`，Key 为 **32 位十六进制** |
| 会话 | 头 `x-hw-agentarts-session-id`，1–32 位、小写字母/数字/中划线 |
| **请求体** | **`{"inputs": {"query": "..."}}`** |
| 响应 | SSE：`start` / `message` / `statistic_data` / `done` 等，与 `events.py` 一致 |
| 入站认证 | API Key 认证（运行时绑定的是以运行时名命名的那条 Key） |

### 踩过的两个坑

1. **`查看API` 给出空 URL**：低代码 API 渠道在此版本不生成地址，不要在上面耗时间，直接部署运行时。
2. **401 的两种原因**：`zhixue-gateway` 身份下生成的 86 位 Key **不是**运行时绑定的那条；
   运行时绑定的是名称等于运行时名的那条（32 位十六进制）。取错就 401。

### 对应代码改动

- `app/agentarts/config.py`：新增 `AGENTARTS_API_STYLE=runtime|run_agent`、`runtime_name`、`api_key`、`invocation_url()`
- `app/agentarts/client.py`：运行时形态（Bearer + 会话头）、请求体自动回退（4 种形态，仅 4xx 时切换）
- `app/agentarts/session.py`：`conversation_id` 改为 32 位十六进制（运行时会话头上限 32）
- `tools/check_agentarts_runtime.py`：一条命令验证运行时调用
