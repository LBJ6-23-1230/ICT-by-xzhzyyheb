# -*- coding: utf-8 -*-
"""MCP 工具层测试（pytest 版）。

与 `tools/verify_agentarts_s3.py` 的分工：
* 那个脚本是**端到端**：真起 HTTP、真发 JSON-RPC，用来证明"平台能发现并调用"。
* 这里是**单元级**：直接对 registry 发工具调用，用来快速定位是哪个工具坏了。

两者数据都落在临时目录，不碰开发库。
"""
import pytest

from zhixue_mcp import envelope
from zhixue_mcp.registry import ToolContext
from zhixue_mcp.tools import build_registry


class MemoryRepository:
    def __init__(self):
        self.data = {}

    def get(self, collection, item_id):
        return self.data.get((collection, item_id))

    def save(self, collection, item_id, value):
        self.data[(collection, item_id)] = dict(value)

    def list(self, collection):
        return [dict(value) for (name, _), value in self.data.items() if name == collection]


SESSION_REF = "sref-unit-test"


@pytest.fixture
def repo():
    repository = MemoryRepository()
    repository.save("agentarts_sessions", "conv-unit", {
        "conversationId": "conv-unit", "userId": "u-unit", "chatSessionId": "chat-unit",
        "agentId": "agent-unit", "sessionRef": SESSION_REF,
    })
    repository.save("profiles", "u-unit", {
        "userId": "u-unit", "goal": "期末复习", "examDate": "2027-01-10",
        "profileVersion": 1,
        "mastery": [{"knowledgePointId": "tree", "knowledgePointName": "树",
                     "masteryScore": 42, "source": "seed"}],
    })
    repository.save("exercises", "ex-1", {
        "exerciseId": "ex-1", "answerKey": "B", "knowledgePointId": "tree",
        "knowledgePointName": "树", "stem": "遍历顺序？", "options": ["A", "B"],
    })
    repository.save("plans", "plan-1", {
        "planId": "plan-1", "userId": "u-unit", "version": 1,
        "tasks": [{"taskId": "t1", "knowledgePointId": "tree",
                   "durationMinutes": 30, "status": "pending"}],
    })
    return repository


def call(repository, name, **arguments):
    registry = build_registry()
    context = ToolContext(repository=repository, user_id="",
                          session_ref=arguments.get("sessionRef", ""))
    return registry.call(name, arguments, context)


def test_registry_exposes_eleven_tools():
    registry = build_registry()
    assert len(registry.describe()) == 11
    assert set(registry.writes()) == {"save_learning_plan", "update_mastery", "request_replan"}


def test_every_tool_requires_session_ref():
    registry = build_registry()
    for tool in registry.describe():
        assert "sessionRef" in tool["inputSchema"]["required"], tool["name"]


def test_forged_session_ref_is_rejected(repo):
    result = call(repo, "get_learning_context", sessionRef="sref-forged")
    assert result["ok"] is False
    assert result["errorCode"] == envelope.UNAUTHORIZED


def test_user_id_argument_is_refused(repo):
    result = call(repo, "get_learning_context", sessionRef=SESSION_REF, userId="victim")
    assert result["ok"] is False
    assert result["errorCode"] == envelope.VALIDATION_ERROR


def test_learning_context_reads_profile_and_plan(repo):
    result = call(repo, "get_learning_context", sessionRef=SESSION_REF)
    assert result["ok"] is True
    assert result["data"]["goal"] == "期末复习"
    assert result["data"]["plan"]["version"] == 1


def test_grading_uses_server_side_answer_keys(repo):
    result = call(repo, "grade_exercise", sessionRef=SESSION_REF,
                  answers=[{"exerciseId": "ex-1", "answer": "B"}],
                  answerKeys={"ex-1": "A"})
    assert result["ok"] is True
    assert result["data"]["score"] == 100


def test_plan_save_is_versioned_and_idempotent(repo):
    plan = {"planId": "plan-1", "tasks": [{"taskId": "t1", "status": "pending",
                                          "durationMinutes": 45}]}
    first = call(repo, "save_learning_plan", sessionRef=SESSION_REF, plan=plan,
                 expectedVersion=1, idempotencyKey="k1")
    assert first["ok"] is True and first["data"]["version"] == 2

    again = call(repo, "save_learning_plan", sessionRef=SESSION_REF, plan=plan,
                 expectedVersion=1, idempotencyKey="k1")
    assert again["data"]["version"] == 2
    assert again["details"]["replayed"] is True

    stale = call(repo, "save_learning_plan", sessionRef=SESSION_REF, plan=plan,
                 expectedVersion=1, idempotencyKey="k2")
    assert stale["ok"] is False and stale["errorCode"] == envelope.CONFLICT


def test_mastery_update_applies_documented_rule(repo):
    result = call(repo, "update_mastery", sessionRef=SESSION_REF,
                  updates=[{"knowledgePointId": "tree", "assessmentScore": 66.67}])
    assert result["ok"] is True
    assert result["data"]["mastery"][0]["newScore"] == 58


def test_budget_tool_defers_overflow(repo):
    plan = {"planId": "p", "version": 1, "tasks": [
        {"taskId": "t1", "knowledgePointId": "a", "durationMinutes": 30, "status": "pending"},
        {"taskId": "t2", "knowledgePointId": "b", "durationMinutes": 30, "status": "pending"},
    ]}
    result = call(repo, "fit_plan_to_budget", sessionRef=SESSION_REF,
                  plan=plan, availableMinutes=30)
    assert result["ok"] is True
    assert len(result["data"]["plan"]["tasks"]) == 1
    assert len(result["data"]["plan"]["unscheduledTasks"]) == 1


def test_unknown_tool_returns_not_found(repo):
    result = call(repo, "no_such_tool", sessionRef=SESSION_REF)
    assert result["ok"] is False
    assert result["errorCode"] == envelope.NOT_FOUND


def test_missing_required_argument_is_validation_error(repo):
    result = call(repo, "fit_plan_to_budget", sessionRef=SESSION_REF, plan={"tasks": []})
    assert result["ok"] is False
    assert result["errorCode"] == envelope.VALIDATION_ERROR
