# -*- coding: utf-8 -*-
"""验证「智能体运行时」调用是否打通（不依赖 Flask、不依赖后端依赖环境）。

用法（在仓库根目录）::

    python tools/check_agentarts_runtime.py ^
      --host defaultgw-gnsgmnbygy.cn-southwest-2.huaweicloud-agentarts.com ^
      --runtime agent-arts-2fa786ab339c425b835c784e21a12166 ^
      --key <工作负载身份 API Key>

它会：
1. 打印将要请求的完整地址（避免"URL 拼错"这种低级问题）
2. 依次尝试 4 种请求体形态（平台版本差异），**只有 4xx 才换下一个**
3. 打印平台返回的事件序列，并告诉你最终哪种 body 被接受

退出码：0 打通；2 参数缺失；3 鉴权失败；4 其他失败。
"""
import argparse
import json
import sys
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server" / "zhixue-agent-server"
sys.path.insert(0, str(SERVER))

# 用空壳 `app` 包绕开 app/__init__.py —— 那里 import 了 flask/dotenv，
# 而本脚本只需要 agentarts 这几个纯标准库模块。这样在没装完整依赖的机器上也能跑。
if "app" not in sys.modules:
    _stub = types.ModuleType("app")
    _stub.__path__ = [str(SERVER / "app")]
    sys.modules["app"] = _stub

from app.agentarts import AgentArtsClient, AgentArtsConfig, IamTokenProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentArts 运行时调用自检")
    parser.add_argument("--host", required=True, help="访问域名，如 defaultgw-xxx.cn-southwest-2.huaweicloud-agentarts.com")
    parser.add_argument("--runtime", required=True, help="运行时名称，如 agent-arts-2fa786ab...")
    parser.add_argument("--key", required=True, help="工作负载身份 API Key（Authorization: Bearer 的值）")
    parser.add_argument("--session", default="0123456789abcdef0123456789abcdef",
                        help="会话 ID：1~32 位，仅小写字母/数字/中划线")
    parser.add_argument("--query", default="你好，请用一句话介绍你自己")
    parser.add_argument("--session-ref", default="", help="可选：一并尝试透传 sessionRef")
    args = parser.parse_args()

    session_id = args.session[:32]
    config = AgentArtsConfig(
        enabled=True, mode="cloud", api_style="runtime", region="cn-southwest-2",
        iam_user="", iam_domain="", iam_password="", project_id="",
        workspace_id="default", agent_id="", agent_version="",
        invoke_mode="published", endpoint=args.host, timeout_seconds=120,
        runtime_name=args.runtime, api_key=args.key,
    )

    print("运行时调用自检")
    print("  地址   : %s" % config.invocation_url())
    print("  会话 ID: %s（长度 %d）" % (session_id, len(session_id)))
    print("  Key    : %s...（长度 %d）" % (args.key[:8], len(args.key)))
    print()

    client = AgentArtsClient(config, IamTokenProvider(config))
    try:
        client.ensure_ready()
    except Exception as error:  # noqa: BLE001
        print("  [X] 凭据不可用：%s" % (error,))
        return 2

    events = []
    try:
        for event in client.run_agent(session_id, args.query,
                                      {"sessionRef": args.session_ref} if args.session_ref else None):
            events.append(event)
            name = event.get("event") or "?"
            # 全部事件都打出来：排障时最怕"事件到了但没显示"，看不出到底是哪种。
            print("\n  [事件] %s" % name)
            if name == "message":
                print("         内容: %s" % (event.get("content") or ""))
            elif name == "summary_response":
                print("         全文: %s" % (event.get("content") or ""))
            elif name == "plugin_start":
                print("         工具: %s" % ((event.get("plugin") or {}).get("name") or "?"))
                print("         入参: %s" % json.dumps(
                    (event.get("plugin") or {}).get("arguments") or {}, ensure_ascii=False)[:200])
            elif name == "plugin_end":
                print("         工具: %s" % ((event.get("plugin") or {}).get("name") or "?"))
            elif name == "statistic_data":
                print("         耗时: %s" % json.dumps(event.get("latency") or {}, ensure_ascii=False))
            elif name == "start":
                print("         会话开始")
            elif name == "done":
                print("         结束")
            else:
                print("         原始: %s" % json.dumps(event, ensure_ascii=False)[:300])
    except Exception as error:  # noqa: BLE001
        print("\n  [X] 调用失败：%s" % (error,))
        status = getattr(error, "status", 0)
        body = getattr(error, "body", "")
        if body:
            print("      平台返回：%s" % body[:400])
        if status in (401, 403):
            print("      结论：API Key 不对，或该身份没有绑定到这个运行时。")
            return 3
        if status == 404:
            print("      结论：运行时名称或域名不对，检查访问地址里的 /runtimes/<名称>/invocations。")
        return 4

    print()
    print("=" * 60)
    if not events:
        print("  [!] 请求成功但没有任何事件，检查请求体是否符合平台要求。")
        return 4

    errors = [item for item in events if item.get("event") == "error"]
    if errors:
        data = errors[0].get("data") if isinstance(errors[0].get("data"), dict) else {}
        message = str(data.get("message") or "")
        print("  [X] 平台执行失败（HTTP 是 200，但业务失败）")
        print("      code   : %s" % data.get("code"))
        print("      message: %s" % message[:400])
        if "authorization header" in message or "100025" in message:
            print("      结论：运行时没有模型鉴权 → 部署时的「模型 API Key」没填或失效。")
            print("      处理：去 MaaS 创建 API Key，然后在运行时「编辑」里补上（或重新部署）。")
        elif "tool" in message.lower():
            print("      结论：工具调用失败，检查 MCP 地址是否可从平台访问。")
        return 4

    print("  [OK] 打通：收到 %d 个事件" % len(events))
    if client.last_body is not None:
        print("  被接受的请求体：%s" % json.dumps(client.last_body, ensure_ascii=False))
    print("  接下来把 host / runtime / key 写进网关 .env 即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
