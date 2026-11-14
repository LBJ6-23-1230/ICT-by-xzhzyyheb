# 演示基线复位与固定公网 IP

> 这两件事都不增加功能，但直接决定"现场演示会不会翻车"。
> 数字前后不一致、链接突然失效，比功能少一项更致命。

## 一、为什么必须固定 IP

调试期公网 IP 已经变过三次（`47.100.237.167` → `139.196.188.253` → `47.101.194.88`）。
每变一次，下面三处全部失效：

| 依赖 IP 的地方 | 失效表现 |
|---|---|
| 平台里的 MCP 服务地址 | 11 个工具全部超时，工作流报 `MCP execute error` |
| MCP 自签证书的 `subjectAltName` | 证书与地址不匹配，连接被拒 |
| 演示链接 `http://<IP>:5000/demo` | 页面打不开 |

**根因**：ECS 每次停止/启动，公网 IP 都可能重新分配。

## 二、绑定 EIP（一次性，约 10 分钟）

1. 阿里云控制台 → **弹性公网 IP** → 开通（按量付费，带宽选"按使用流量"最省）
2. 购买后 → **绑定资源** → 选你的 ECS 实例
3. 绑定后 ECS 的原公网 IP 会被释放，**实例对外地址变成 EIP**
4. 记下新 IP（下文称 `<EIP>`）

> 绑定前先确认没有正在跑的重要任务：换 IP 会瞬断连接。

## 三、换 IP 后必须按顺序做四件事

**① 重新生成 MCP 证书（SAN 用新 IP）**

```bash
cd /opt/zhixue
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout key.pem -out cert.pem \
  -subj "/CN=<EIP>" -addext "subjectAltName=IP:<EIP>"
```

**② 重启 MCP，确认证书生效**

```bash
systemctl restart zhixue-mcp
sleep 2
curl -sk https://<EIP>:8443/healthz -H "X-API-Key: 11312daf79d35eb7611f56a042ddbee2"
```

**③ 更新平台里的 MCP 地址**

AgentArts → 单智能体 → 技能 → MCP服务 → `zhixue-learning-tools` → 编辑：
访问地址改成 `https://<EIP>:8443/mcp` → 保存 → **重新「调试」一次**，确认 11 个工具都能列出。

**④ 更新文档与脚本里的 IP**

```bash
grep -rn "47.101.194.88" /opt/zhixue /etc/systemd/system/zhixue-*.service 2>/dev/null
```

本仓库中同步修改的位置：`ICT/S3-MCP-阿里云部署步骤.md`、`ICT/evidence/s3-platform-mcp-install.md`、
各 `evidence/*.md` 里的示例命令。

## 四、加固：只重启服务，不重启实例

```bash
# 日常改代码后只重启服务
systemctl restart zhixue-mcp zhixue-gateway

# 绝对不要：
#   shutdown -r now / 控制台"重启实例"
```

EIP 绑定后重启实例**也不会**换 IP，但仍在团队约定里写死这一条，避免有人习惯性重启。

## 五、演示数据复位

### 用法

```bash
cd /opt/zhixue

# 先看一眼会写什么
python3 tools/reset_demo_data.py --dry-run

# 正式复位
python3 tools/reset_demo_data.py
```

脚本需要上传到服务器（随 `tools` 目录）：

```powershell
scp tools/reset_demo_data.py root@<EIP>:/opt/zhixue/tools/
```

### 复位后的基线

| 项 | 值 |
|---|---|
| 用户 | `demo-user`（昵称"演示用户"） |
| 掌握度 | 二叉树遍历 42 / 图算法 55 / 哈希冲突 63 |
| 计划 | `plan-demo` v1，两个任务各 30 分钟，合计 60 |
| 资料 | `kb-demo` · `二叉树讲义.pdf` · 1 个切片（章节含"二叉树遍历基础"） |
| 题目 | `ex-demo-1`，标准答案 **B** |
| 会话 | `sref-demo-0000000000000001` |
| 运行记录 | 清空（时间线从零开始） |

### 设计要点

* **只清 `demo-user` 的数据**：队友或真实账号的记录保留（测试已验证）
* **清掉 `llm-*` 题目**：避免演示时判分撞上测试残留题
* **幂等**：连跑两次结果一致
* **不用重启**：SQLite 每次查询都读盘（JSON 仓储才需要重启）

## 六、演示前 5 分钟检查清单

```bash
# 1. 两个服务都在
systemctl is-active zhixue-mcp zhixue-gateway

# 2. MCP 活着且工具齐
curl -sk https://<EIP>:8443/healthz -H "X-API-Key: <MCP Key>"

# 3. 网关活着
curl -s http://127.0.0.1:5000/health

# 4. 云端配置正确（不打印密钥）
curl -s http://127.0.0.1:5000/api/v2/agent/runtime | head -c 300

# 5. 复位数据
python3 tools/reset_demo_data.py

# 6. 端到端冒烟（30 秒内应看到 wf-planner）
curl -N -s --max-time 60 -X POST http://127.0.0.1:5000/api/v2/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"我今晚只有60分钟怎么安排","sessionId":"chat-smoke"}' | tail -8
```

六条全过再开始演示。**任何一条不过，先修再讲。**

## 七、常见翻车与对策

| 现象 | 原因 | 现场处理 |
|---|---|---|
| 工具全超时 | 平台 MCP 地址还是旧 IP | 控制台改地址，或临时用 `https://<EIP>:8443/mcp` |
| 页面打不开 | 安全组没放行 / 服务没起 | `systemctl status`；安全组补规则 |
| 回答是"请提供相关信息" | 控制器未拿到 `query` | 检查控制器入参映射 |
| `100025 Invalid authorization header` | 平台模型鉴权失效 | **备用模型**预案：换一条已开通的模型 |
| 计划里数字不对 | 数据不是基线 | 跑复位脚本 |
| 判分 `NOT_FOUND` | 题目 ID 不存在 | 用 `ex-demo-1`，或重新出题后取新 ID |

**提前准备**：把"模型鉴权失效"的应对写在手卡上——这是目前最可能复发的平台侧问题。
