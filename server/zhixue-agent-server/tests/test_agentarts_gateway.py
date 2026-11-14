# -*- coding: utf-8 -*-
"""`/api/v2/agent/*` 网关行为测试：流式事件、运行回放、归属隔离、状态不泄露密钥。

客户端被替换成假实现，所以测试完全离线；但也因此**必须**保证假客户端走的是与真实
客户端相同的接口（ensure_ready / run_agent），否则测试通过而线上不通。
"""
import json

import pytest

from app import create_app
from app.agentarts import AgentArtsClient, AgentArtsConfig, AgentArtsError, IamTokenProvider
from app.api import agentarts_api as api_module
from app.api.agentarts_api import configure_agentarts
from app.agentarts import session as store

CLOUD_ENV = {
    "AGENTARTS_ENABLED": "1",
    "AGENTARTS_MODE": "cloud",
    "AGENTARTS_IAM_USER": "zhixue",
    "AGENTARTS_IAM_DOMAIN": "main-account",
    "AGENTARTS_IAM_PASSWORD": "secret-pass",
    "AGENTARTS_PROJECT_ID": "proj123",
    "AGENTARTS_AGENT_ID": "agent-abc",
    "AGENTARTS_AGENT_VERSION": "7",
}


class FakeClient:
    """与 AgentArtsClient 同形的假实现。"""

    def __init__(self, events=None, *, ready_error=None, fail_after=None):
        self.events = list(events or [])
        self.ready_error = ready_error
        self.fail_after = fail_after
        self.calls = []

    def ensure_ready(self):
        if self.ready_error:
            raise self.ready_error
        return "tok"

    def run_agent(self, conversation_id, query, inputs=None, *, timeout=None):
        self.calls.append({"conversationId": conversation_id, "query": query,
                           "inputs": dict(inputs or {})})
        for index, event in enumerate(self.events):
            if self.fail_after is not None and index >= self.fail_after:
                raise AgentArtsError("平台中断", status=500)
            yield event


PLATFORM_EVENTS = [
    {"event": "start", "createdTime": 1},
    {"event": "message", "content": "先看", "createdTime": 2},
    {"event": "message", "content": "二叉树", "createdTime": 3},
    {"event": "plugin_start", "plugin": {"name": "get_learning_context",
                                         "arguments": {"sessionRef": "sref-x"}}, "createdTime": 4},
    {"event": "plugin_end", "plugin": {"name": "get_learning_context"},
     "latency": {"plugin": 120}, "createdTime": 5},
    {"event": "statistic_data", "latency": {"overall": 1.9, "model": 1.2}, "createdTime": 6},
    {"event": "summary_response", "content": "先看二叉树", "role": "assistant", "createdTime": 7},
    {"event": "done", "createdTime": 8},
]


def parse_sse(text: str) -> list[dict]:
    parsed = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    parsed.append(json.loads(chunk))
    return parsed


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    for key, value in CLOUD_ENV.items():
        monkeypatch.setenv(key, value)
    app = create_app(tmp_path / "agentarts.json")
    config = AgentArtsConfig.from_env()
    fake = FakeClient(PLATFORM_EVENTS)
    configure_agentarts(app.extensions["repository"], config=config, client=fake)
    app.config["TEST_FAKE_CLIENT"] = fake
    return app


def test_chat_streams_normalized_events(cloud):
    response = cloud.test_client().post("/api/v2/agent/chat", json={"message": "今晚只有60分钟"})
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"

    events = parse_sse(response.get_data(as_text=True))
    types = [item["type"] for item in events]
    assert types[0] == "ready"
    assert "run_started" in types
    assert "agent_message" in types
    assert "tool_started" in types
    assert "tool_finished" in types
    assert "metrics" in types
    assert "run_summary" in types
    assert "run_finished" in types

    tool = next(item for item in events if item["type"] == "tool_started")
    assert tool["name"] == "get_learning_context"
    ready = events[0]
    assert ready["mode"] == "cloud"
    assert ready["agentVersion"] == "7"


def test_chat_passes_session_ref_into_agent_inputs(cloud):
    fake = cloud.config["TEST_FAKE_CLIENT"]
    cloud.test_client().post("/api/v2/agent/chat", json={"message": "hi", "sessionId": "chat-9"})
    assert fake.calls[0]["inputs"]["sessionRef"].startswith("sref")
    # 平台会话 ID 必须满足 [A-Za-z0-9-_]{1,64}
    conversation_id = fake.calls[0]["conversationId"]
    assert len(conversation_id) <= 64
    assert all(ch.isalnum() or ch in "-_" for ch in conversation_id)


def test_run_can_be_replayed_after_the_stream_closes(cloud):
    client = cloud.test_client()
    response = client.post("/api/v2/agent/chat", json={"message": "hi", "sessionId": "chat-1"})
    ready = parse_sse(response.get_data(as_text=True))[0]
    run_id = ready["runId"]

    replay = client.get("/api/v2/agent/runs/%s" % run_id)
    assert replay.status_code == 200
    body = replay.get_json()
    assert body["status"] == "completed"
    assert body["reply"] == "先看二叉树"
    assert body["tools"] == ["get_learning_context"]
    assert body["total"] >= 8

    partial = client.get("/api/v2/agent/runs/%s?after=6" % run_id).get_json()
    assert partial["after"] == 6
    assert len(partial["events"]) == body["total"] - 6


def test_replay_of_other_users_run_is_404(cloud):
    repository = cloud.extensions["repository"]
    run = store.start_run(repository, conversation_id="c", user_id="someone-else",
                          chat_session_id="s", agent_id="a")
    response = cloud.test_client().get("/api/v2/agent/runs/%s" % run["runId"])
    assert response.status_code == 404
    assert response.get_json()["errorCode"] == "NOT_FOUND"


def test_runtime_status_is_available_and_secret_free(cloud):
    response = cloud.test_client().get("/api/v2/agent/runtime")
    assert response.status_code == 200
    body = response.get_json()
    assert body["mode"] == "cloud"
    assert body["ready"] is True
    assert body["agentId"] == "agent-abc"
    assert body["hasIamUser"] is True
    text = json.dumps(body, ensure_ascii=False)
    assert "secret-pass" not in text
    assert "main-account" not in text


def test_empty_message_is_rejected_before_touching_the_platform(cloud):
    fake = cloud.config["TEST_FAKE_CLIENT"]
    response = cloud.test_client().post("/api/v2/agent/chat", json={"message": "   "})
    assert response.status_code == 400
    assert response.get_json()["errorCode"] == "BAD_REQUEST"
    assert fake.calls == []


def test_feedback_is_stored_and_requires_an_owned_run(cloud):
    client = cloud.test_client()
    response = client.post("/api/v2/agent/chat", json={"message": "hi", "sessionId": "chat-f"})
    run_id = parse_sse(response.get_data(as_text=True))[0]["runId"]

    missing = client.post("/api/v2/agent/feedback", json={"runId": "run-nope", "rating": "helpful"})
    assert missing.status_code == 404

    bad = client.post("/api/v2/agent/feedback", json={"runId": run_id, "rating": "great"})
    assert bad.status_code == 400

    ok = client.post("/api/v2/agent/feedback",
                     json={"runId": run_id, "rating": "helpful", "note": "计划切合实际"})
    assert ok.status_code == 201
    assert ok.get_json()["status"] == "ok"


def test_real_client_is_wired_with_token_provider():
    """防止"测试全绿但生产构造的是假客户端"：这里验证默认构造链路。"""
    config = AgentArtsConfig.from_env({**CLOUD_ENV})
    provider = IamTokenProvider(config)
    client = AgentArtsClient(config, provider)
    assert client._config.agent_id == "agent-abc"
    assert client._tokens is provider
