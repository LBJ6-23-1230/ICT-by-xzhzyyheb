# S3-MCP：阿里云 ECS 公网 IP + 8443 试跑（按量付费）

**目的**：用最小成本验证一件事——**AgentArts 能不能连上 `https://<公网IP>:8443/mcp` 并发现工具**。
能通，就绕开了域名与备案；不能通，就老老实实走域名（备案或香港节点）。

**成本**：按量付费 2 核 2G 约 ¥0.2–0.4/小时，域名和备案都不需要。**验证完立刻释放**。

**判定标准**（30 分钟内出结论）：

| 结果 | 说明 | 下一步 |
|---|---|---|
| 平台能发现 11 个工具 | IP + 自签证书 + 8443 全通 | 继续 S4，正式演示前换正式域名/证书 |
| 报证书不受信任 | 平台不认自签 | 换域名 + 正式证书（香港免备案） |
| 报地址格式不支持 | 平台要求域名 | 同上 |
| 连接超时 | 安全组/监听地址问题 | 先本地 `curl -k` 自测，再查安全组 |

---

## 1. 买 ECS

阿里云控制台 → 云服务器 ECS → **自定义购买**：

| 项 | 选什么 | 原因 |
|---|---|---|
| 付费方式 | **按量付费** | 试完就释放，不占包年 |
| 地域 | 华东1（杭州）/ 华北2（北京）任选 | 离你近即可 |
| 实例规格 | 2 vCPU / 2 GiB（如 `ecs.e-c1m1.large`） | Python 单进程足够 |
| 镜像 | **Ubuntu 22.04 LTS** | 自带 Python 3.10，代码可直接跑 |
| 公网 IP | **分配公网 IPv4**，计费"按使用流量"，带宽峰值 5 Mbps | 只要够协议往返 |
| 系统盘 | 40 GiB 高效云盘 | 最小配置 |
| 登录凭证 | 自定义密码（记下来） | 后面 SSH 用 |

> 不用买 SLB、不用买域名、不用备案。

## 2. 开安全组（关键，最容易漏）

实例 → 安全组 → 配置规则 → **入方向**，加两条：

| 端口 | 授权对象 | 用途 |
|---|---|---|
| `22/tcp` | 你的公网 IP/32 | SSH（**别开 0.0.0.0/0**） |
| `8443/tcp` | `0.0.0.0/0` | 平台来连 MCP |

出方向保持默认全放通。

## 3. 登录并装依赖

```bash
ssh root@<公网IP>

apt update
apt install -y python3 python3-venv openssl curl
python3 --version   # 应为 3.10.x，够用
```

> 代码只用到标准库；Ubuntu 22.04 的 3.10 满足全部语法。

## 4. 上传最小代码集

在**本机**仓库根目录打包（只传 MCP 需要的部分，别传整个仓库）：

```powershell
# 本机 PowerShell
cd C:\Users\86150\Desktop\ICT-by-xzhzyyheb-main
tar -czf mcp-min.tgz -C server/zhixue-agent-server app zhixue_mcp config.py data
scp mcp-min.tgz root@<公网IP>:/root/
```

服务器上解包：

```bash
mkdir -p /opt/zhixue && cd /opt/zhixue
tar -xzf /root/mcp-min.tgz
ls    # 应看到 app  zhixue_mcp  config.py  data
```

> `data/` 里只有 `question_bank.json` 时也没关系——第 5 步会种演示数据。

## 5. 种一份演示数据（拿到 sessionRef）

```bash
cd /opt/zhixue
python3 - <<'PY'
import json
records = {
  "agentarts_sessions": {"conv-demo-0000000000000001": {
      "conversationId": "conv-demo-0000000000000001", "userId": "u-demo",
      "chatSessionId": "chat-demo", "agentId": "agent-demo",
      "sessionRef": "sref-demo-0000000000000001",
      "createdAt": "2026-11-09T00:00:00+00:00", "lastActiveAt": "2026-11-09T00:00:00+00:00"}},
  "profiles": {"u-demo": {"userId": "u-demo", "goal": "过数据结构期末",
      "examDate": "2027-01-10", "profileVersion": 1,
      "mastery": [{"knowledgePointId": "binary-tree-traversal",
                   "knowledgePointName": "二叉树遍历", "masteryScore": 42}]}},
}
open("/opt/zhixue/data/repository.json", "w", encoding="utf-8").write(
    json.dumps(records, ensure_ascii=False, indent=2))
print("seeded")
PY
```

> 更省事的做法：本机先跑 `python tools/seed_mcp_demo.py --repository data/repository.json`，
> 再把 `data/repository.json` 一起打包上传。

## 6. 生成自签证书（含 IP SAN）

```bash
cd /opt/zhixue
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout key.pem -out cert.pem \
  -subj "/CN=<公网IP>" \
  -addext "subjectAltName=IP:<公网IP>"
```

必须带 `subjectAltName=IP:...`。只写 CN 的证书，现代 TLS 客户端会直接判为不匹配。

## 7. 启动服务

```bash
cd /opt/zhixue
export MCP_API_KEY="$(openssl rand -hex 16)"   # 记下这个值，平台侧要填一样的
echo "MCP_API_KEY=$MCP_API_KEY" > /opt/zhixue/mcp.env
chmod 600 /opt/zhixue/mcp.env

nohup python3 -m zhixue_mcp.server \
  --host 0.0.0.0 --port 8443 \
  --cert cert.pem --key key.pem \
  > /opt/zhixue/mcp.log 2>&1 &

sleep 2; tail -5 /opt/zhixue/mcp.log
# 期望：MCP 服务已启动：https://0.0.0.0:8443/mcp （工具 11 个）
```

> 想常驻（SSH 断开也不停）就写 systemd 服务；临时试跑 `nohup` 够用。

## 8. 本机先自测（别急着上平台）

在**本机** PowerShell：

```powershell
# 证书是自签的，curl 要 -k
curl.exe -k -s https://<公网IP>:8443/healthz -H "X-API-Key: <你的Key>"
# 期望 {"status":"ok",...,"tools":11,...}

curl.exe -k -s -X POST https://<公网IP>:8443/mcp `
  -H "Content-Type: application/json" -H "X-API-Key: <你的Key>" `
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'
# 期望返回 11 个工具的 JSON
```

再调一个真实工具：

```powershell
curl.exe -k -s -X POST https://<公网IP>:8443/mcp `
  -H "Content-Type: application/json" -H "X-API-Key: <你的Key>" `
  -d '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"get_learning_context\",\"arguments\":{\"sessionRef\":\"sref-demo-0000000000000001\"}}}'
# 期望 ok:true，data.goal = "过数据结构期末"
```

**这三条都通过，才说明服务器侧没问题；后面不通就一定是平台侧策略。**

## 9. 平台侧填地址

公测通过后：开发中心 → MCP → 创建 MCP → **streamableHttp** →

- 地址：`https://<公网IP>:8443/mcp`
- 鉴权：**API Key**，填第 7 步的 `MCP_API_KEY`
- 保存 → 观察是否自动发现 11 个工具

## 10. 不通怎么办

| 平台报错 | 含义 | 处理 |
|---|---|---|
| 证书校验失败 / SSL error | 平台不信任自签证书 | 放弃 IP 方案，换域名 + 正式证书 |
| URL 格式不支持 | 平台要求域名 | 同上 |
| 连接超时 | 安全组没放行 / 没监听 0.0.0.0 | 先用第 8 步自测确认 |
| 401 | Key 不一致 | 核对 `mcp.env` 与平台填写值 |
| 能连但 0 个工具 | 响应格式或路径问题 | `curl` 看 `tools/list` 是否正常；确认路径是 `/mcp` |

## 11. 收尾（重要）

```bash
# 停服务
pkill -f zhixue_mcp.server
```

然后回控制台 **释放 ECS 实例**（按量付费不释放会持续计费），并删除安全组规则。

> 别把 `mcp.env`、`cert.pem`、`key.pem` 提交到仓库或发到群里。

---

## 一句提醒

用 8443 而不是 80/443，是为了**做联调测试**，不是对外提供网站服务。正式演示（尤其涉及评委长期访问）应当用**已备案域名**或**香港/海外节点**，这条口径在答辩时也要能说清楚：当前阶段是内部联调，正式地址随后替换。
