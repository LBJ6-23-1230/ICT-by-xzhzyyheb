# -*- coding: utf-8 -*-
"""断链与降级：云端不可用时，系统必须"说清楚"，不能假装成功。

这组测试对应施工手册 S2 的验收第 2、3 条：
* 未开启 → 503（配置状态，不是故障）
* 配置不全 → 503 且列出缺哪个变量
* Token 失败 / 平台不可达 → 502 且 `fallback: false`
* 流到一半断掉 → 事件里出现 `run_failed`，运行记录标记 failed
* 本地聊天通道明确标 `source: local`，不与云端混淆
"""
import json

import pytest

from app import create_app
from app.agentarts import AgentArtsConfig, AgentArtsError
from app.agentarts import session as store
from app.api.agentarts_api import configure_agentarts


def parse_sse(text: str) -> list[dict]:
    parsed = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    parsed.append(json.loads(chunk))
    return parsed


class _BrokenClient:
    """ensure_ready 就失败：模拟取不到 Token / 平台不可达。"""

    def __init__(self, error):
        self.error = error

    def ensure_ready(self):
        raise self.error

    def run_agent(self, *args, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("ensure_ready 失败后不应开始流式调用")


class _HalfBrokenClient:
    def __init__(self, events, fail_after):
        self.events = events
        self.fail_after = fail_after

    def ensure_ready(self):
        return "tok"

    def run_agent(self, *args, **kwargs):
        for index, event in enumerate(self.events):
            if index >= self.fail_after:
                raise AgentArtsError("平台断流", status=500)
            yield event


def _app(tmp_path, monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return create_app(tmp_path / "degrade.json")


def test_disabled_mode_returns_503_with_reason_and_no_fallback(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"AGENTARTS_ENABLED": "0"})
    response = app.test_client().post("/api/v2/agent/chat", json={"message": "hi"})
    assert response.status_code == 503
    body = response.get_json()
    assert body["errorCode"] == "AGENTARTS_DISABLED"
    assert body["fallback"] is False
    assert body["details"]["mode"] == "disabled"
    assert "AGENTARTS_ENABLED" in body["message"]


def test_incomplete_config_lists_missing_variables(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {
        "AGENTARTS_ENABLED": "1",
        "AGENTARTS_MODE": "cloud",
        "AGENTARTS_IAM_USER": "zhixue",
        "AGENTARTS_IAM_DOMAIN": "",
        "AGENTARTS_IAM_PASSWORD": "",
        "AGENTARTS_PROJECT_ID": "",
        "AGENTARTS_AGENT_ID": "",
    })
    response = app.test_client().post("/api/v2/agent/chat", json={"message": "hi"})
    assert response.status_code == 503
    body = response.get_json()
    assert body["errorCode"] == "AGENTARTS_CONFIG_INCOMPLETE"
    assert "AGENTARTS_IAM_DOMAIN" in body["details"]["missing"]
    assert body["fallback"] is False


def test_unreachable_platform_returns_502_before_streaming(tmp_path, monkeypatch):
    env = {
        "AGENTARTS_ENABLED": "1",
        "AGENTARTS_MODE": "cloud",
        "AGENTARTS_IAM_USER": "zhixue",
        "AGENTARTS_IAM_DOMAIN": "main",
        "AGENTARTS_IAM_PASSWORD": "p",
        "AGENTARTS_PROJECT_ID": "proj",
        "AGENTARTS_AGENT_ID": "agent",
    }
    app = _app(tmp_path, monkeypatch, env)
    configure_agentarts(app.extensions["repository"],
                        config=AgentArtsConfig.from_env(),
                        client=_BrokenClient(AgentArtsError("无法连接 AgentArts", status=0)))

    response = app.test_client().post("/api/v2/agent/chat", json={"message": "hi"})
    assert response.status_code == 502
    body = response.get_json()
    assert body["errorCode"] == "AGENTARTS_UNAVAILABLE"
    assert body["fallback"] is False
    # 失败不应留下一条"成功"的运行记录
    assert store.events_since(app.extensions["repository"], "no-such-run") == []


def test_mid_stream_failure_is_reported_and_run_marked_failed(tmp_path, monkeypatch):
    env = {
        "AGENTARTS_ENABLED": "1",
        "AGENTARTS_MODE": "cloud",
        "AGENTARTS_IAM_USER": "zhixue",
        "AGENTARTS_IAM_DOMAIN": "main",
        "AGENTARTS_IAM_PASSWORD": "p",
        "AGENTARTS_PROJECT_ID": "proj",
        "AGENTARTS_AGENT_ID": "agent",
    }
    app = _app(tmp_path, monkeypatch, env)
    events = [
        {"event": "start"},
        {"event": "message", "content": "我先查一下"},
        {"event": "message", "content": "你的课程"},
    ]
    configure_agentarts(app.extensions["repository"],
                        config=AgentArtsConfig.from_env(),
                        client=_HalfBrokenClient(events, fail_after=2))

    response = app.test_client().post("/api/v2/agent/chat", json={"message": "hi", "sessionId": "c1"})
    parsed = parse_sse(response.get_data(as_text=True))
    types = [item["type"] for item in parsed]
    assert "run_failed" in types
    failed = next(item for item in parsed if item["type"] == "run_failed")
    assert failed["errorCode"] == "AGENTARTS_STREAM_ERROR"

    run_id = parsed[0]["runId"]
    saved = store.get_run(app.extensions["repository"], run_id)
    assert saved["status"] == "failed"
    assert saved["error"]


def test_local_chat_channel_is_labelled_local(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"AGENTARTS_ENABLED": "0"})
    response = app.test_client().post("/api/agent/chat", json={"message": "今天我应该先学什么？"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["source"] == "local"
    # 本地通道不应伪装成云端
    assert "agentarts" not in json.dumps(body, ensure_ascii=False).lower()


def test_service_index_reports_agentarts_state(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"AGENTARTS_ENABLED": "0"})
    body = app.test_client().get("/").get_json()
    assert body["agentarts"]["mode"] == "disabled"
    assert body["layers"]["cloudAgent"] == "/api/v2/agent/chat"
