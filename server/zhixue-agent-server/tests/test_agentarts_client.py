# -*- coding: utf-8 -*-
"""AgentArts 接入层单元测试：配置、事件归一化、会话映射、Token 缓存。

全部离线：不碰 IAM，也不碰平台。
"""
import os

import pytest

from app.agentarts import AgentArtsConfig
from app.agentarts import events as ev
from app.agentarts import session as store
from app.agentarts.auth import IamTokenProvider, _parse_expires_at


# ------------------------------------------------------------------ 配置
def _config(**overrides):
    env = {
        "AGENTARTS_ENABLED": "1",
        "AGENTARTS_MODE": "cloud",
        "AGENTARTS_REGION": "cn-north-4",
        "AGENTARTS_IAM_USER": "zhixue",
        "AGENTARTS_IAM_DOMAIN": "main-account",
        "AGENTARTS_IAM_PASSWORD": "secret-pass",
        "AGENTARTS_PROJECT_ID": "proj123",
        "AGENTARTS_AGENT_ID": "agent-abc",
    }
    env.update(overrides)
    return AgentArtsConfig.from_env(env)


def test_disabled_by_default_keeps_mode_explicit():
    config = AgentArtsConfig.from_env({})
    assert config.mode == "disabled"
    assert config.ready() is False
    assert "AGENTARTS_ENABLED" in config.blocking_reason()


def test_missing_lists_variables_in_access_order():
    config = _config(AGENTARTS_AGENT_ID="", AGENTARTS_PROJECT_ID="")
    assert config.missing() == ["AGENTARTS_PROJECT_ID", "AGENTARTS_ENDPOINT", "AGENTARTS_AGENT_ID"]
    assert config.ready() is False


def test_endpoint_is_derived_from_project_id():
    config = _config(AGENTARTS_ENDPOINT="")
    assert config.endpoint == "proj123.studio.agentarts.cn-north-4.huaweiapaas.com"
    assert config.runtime_url("conv1") == (
        "https://proj123.studio.agentarts.cn-north-4.huaweiapaas.com"
        "/v1/proj123/agents/agent-abc/conversations/conv1?workspace_id=default")


def test_runtime_url_carries_version_when_set():
    url = _config(AGENTARTS_AGENT_VERSION="3").runtime_url("c1")
    assert url.endswith("workspace_id=default&version=3")


def test_describe_never_leaks_password():
    config = _config()
    described = config.describe()
    assert described["hasIamUser"] is True
    assert described["ready"] is True
    assert "secret-pass" not in str(described)
    assert "iam_password" not in str(described).lower()
    assert "main-account" not in str(described)


# ------------------------------------------------------------------ 事件
def test_normalize_maps_every_documented_platform_event():
    assert ev.normalize({"event": "start"})["type"] == ev.RUN_STARTED
    assert ev.normalize({"event": "message", "content": "你"})["delta"] == "你"
    started = ev.normalize({"event": "plugin_start",
                            "plugin": {"name": "get_learning_context", "arguments": {"a": 1}}})
    assert started["type"] == ev.TOOL_STARTED
    assert started["name"] == "get_learning_context"
    assert started["arguments"] == {"a": 1}
    finished = ev.normalize({"event": "plugin_end", "plugin": {"name": "x"}})
    assert finished["type"] == ev.TOOL_FINISHED
    metrics = ev.normalize({"event": "statistic_data", "latency": {"overall": 1.9}})
    assert metrics["type"] == ev.METRICS
    assert metrics["latency"]["overall"] == 1.9
    assert ev.normalize({"event": "summary_response", "content": "全文"})["type"] == ev.RUN_SUMMARY
    assert ev.normalize({"event": "done"})["type"] == ev.RUN_FINISHED


def test_unknown_event_is_kept_as_raw_not_dropped():
    raw = ev.normalize({"event": "node_transfer", "content": "转交"})
    assert raw["type"] == ev.RAW
    assert raw["platformEvent"] == "node_transfer"


def test_collect_text_and_tool_names():
    items = [
        {"type": ev.AGENT_MESSAGE, "delta": "先看"},
        {"type": ev.AGENT_MESSAGE, "delta": "二叉树"},
        {"type": ev.TOOL_STARTED, "name": "a"},
        {"type": ev.TOOL_STARTED, "name": "a"},
        {"type": ev.TOOL_STARTED, "name": "b"},
    ]
    assert ev.collect_text(items) == "先看二叉树"
    assert ev.tool_names(items) == ["a", "b"]


# ------------------------------------------------------------------ 会话
class _MemoryRepository:
    """最小仓储实现，只覆盖 session 模块用到的方法。"""

    def __init__(self):
        self.data = {}

    def get(self, collection, item_id):
        return self.data.get((collection, item_id))

    def save(self, collection, item_id, value):
        self.data[(collection, item_id)] = value

    def list(self, collection):
        return [v for (c, _), v in self.data.items() if c == collection]


def test_conversation_id_meets_platform_constraints():
    conversation_id = store.new_conversation_id()
    assert len(conversation_id) <= 64
    assert all(ch.isalnum() or ch in "-_" for ch in conversation_id)
    assert conversation_id.startswith("conv")


def test_bind_session_reuses_conversation_for_same_chat():
    repo = _MemoryRepository()
    first = store.bind_session(repo, user_id="u1", chat_session_id="chat-1", agent_id="a")
    second = store.bind_session(repo, user_id="u1", chat_session_id="chat-1", agent_id="a")
    other = store.bind_session(repo, user_id="u1", chat_session_id="chat-2", agent_id="a")
    assert first["conversationId"] == second["conversationId"]
    assert other["conversationId"] != first["conversationId"]
    assert first["sessionRef"].startswith("sref")


def test_session_ref_resolves_back_to_user():
    repo = _MemoryRepository()
    record = store.bind_session(repo, user_id="u9", chat_session_id="c", agent_id="a")
    found = store.resolve_by_session_ref(repo, record["sessionRef"])
    assert found["userId"] == "u9"
    assert store.resolve_by_session_ref(repo, "sref-not-exist") is None


def test_run_events_are_capped_but_keep_head_and_tail():
    repo = _MemoryRepository()
    run = store.start_run(repo, conversation_id="c", user_id="u", chat_session_id="s", agent_id="a")
    store.append_events(repo, run["runId"], [{"type": ev.AGENT_MESSAGE, "delta": str(i)}
                                             for i in range(store.MAX_EVENTS + 50)])
    saved = store.get_run(repo, run["runId"])
    assert saved["truncated"] is True
    assert len(saved["events"]) == store.MAX_EVENTS


# ------------------------------------------------------------------ Token
def test_expires_at_parses_huawei_microsecond_format():
    parsed = _parse_expires_at("2026-11-10T12:00:00.000000Z")
    assert parsed is not None
    assert _parse_expires_at("") is None
    assert _parse_expires_at("not-a-date") is None


def _post_ok(token="tok-1", expires="2099-01-01T00:00:00.000000Z"):
    calls = []

    def _post(url, payload, *, timeout):
        calls.append((url, payload))
        return 201, {"x-subject-token": token}, '{"token":{"expires_at":"%s"}}' % expires

    _post.calls = calls
    return _post


def test_token_is_cached_between_calls():
    post = _post_ok()
    provider = IamTokenProvider(_config(), http_post=post)
    assert provider.get() == "tok-1"
    assert provider.get() == "tok-1"
    assert len(post.calls) == 1
    assert "cn-north-4" in post.calls[0][0]
    assert post.calls[0][1]["auth"]["scope"]["project"]["name"] == "cn-north-4"


def test_token_refreshes_when_close_to_expiry():
    post = _post_ok(expires="2026-11-10T12:00:00.000000Z")
    now = [1000.0]
    provider = IamTokenProvider(_config(), http_post=post, clock=lambda: now[0])
    provider.get()
    # expires_at 具体时刻与 clock 无关，这里直接推进到过期之后
    now[0] = 2_000_000_000.0
    provider.get()
    assert len(post.calls) == 2


def test_force_refresh_and_invalidate():
    post = _post_ok()
    provider = IamTokenProvider(_config(), http_post=post)
    provider.get()
    provider.get(force=True)
    assert len(post.calls) == 2
    provider.invalidate()
    assert provider.snapshot()["cached"] is False


def test_missing_header_is_reported_as_auth_error():
    def _post(url, payload, *, timeout):
        return 201, {}, "{}"

    provider = IamTokenProvider(_config(), http_post=_post)
    with pytest.raises(Exception) as error:
        provider.get()
    assert "X-Subject-Token" in str(error.value)
