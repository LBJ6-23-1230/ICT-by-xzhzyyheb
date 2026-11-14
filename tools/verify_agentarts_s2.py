# -*- coding: utf-8 -*-
"""S2 网关的**无依赖**自检。

为什么需要它
------------
后端完整依赖锁在 Python 3.12（`requirements-lock-py312.txt`），开发机若只有 3.14，
`pip install` 会卡在少数二进制轮子上。但 S2 的核心逻辑（配置、事件归一化、会话映射、
Token 缓存）**一行第三方库都不需要**——只有 `api/agentarts_api.py` 用到 Flask。

所以这里用"空壳 app 包 + 按路径加载"绕开 `app/__init__.py`（那里 import flask/dotenv），
直接验证四个纯逻辑模块；再用 `py_compile` 检查全部新增/改动文件的语法。
这样环境装不好也能确认"代码本身是对的"，等 3.12 环境就绪再跑完整 pytest。

用法::

    python tools/verify_agentarts_s2.py

退出码 0 = 全通过；1 = 有断言失败。
"""
import importlib.util
import json
import py_compile
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server" / "zhixue-agent-server"

failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print("  [OK] %s" % label)
    else:
        print("  [X]  %s%s" % (label, ("  -> " + detail) if detail else ""))
        failures.append(label)


def load_modules():
    """用空壳 `app` 包按文件路径加载，避免触发 app/__init__.py 的第三方依赖。"""
    package = types.ModuleType("app")
    package.__path__ = [str(SERVER / "app")]
    sys.modules["app"] = package

    spec = importlib.util.spec_from_file_location(
        "app.agentarts",
        SERVER / "app" / "agentarts" / "__init__.py",
        submodule_search_locations=[str(SERVER / "app" / "agentarts")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["app.agentarts"] = module
    spec.loader.exec_module(module)
    # `session` 不在包的 __init__ 里导出（业务代码按需 import），这里显式加载，
    # 否则 sys.modules 里没有 "app.agentarts.session"。
    for name in ("config", "auth", "client", "events", "session"):
        importlib.import_module("app.agentarts." + name)
    return module


def verify_config(agentarts):
    print("\n[1/5] 配置三态与缺项判定")
    disabled = agentarts.AgentArtsConfig.from_env({})
    check("默认未开启时 mode=disabled", disabled.mode == "disabled", disabled.mode)
    check("disabled 时 ready 为 False", disabled.ready() is False)
    check("disabled 时给出人话原因", "AGENTARTS_ENABLED" in disabled.blocking_reason())

    env = {
        "AGENTARTS_ENABLED": "1",
        "AGENTARTS_IAM_USER": "zhixue",
        "AGENTARTS_IAM_DOMAIN": "main-account",
        "AGENTARTS_IAM_PASSWORD": "secret-pass",
        "AGENTARTS_PROJECT_ID": "proj123",
        "AGENTARTS_AGENT_ID": "agent-abc",
    }
    config = agentarts.AgentArtsConfig.from_env(env)
    check("配置齐全时 mode=cloud 且 ready", config.mode == "cloud" and config.ready())
    check("endpoint 由 project_id 推导",
          config.endpoint == "proj123.studio.agentarts.cn-north-4.huaweiapaas.com",
          config.endpoint)
    check("runtime_url 结构正确", config.runtime_url("conv1").endswith(
        "/v1/proj123/agents/agent-abc/conversations/conv1?workspace_id=default"),
        config.runtime_url("conv1"))

    partial = agentarts.AgentArtsConfig.from_env({**env, "AGENTARTS_AGENT_ID": ""})
    check("缺 agent_id 时列出缺项", partial.missing() == ["AGENTARTS_AGENT_ID"], str(partial.missing()))
    check("配置不全时 ready 为 False", partial.ready() is False)

    described = json.dumps(config.describe(), ensure_ascii=False)
    check("describe 不泄露密码", "secret-pass" not in described)
    check("describe 不泄露主账号名", "main-account" not in described)
    check("describe 暴露 hasIamUser 布尔", '"hasIamUser": true' in described)


def verify_events(agentarts):
    print("\n[2/5] 平台事件 → 内部事件")
    ev = sys.modules["app.agentarts.events"]
    check("start → run_started", ev.normalize({"event": "start"})["type"] == "run_started")
    message = ev.normalize({"event": "message", "content": "你好"})
    check("message → agent_message 且保留增量", message["type"] == "agent_message"
          and message["delta"] == "你好")
    started = ev.normalize({"event": "plugin_start",
                            "plugin": {"name": "get_learning_context",
                                       "arguments": {"sessionRef": "sref-1"}}})
    check("plugin_start → tool_started 带工具名", started["type"] == "tool_started"
          and started["name"] == "get_learning_context")
    check("plugin_start 保留入参", started["arguments"] == {"sessionRef": "sref-1"})
    check("plugin_end → tool_finished",
          ev.normalize({"event": "plugin_end", "plugin": {"name": "x"}})["type"] == "tool_finished")
    metrics = ev.normalize({"event": "statistic_data", "latency": {"overall": 1.9}})
    check("statistic_data → metrics 提取耗时", metrics["type"] == "metrics"
          and metrics["latency"]["overall"] == 1.9)
    check("summary_response → run_summary",
          ev.normalize({"event": "summary_response", "content": "全文"})["type"] == "run_summary")
    check("done → run_finished", ev.normalize({"event": "done"})["type"] == "run_finished")
    raw = ev.normalize({"event": "node_transfer", "content": "转交"})
    check("未知事件不丢弃（落 raw）", raw["type"] == "raw" and raw["platformEvent"] == "node_transfer")

    items = [{"type": "agent_message", "delta": "先看"},
             {"type": "agent_message", "delta": "二叉树"},
             {"type": "tool_started", "name": "a"},
             {"type": "tool_started", "name": "a"},
             {"type": "tool_started", "name": "b"}]
    check("collect_text 拼接增量", ev.collect_text(items) == "先看二叉树")
    check("tool_names 去重保序", ev.tool_names(items) == ["a", "b"])


class MemoryRepository:
    def __init__(self):
        self.data = {}

    def get(self, collection, item_id):
        return self.data.get((collection, item_id))

    def save(self, collection, item_id, value):
        self.data[(collection, item_id)] = value

    def list(self, collection):
        return [value for (name, _), value in self.data.items() if name == collection]


def verify_session():
    print("\n[3/5] 会话映射与运行记录")
    store = sys.modules["app.agentarts.session"]
    repo = MemoryRepository()

    conversation_id = store.new_conversation_id()
    check("conversationId ≤64 字符", len(conversation_id) <= 64, str(len(conversation_id)))
    check("conversationId 字符合法",
          all(ch.isalnum() or ch in "-_" for ch in conversation_id), conversation_id)

    first = store.bind_session(repo, user_id="u1", chat_session_id="chat-1", agent_id="a")
    again = store.bind_session(repo, user_id="u1", chat_session_id="chat-1", agent_id="a")
    other = store.bind_session(repo, user_id="u1", chat_session_id="chat-2", agent_id="a")
    check("同一聊天会话复用平台会话", first["conversationId"] == again["conversationId"])
    check("不同聊天会话相互独立", other["conversationId"] != first["conversationId"])
    check("sessionRef 可用一次随机串换回用户",
          store.resolve_by_session_ref(repo, first["sessionRef"])["userId"] == "u1")
    check("伪造 sessionRef 查不到身份",
          store.resolve_by_session_ref(repo, "sref-forged") is None)

    run = store.start_run(repo, conversation_id="c", user_id="u1",
                          chat_session_id="chat-1", agent_id="a")
    check("运行记录初始状态 running", run["status"] == "running")
    store.append_events(repo, run["runId"],
                        [{"type": "agent_message", "delta": str(i)}
                         for i in range(store.MAX_EVENTS + 50)])
    saved = store.get_run(repo, run["runId"])
    check("事件总量被截断到上限", len(saved["events"]) == store.MAX_EVENTS, str(len(saved["events"])))
    check("被截断时有显式标记", saved["truncated"] is True)
    done = store.finish_run(repo, run["runId"], status="completed", reply="答复",
                            tools=["get_learning_context"])
    check("结束后落库状态与工具清单", done["status"] == "completed"
          and done["tools"] == ["get_learning_context"] and done["reply"] == "答复")
    check("回放支持 after 游标", len(store.events_since(repo, run["runId"], 10))
          == len(saved["events"]) - 10)


def verify_token():
    print("\n[4/5] IAM Token 缓存")
    auth = sys.modules["app.agentarts.auth"]
    config = sys.modules["app.agentarts"].AgentArtsConfig.from_env({
        "AGENTARTS_ENABLED": "1", "AGENTARTS_IAM_USER": "u", "AGENTARTS_IAM_DOMAIN": "d",
        "AGENTARTS_IAM_PASSWORD": "p", "AGENTARTS_PROJECT_ID": "proj",
        "AGENTARTS_AGENT_ID": "a",
    })

    calls = []

    def post_ok(url, payload, *, timeout):
        calls.append((url, payload))
        return 201, {"x-subject-token": "tok-1"}, '{"token":{"expires_at":"2099-01-01T00:00:00.000000Z"}}'

    provider = auth.IamTokenProvider(config, http_post=post_ok)
    check("首次取 Token 成功", provider.get() == "tok-1")
    check("重复调用命中缓存（只请求一次）", provider.get() == "tok-1" and len(calls) == 1,
          "calls=%d" % len(calls))
    check("请求的是 IAM 项目级 Token 接口",
          calls[0][0] == "https://iam.cn-north-4.myhuaweicloud.com/v3/auth/tokens", calls[0][0])
    check("scope 为 project（平台要求）",
          calls[0][1]["auth"]["scope"]["project"]["name"] == "cn-north-4")
    check("强制刷新会重新请求", provider.get(force=True) == "tok-1" and len(calls) == 2)

    snapshot = provider.snapshot()
    check("snapshot 只报状态不报 Token", "tok-1" not in json.dumps(snapshot), json.dumps(snapshot))

    def post_broken(url, payload, *, timeout):
        return 401, {}, '{"error_msg":"invalid user"}'

    broken = auth.IamTokenProvider(config, http_post=post_broken)
    try:
        broken.get()
        check("IAM 401 会抛错", False)
    except auth.AgentArtsAuthError as error:
        check("IAM 401 会抛错且带状态码", error.status == 401, str(error.status))


def verify_syntax():
    print("\n[5/5] 语法检查（不导入，故不依赖第三方库）")
    targets = [
        SERVER / "app" / "__init__.py",
        SERVER / "app" / "api" / "chat.py",
        SERVER / "app" / "api" / "agentarts_api.py",
        SERVER / "run.py",
        SERVER / "app" / "agentarts" / "__init__.py",
        SERVER / "app" / "agentarts" / "config.py",
        SERVER / "app" / "agentarts" / "auth.py",
        SERVER / "app" / "agentarts" / "client.py",
        SERVER / "app" / "agentarts" / "events.py",
        SERVER / "app" / "agentarts" / "session.py",
        SERVER / "tests" / "test_agentarts_client.py",
        SERVER / "tests" / "test_agentarts_gateway.py",
        SERVER / "tests" / "test_agentarts_degradation.py",
        ROOT / "tools" / "check_agentarts.py",
    ]
    for path in targets:
        try:
            py_compile.compile(str(path), doraise=True, cfile=str(path) + ".pyc.tmp")
            check("编译通过 %s" % path.relative_to(ROOT), True)
        except Exception as error:  # noqa: BLE001
            check("编译通过 %s" % path.relative_to(ROOT), False, str(error)[:160])
    for leftover in ROOT.rglob("*.pyc.tmp"):
        leftover.unlink(missing_ok=True)


def main() -> int:
    print("AgentArts S2 无依赖自检  ·  %s" % SERVER)
    agentarts = load_modules()
    verify_config(agentarts)
    verify_events(agentarts)
    verify_session()
    verify_token()
    verify_syntax()

    print("\n" + "=" * 64)
    if failures:
        print("  失败 %d / %d 项" % (len(failures), checks))
        for item in failures:
            print("    - %s" % item)
        return 1
    print("  全部通过：%d 项检查" % checks)
    print("  说明：Flask 路由与 SSE 组装需 Python 3.12 完整依赖，用 pytest 验证：")
    print("        python -m pytest tests/test_agentarts_*.py -q")
    return 0


if __name__ == "__main__":
    sys.exit(main())
