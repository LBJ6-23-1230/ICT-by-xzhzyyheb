# -*- coding: utf-8 -*-
"""S3 MCP 服务端到端自检：**真的起一个 HTTP 服务并调用工具**。

与"读代码猜行为"不同，这里验证的是实际协议往返：
JSON-RPC → 工具 → 仓储读写 → 信封 → HTTP 响应。

覆盖的验收点（对应施工手册 S3）：
1. `initialize` / `tools/list` 能被平台发现（11 个工具带 inputSchema）
2. 无 API Key → 401；伪造 sessionRef → UNAUTHORIZED
3. 工具入参带 userId → 拒绝（越权防线）
4. 判分只认服务端答案键（请求里塞 answerKeys 无效）
5. 计划保存的版本校验 + 幂等重放
6. 重排只在确有变化时才升版本
7. 掌握度按固定规则更新（66.67 分 +16）

用法::

    python tools/verify_agentarts_s3.py

退出码 0 = 全通过。
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server" / "zhixue-agent-server"
sys.path.insert(0, str(SERVER))

WORK = ROOT / "ICT" / "evidence" / ".mcp-selftest"
failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print("  [OK] %s" % label)
    else:
        print("  [X]  %s%s" % (label, ("  -> " + str(detail)) if detail else ""))
        failures.append(label)


# ------------------------------------------------------------------ 造数据
def seed(repository):
    now = "2026-11-01T00:00:00+00:00"
    repository.save("agentarts_sessions", "conv-test", {
        "conversationId": "conv-test",
        "userId": "u-demo",
        "chatSessionId": "chat-test",
        "agentId": "agent-test",
        "sessionRef": "sref-valid-0001",
        "createdAt": now,
        "lastActiveAt": now,
    })
    repository.save("profiles", "u-demo", {
        "userId": "u-demo",
        "goal": "过数据结构期末",
        "examDate": "2027-01-10",
        "freeTimeSlots": [{"day": "mon", "minutes": 60}],
        "profileVersion": 3,
        "mastery": [
            {"knowledgePointId": "binary-tree-traversal", "knowledgePointName": "二叉树遍历",
             "masteryScore": 42, "source": "seed"},
            {"knowledgePointId": "graph-algorithm", "knowledgePointName": "图算法",
             "masteryScore": 55, "source": "seed"},
        ],
    })
    repository.save("plans", "plan-1", {
        "planId": "plan-1",
        "userId": "u-demo",
        "version": 1,
        "generatedAt": now,
        "reason": "seed",
        "tasks": [
            {"taskId": "task-postorder", "knowledgePointId": "binary-tree-traversal",
             "title": "二叉树后序遍历", "durationMinutes": 30, "status": "pending"},
            {"taskId": "task-graph", "knowledgePointId": "graph-algorithm",
             "title": "图算法", "durationMinutes": 30, "status": "pending"},
        ],
        "constraints": {"availableMinutes": 60},
        "budgetSummary": {"availableMinutes": 60, "scheduledMinutes": 60,
                          "unallocatedMinutes": 0, "unscheduledMinutes": 0},
    })
    repository.save("exercises", "ex-1", {
        "exerciseId": "ex-1",
        "answerKey": "B",
        "knowledgePointId": "binary-tree-traversal",
        "knowledgePointName": "二叉树遍历",
        "stem": "后序遍历的顺序是？",
        "options": ["A. 根左右", "B. 左右根", "C. 左根右", "D. 右根左"],
        "difficulty": "easy",
    })
    repository.save("knowledge_bases", "kb-1", {
        "kbId": "kb-1", "userId": "u-demo", "name": "课程资料库", "courseName": "数据结构",
    })
    repository.save("documents", "doc-1", {
        "documentId": "doc-1", "kbId": "kb-1", "userId": "u-demo",
        "fileName": "二叉树讲义.pdf", "status": "ready",
        # 解析时抽出的知识点：出题"考什么"由它决定，而不是由谁最弱决定
        "knowledgePoints": ["二叉树遍历基础", "后序遍历"],
        "chunkCount": 1, "parsedAt": now,
    })
    repository.save("chunks", "chunk-1", {
        "chunkId": "chunk-1",
        "documentId": "doc-1",
        "kbId": "kb-1",
        "userId": "u-demo",
        "fileName": "二叉树讲义.pdf",
        "headingPath": "第3章 > 3.2 二叉树遍历基础（binary-tree-traversal）",
        "text": "二叉树遍历基础：后序遍历的顺序是左右根，递归实现时先访问左子树，"
                "再访问右子树，最后访问根节点。",
        "contentHash": "hash-1",
    })
    repository.save("wrong_book", "u-demo:ex-1", {
        "userId": "u-demo",
        "exerciseId": "ex-1",
        "knowledgePointId": "binary-tree-traversal",
        "wrongCount": 2,
        "lastPracticedAt": now,
    })
    repository.save("evidences", "ev-1", {"evidenceId": "ev-1", "userId": "u-demo",
                                          "type": "assessment"})


# ------------------------------------------------------------------ HTTP
class Client:
    def __init__(self, base: str, api_key: str):
        self.base = base
        self.api_key = api_key
        self.seq = 0

    def _post(self, payload, *, with_key=True):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if with_key:
            headers["X-API-Key"] = self.api_key
        request = urllib.request.Request(self.base + "/mcp", data=data, headers=headers,
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
                # 通知类请求返回 202 且无 body，这是协议允许的，不能当解析失败。
                return response.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8")
            return error.code, (json.loads(raw) if raw.strip() else {})

    def rpc(self, method, params=None, *, with_key=True):
        self.seq += 1
        payload = {"jsonrpc": "2.0", "id": self.seq, "method": method}
        if params is not None:
            payload["params"] = params
        return self._post(payload, with_key=with_key)

    def call(self, name, arguments):
        status, body = self.rpc("tools/call", {"name": name, "arguments": arguments})
        result = body.get("result") or {}
        text = (result.get("content") or [{}])[0].get("text") or "{}"
        return status, json.loads(text), result.get("isError")

    def health(self, *, with_key=True):
        headers = {"X-API-Key": self.api_key} if with_key else {}
        request = urllib.request.Request(self.base + "/healthz", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, {}


def scenario_discovery(client):
    print("\n[1/6] 平台发现：initialize + tools/list")
    status, body = client.rpc("initialize", {"protocolVersion": "2025-03-26",
                                             "capabilities": {},
                                             "clientInfo": {"name": "selfcheck"}})
    result = body.get("result") or {}
    check("initialize 返回 200", status == 200, status)
    check("声明 tools 能力", bool((result.get("capabilities") or {}).get("tools")) is True)
    check("返回 serverInfo", (result.get("serverInfo") or {}).get("name") == "zhixue-learning-tools")

    _, body = client.rpc("notifications/initialized", {})
    check("初始化通知不报错", "error" not in body)

    _, body = client.rpc("tools/list", {})
    tools = (body.get("result") or {}).get("tools") or []
    names = [tool.get("name") for tool in tools]
    check("工具数量为 12", len(tools) == 12, len(tools))
    check("每个工具都带 inputSchema",
          all(isinstance(tool.get("inputSchema"), dict) for tool in tools))
    for required in ("get_learning_context", "grade_exercise", "save_learning_plan",
                     "create_grounded_exercise", "record_wrong_question"):
        check("存在工具 %s" % required, required in names)


def scenario_security(client):
    print("\n[2/6] 鉴权与越权防线")
    status, _ = client.rpc("initialize", {"protocolVersion": "2025-03-26"}, with_key=False)
    check("无 API Key 被拒（401）", status == 401, status)

    _, body, is_error = client.call("get_learning_context", {"sessionRef": "sref-forged"})
    check("伪造 sessionRef 返回 isError", is_error is True)
    check("伪造 sessionRef 错误码 UNAUTHORIZED",
          body.get("errorCode") == "UNAUTHORIZED", body.get("errorCode"))

    _, body, _ = client.call("get_learning_context",
                             {"sessionRef": "sref-valid-0001", "userId": "someone-else"})
    check("入参带 userId 被拒绝", body.get("errorCode") == "VALIDATION_ERROR", body.get("errorCode"))


def scenario_read(client):
    print("\n[3/6] 只读工具")
    _, body, _ = client.call("get_learning_context", {"sessionRef": "sref-valid-0001"})
    check("读取上下文 ok", body.get("ok") is True)
    data = body.get("data") or {}
    check("返回目标与考试日期", data.get("goal") == "过数据结构期末" and data.get("examDate"))
    check("返回最薄弱知识点（2 个）", len(data.get("weakPoints") or []) == 2)
    check("返回当前计划版本", (data.get("plan") or {}).get("version") == 1)

    _, body, _ = client.call("search_learning_materials",
                             {"sessionRef": "sref-valid-0001", "query": "后序遍历", "topK": 3})
    hits = (body.get("data") or {}).get("hits") or []
    check("资料检索命中", len(hits) >= 1, len(hits))
    if hits:
        check("命中带文件名", bool(hits[0].get("fileName")), hits[0].get("fileName"))
        check("命中带章节路径", bool(hits[0].get("headingPath")), hits[0].get("headingPath"))
        check("命中带片段哈希", bool(hits[0].get("contentHash")))

    _, body, _ = client.call("get_learning_evidence", {"sessionRef": "sref-valid-0001"})
    data = body.get("data") or {}
    check("证据存在标记为 true", data.get("hasEvidence") is True)
    check("错题数量正确", data.get("wrongBookCount") == 1, data.get("wrongBookCount"))

    # 不指定知识点出题：考什么应由**资料里抽出的知识点**决定（而不是"谁最弱"）。
    # 离线环境没有大模型 Key，生成一定为空——所以断言"选点逻辑对了"，
    # 而不是"题目出来了"。
    _, body, _ = client.call("create_grounded_exercise",
                             {"sessionRef": "sref-valid-0001", "count": 1})
    details = body.get("details") or {}
    check("未指定知识点时从资料里选点",
          details.get("pointSource") == "auto_from_material", details.get("pointSource"))
    check("选中资料里的知识点",
          bool(details.get("knowledgePointId")), details.get("knowledgePointId"))
    check("出题受限于生成能力而非资料缺失",
          "未配置出题模型" in str(body.get("message") or "")
          or "资料不足以生成" in str(body.get("message") or ""), body.get("message"))

    # 题量以"用户原话里的数字"为准：节点里的 count 是固定值，用户说两道就该两道
    _, body, _ = client.call("create_grounded_exercise", {
        "sessionRef": "sref-valid-0001", "count": 3,
        "topic": "出两道二叉树遍历的题"})
    check("题量取用户原话（两道）",
          (body.get("details") or {}).get("requestedCount") == 2,
          (body.get("details") or {}).get("requestedCount"))

    # 原话点到资料里没有的主题时，应如实拒绝并给出现有候选（不悄悄换知识点）
    _, body, _ = client.call("create_grounded_exercise", {
        "sessionRef": "sref-valid-0001", "topic": "出两道TCP的题"})
    details = body.get("details") or {}
    check("资料里没有的主题会如实拒绝",
          details.get("reason") == "TOPIC_NOT_IN_MATERIALS", details.get("reason"))
    check("拒绝时带出现有候选", bool(details.get("candidates")), details.get("candidates"))

    # 用户原话里点到资料中的知识点时，应优先用它
    _, body, _ = client.call("create_grounded_exercise", {
        "sessionRef": "sref-valid-0001", "count": 1,
        "topic": "根据这份资料出两道二叉树遍历基础的题"})
    check("原话提到知识点时优先命中",
          (body.get("details") or {}).get("pointSource") == "auto_from_topic",
          (body.get("details") or {}).get("pointSource"))


def scenario_compute(client):
    print("\n[4/6] 计算类工具")
    points = [
        {"knowledgePointId": "binary-tree-traversal", "masteryScore": 42,
         "errorIntensity": 60, "importance": 80},
        {"knowledgePointId": "graph-algorithm", "masteryScore": 80,
         "errorIntensity": 10, "importance": 50},
    ]
    _, body, _ = client.call("calculate_learning_priority",
                             {"sessionRef": "sref-valid-0001", "knowledgePoints": points})
    ranked = (body.get("data") or {}).get("ranked") or []
    check("优先级返回 2 项", len(ranked) == 2, len(ranked))
    check("按总分降序", ranked[0]["totalScore"] >= ranked[1]["totalScore"])
    check("带五因子明细", len(ranked[0].get("factorDetails") or []) == 5)

    plan = {"planId": "p-budget", "version": 1, "tasks": [
        {"taskId": "t1", "knowledgePointId": "a", "durationMinutes": 30, "status": "pending"},
        {"taskId": "t2", "knowledgePointId": "b", "durationMinutes": 30, "status": "pending"},
    ]}
    _, body, _ = client.call("fit_plan_to_budget",
                             {"sessionRef": "sref-valid-0001", "plan": plan,
                              "availableMinutes": 30, "prioritizePoint": "a"})
    data = body.get("data") or {}
    scheduled = (data.get("plan") or {}).get("tasks") or []
    deferred = (data.get("plan") or {}).get("unscheduledTasks") or []
    check("30 分钟只排得下 1 个任务", len(scheduled) == 1, len(scheduled))
    check("排不下的进入延期列表", len(deferred) == 1, len(deferred))
    check("延期原因写清", "分钟" in str(deferred[0].get("deferredReason", "")) if deferred else False)

    # 请求里故意塞一份错误答案键：判分必须只认服务端那份。
    _, body, _ = client.call("grade_exercise", {
        "sessionRef": "sref-valid-0001",
        "answers": [{"exerciseId": "ex-1", "answer": "B"}],
        "answerKeys": {"ex-1": "A"},
    })
    data = body.get("data") or {}
    check("判分只用服务端答案键（满分 100）", data.get("score") == 100, data.get("score"))

    # 逐题对错：页面靠它显示"哪题错了、正确答案是什么"
    per_question = data.get("perQuestion") or []
    check("判分返回逐题结果", len(per_question) == 1, len(per_question))
    if per_question:
        check("逐题结果带正确答案", per_question[0].get("correctAnswer") == "B",
              per_question[0].get("correctAnswer"))
        check("逐题结果标记正确", per_question[0].get("correct") is True)

    _, body, _ = client.call("grade_exercise", {
        "sessionRef": "sref-valid-0001",
        "answers": [{"exerciseId": "no-such", "answer": "B"}],
    })
    check("题目不存在时拒绝判分", body.get("errorCode") == "NOT_FOUND", body.get("errorCode"))

    new_plan = {"planId": "p-diff", "version": 2, "tasks": [
        {"taskId": "t1", "knowledgePointId": "a", "durationMinutes": 45, "status": "pending"}]}
    _, body, _ = client.call("get_plan_diff", {"sessionRef": "sref-valid-0001",
                                               "oldPlan": plan, "newPlan": new_plan})
    data = body.get("data") or {}
    check("差异识别出改动任务", len(data.get("changedTasks") or []) >= 1)
    check("差异带版本号", data.get("oldVersion") == 1 and data.get("newVersion") == 2)


def scenario_write(client):
    print("\n[5/6] 写操作：版本、幂等、重排、掌握度")
    plan = {"planId": "plan-1", "tasks": [
        {"taskId": "task-postorder", "knowledgePointId": "binary-tree-traversal",
         "durationMinutes": 30, "status": "pending"},
        {"taskId": "task-graph", "knowledgePointId": "graph-algorithm",
         "durationMinutes": 30, "status": "pending"},
    ]}
    _, body, _ = client.call("save_learning_plan", {
        "sessionRef": "sref-valid-0001", "plan": plan,
        "expectedVersion": 1, "idempotencyKey": "save-key-1", "reason": "按 45 分钟重排",
    })
    data = body.get("data") or {}
    check("首次保存成功且版本 +1", data.get("version") == 2, data.get("version"))

    _, body, _ = client.call("save_learning_plan", {
        "sessionRef": "sref-valid-0001", "plan": plan,
        "expectedVersion": 1, "idempotencyKey": "save-key-1",
    })
    data = body.get("data") or {}
    check("同一幂等键重放不升版本", data.get("version") == 2, data.get("version"))
    check("重放被显式标记", (body.get("details") or {}).get("replayed") is True)

    _, body, _ = client.call("save_learning_plan", {
        "sessionRef": "sref-valid-0001", "plan": plan,
        "expectedVersion": 1, "idempotencyKey": "save-key-2",
    })
    check("版本过期返回 CONFLICT", body.get("errorCode") == "CONFLICT",
          {"errorCode": body.get("errorCode"), "message": body.get("message"),
           "details": body.get("details")})

    _, body, _ = client.call("request_replan", {
        "sessionRef": "sref-valid-0001",
        "assessment": {"masteryScore": 40, "incompleteTasks": 1, "repeatedError": True,
                       "assessmentScore": 33.33, "knowledgePointId": "binary-tree-traversal"},
    })
    data = body.get("data") or {}
    check("需要重排", data.get("needReplan") is True)
    check("重排确实改了任务", data.get("changed") is True)
    check("重排后版本递增", (data.get("plan") or {}).get("version") == 3,
          (data.get("plan") or {}).get("version"))

    _, body, _ = client.call("update_mastery", {
        "sessionRef": "sref-valid-0001",
        "updates": [{"knowledgePointId": "binary-tree-traversal", "assessmentScore": 66.67}],
    })
    rows = (body.get("data") or {}).get("mastery") or []
    check("掌握度按规则 +16（42 → 58）", rows and rows[0]["newScore"] == 58,
          rows[0] if rows else None)
    check("画像版本递增", (body.get("data") or {}).get("profileVersion") == 4,
          (body.get("data") or {}).get("profileVersion"))

    # 错题录入：既要落错题本，也要真的把掌握度压下去（否则学情永远停在种子值）
    _, body, is_error = client.call("record_wrong_question", {
        "sessionRef": "sref-valid-0001",
        "knowledgePointId": "binary-tree-traversal",
        "stem": "后序遍历的访问顺序是？（A. 根左右 B. 左右根）",
        "reason": "把后序与中序混淆",
    })
    data = body.get("data") or {}
    check("错题录入成功", is_error is False and data.get("wrongCount") == 1,
          body.get("errorCode"))
    check("错题让掌握度下降（58 → 54）", data.get("newScore") == 54, data.get("newScore"))
    check("错题按内容生成稳定题号", str(data.get("exerciseId") or "").startswith("wrong-"),
          data.get("exerciseId"))
    _, body, _ = client.call("get_learning_evidence",
                             {"sessionRef": "sref-valid-0001",
                              "knowledgePointId": "binary-tree-traversal"})
    check("错题出现在证据里",
          ((body.get("data") or {}).get("wrongBookCount") or 0) >= 1,
          (body.get("data") or {}).get("wrongBookCount"))


def scenario_protocol(client):
    print("\n[6/6] 协议健壮性与健康检查")
    status, body = client.health()
    check("健康检查 200", status == 200, status)
    check("健康检查报告工具数 = 12", body.get("tools") == 12, body.get("tools"))
    check("健康检查列出写操作", set(body.get("writes") or []) ==
          {"save_learning_plan", "update_mastery", "request_replan",
           "record_wrong_question"}, body.get("writes"))

    status, body = client.health(with_key=False)
    check("健康检查也要求鉴权", status == 401, status)

    _, body = client.rpc("resources/list", {})
    check("未实现的方法返回 -32601",
          (body.get("error") or {}).get("code") == -32601, body.get("error"))


def main() -> int:
    if WORK.exists():
        for item in WORK.rglob("*"):
            if item.is_file():
                item.unlink()
    WORK.mkdir(parents=True, exist_ok=True)
    os.environ["ZHIXUE_DB"] = "json"
    os.environ["ZHIXUE_REPOSITORY"] = str(WORK / "repository.json")

    from zhixue_mcp import server as mcp_server

    repository = mcp_server.open_repository()
    # 防回归：确认仓储落在临时目录，绝不允许自检写进开发库。
    actual = Path(getattr(repository, "path", "")).resolve()
    if WORK.resolve() not in actual.parents:
        print("自检中止：仓储路径不在临时目录（%s）。" % actual, file=sys.stderr)
        print("这会把测试数据写进开发库，先修 open_repository 的 ZHIXUE_REPOSITORY 读取。",
              file=sys.stderr)
        return 2
    seed(repository)
    httpd = mcp_server.create_server("127.0.0.1", 0, repository=repository, api_key="test-key")
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    client = Client("http://127.0.0.1:%d" % port, "test-key")
    try:
        print("S3 MCP 端到端自检  ·  http://127.0.0.1:%d/mcp" % port)
        scenario_discovery(client)
        scenario_security(client)
        scenario_read(client)
        scenario_compute(client)
        scenario_write(client)
        scenario_protocol(client)
    finally:
        httpd.shutdown()
        httpd.server_close()

    print("\n" + "=" * 64)
    if failures:
        print("  失败 %d / %d 项" % (len(failures), checks))
        for item in failures:
            print("    - %s" % item)
        return 1
    print("  全部通过：%d 项检查" % checks)
    print("  说明：以上均为真实 HTTP 往返；平台侧接入仍需公网 HTTPS 地址（见 README）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
