# -*- coding: utf-8 -*-
"""AgentArts 连通性自检：先验 IAM 取 Token，再验运行时流式调用。

为什么要有这个脚本
----------------
接入阶段最常见的两种"假故障"：

1. **Token 取不到**，却以为是 AgentArts 没开通。实际多为 domain 名写成了 IAM 用户名、
   或用户组没授权。
2. **Token 取到了、调用却 404**，却以为是接口路径错。实际多为 endpoint 前缀不是
   project_id，或 agent_id 用了调试版 ID。

本脚本把这两步分开验证，并给出可执行的判断结论，避免把"配置错"误判成"平台不可用"。

用法（在仓库根目录）::

    python tools/check_agentarts.py                 # 只验 Token
    python tools/check_agentarts.py --invoke        # Token + 运行时流式调用
    python tools/check_agentarts.py --invoke --query "明天交数据结构作业，今晚60分钟"

退出码：0 全通；2 配置缺失；3 Token 失败；4 运行时调用失败。

安全约定：**任何情况下都不打印 Token 全文**，只打印长度与过期时间。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENV_PATH = os.path.join(ROOT, "server", "zhixue-agent-server", ".env")

try:
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)
except Exception:  # noqa: BLE001 - 没装 dotenv 也不该挡住自检
    pass


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


REGION = env("AGENTARTS_REGION", "cn-north-4")
IAM_USER = env("AGENTARTS_IAM_USER")
IAM_PASSWORD = env("AGENTARTS_IAM_PASSWORD")
IAM_DOMAIN = env("AGENTARTS_IAM_DOMAIN")
PROJECT_ID = env("AGENTARTS_PROJECT_ID")
WORKSPACE_ID = env("AGENTARTS_WORKSPACE_ID", "default")
AGENT_ID = env("AGENTARTS_AGENT_ID")
AGENT_VERSION = env("AGENTARTS_AGENT_VERSION")
INVOKE_MODE = env("AGENTARTS_INVOKE_MODE", "published")
# endpoint 默认按官方格式拼：<project_id>.studio.agentarts.<region>.huaweiapaas.com
ENDPOINT = env("AGENTARTS_ENDPOINT") or (
    "%s.studio.agentarts.%s.huaweiapaas.com" % (PROJECT_ID, REGION) if PROJECT_ID else ""
)

TOKEN_URL = "https://iam.%s.myhuaweicloud.com/v3/auth/tokens" % REGION


def post(url: str, payload: dict, headers: dict, timeout: int):
    """返回 (status, headers, body)；网络层失败时 status=0。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, {k.lower(): v for k, v in response.headers.items()}, body
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        return error.code, {k.lower(): v for k, v in error.headers.items()}, body
    except urllib.error.URLError as error:
        return 0, {}, str(error.reason)


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return "（空）"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep)


def check_config(invoke: bool) -> bool:
    required = [
        ("AGENTARTS_IAM_USER", IAM_USER),
        ("AGENTARTS_IAM_PASSWORD", IAM_PASSWORD),
        ("AGENTARTS_IAM_DOMAIN", IAM_DOMAIN),
        ("AGENTARTS_PROJECT_ID", PROJECT_ID),
    ]
    if invoke:
        required.append(("AGENTARTS_AGENT_ID", AGENT_ID))

    missing = [name for name, value in required if not value]
    print("  配置检查：")
    print("    %-26s %s" % ("IAM 用户", IAM_USER or "（未配置）"))
    print("    %-26s %s" % ("主账号 domain", IAM_DOMAIN or "（未配置）"))
    print("    %-26s %s" % ("region", REGION))
    print("    %-26s %s" % ("project_id", PROJECT_ID or "（未配置）"))
    print("    %-26s %s" % ("endpoint", ENDPOINT or "（未配置）"))
    print("    %-26s %s" % ("agent_id", AGENT_ID or "（未配置）"))
    print("    %-26s %s" % ("调用模式", INVOKE_MODE))

    if missing:
        print()
        print("  [X] 缺少配置：%s" % "、".join(missing))
        print("      在 server/zhixue-agent-server/.env 中补齐（模板见 .env.example），"
              "不要把密码写在代码或命令行里。")
        return False
    return True


def fetch_token():
    payload = {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {
                    "user": {
                        "name": IAM_USER,
                        "password": IAM_PASSWORD,
                        "domain": {"name": IAM_DOMAIN},
                    }
                },
            },
            "scope": {"project": {"name": REGION}},
        }
    }
    started = time.time()
    status, headers, body = post(
        TOKEN_URL, payload, {"Content-Type": "application/json"}, timeout=20)

    if status in (200, 201):
        token = headers.get("x-subject-token", "")
        if not token:
            print("  [X] 返回成功但没有 X-Subject-Token，检查 region 是否为 cn-north-4")
            return None
        expires = ""
        try:
            expires = (json.loads(body).get("token") or {}).get("expires_at", "")
        except ValueError:
            pass
        print("  [OK] 取 Token 成功  %.2fs  token 长度=%d  过期时间=%s"
              % (time.time() - started, len(token), expires or "未知"))
        return token

    print("  [X] 取 Token 失败  HTTP %s  %.2fs" % (status or "网络", time.time() - started))
    print("      响应：%s" % body[:400])
    if status == 401:
        print("      结论：用户名/密码/domain 三者之一不对。注意 domain 填**主账号名**，"
              "不是 zhixue。若已开启登录保护（MFA），密码换取 Token 可能被拒绝，"
              "需要对该用户关闭登录保护，或改用控制台手工取 Token 应急。")
    elif status == 403:
        print("      结论：用户被禁用或未授权。检查 IAM 用户状态与用户组授权。")
    elif status == 0:
        print("      结论：网络不通。检查能否访问 iam.%s.myhuaweicloud.com。" % REGION)
    return None


def invoke_agent(token: str, query: str) -> bool:
    conversation_id = "selfcheck-%d" % int(time.time())
    url = ("https://%s/v1/%s/agents/%s/conversations/%s?workspace_id=%s"
           % (ENDPOINT, PROJECT_ID, AGENT_ID, conversation_id, WORKSPACE_ID))
    if AGENT_VERSION:
        url += "&version=%s" % AGENT_VERSION

    payload = {"query": query, "inputs": {"sessionRef": conversation_id}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "X-Auth-Token": token,
        "X-Invoke-Mode": INVOKE_MODE,
        "stream": "true",
        "Accept": "text/event-stream",
    })

    print()
    print("  运行时调用（流式）：")
    events = []
    preview = []
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    item = json.loads(chunk)
                except ValueError:
                    continue
                event = str(item.get("event") or "")
                if event and event not in events:
                    events.append(event)
                if event == "message":
                    preview.append(str(item.get("content") or ""))
                if event == "plugin_start":
                    name = (item.get("plugin") or {}).get("name") or "?"
                    print("    [工具] %s" % name)
                if event == "done":
                    break
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        print("  [X] HTTP %s：%s" % (error.code, body[:400]))
        if error.code == 401:
            print("      结论：Token 无效或过期，或该用户没有 AgentArts 调用权限。")
        elif error.code == 404:
            print("      结论：路径或 ID 不对。依次核对 endpoint 前缀是否等于 project_id、"
                  "agent_id 是否复制错、workspace_id 是否为 default。")
        elif error.code == 400:
            print("      结论：入参不匹配。检查智能体「开始节点」是否声明了 query / inputs 变量。")
        return False
    except Exception as error:  # noqa: BLE001
        print("  [X] 调用异常：%r" % (error,))
        return False

    elapsed = time.time() - started
    text = "".join(preview).strip()
    print("  [OK] 调用完成  %.2fs  事件序列=%s" % (elapsed, "→".join(events) or "（无）"))
    if text:
        print("      回复摘要：%s" % (text[:120] + ("…" if len(text) > 120 else "")))
    if "plugin_start" not in events:
        print("      [!] 本次没有工具调用事件。若该问句应当查数据，说明智能体没挂上 MCP 或"
              "提示词未指示调用工具——这是接入期最常见的问题之一。")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentArts 连通性自检")
    parser.add_argument("--invoke", action="store_true", help="同时验证运行时流式调用")
    parser.add_argument("--query", default="明天交数据结构作业，今晚只有 60 分钟，怎么安排？")
    args = parser.parse_args()

    print("AgentArts 自检  ·  region=%s" % REGION)
    print()
    if not check_config(invoke=args.invoke):
        return 2

    token = fetch_token()
    if not token:
        return 3

    if not args.invoke:
        print()
        print("  下一步：开通公测并创建智能体后，用 --invoke 验证运行时链路。")
        return 0

    return 0 if invoke_agent(token, args.query) else 4


if __name__ == "__main__":
    sys.exit(main())
