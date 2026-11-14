# -*- coding: utf-8 -*-
"""给 MCP 服务种一份最小演示数据，并打印可直接粘贴的 sessionRef。

为什么需要它
------------
平台侧联调时，`tools/call` 必须带一个有效的 `sessionRef`（网关签发的一次性引用）。
正式流程里这个值由网关调用智能体时注入；但在"只验证 MCP 通不通"的阶段还没有网关，
所以这里直接造一份最小数据，把 sessionRef 打出来，方便手工或平台调试用。

⚠️ 只写入演示身份（默认 u-demo），不碰真实账号；已有内容时需显式 `--force`。

用法::

    python tools/seed_mcp_demo.py --repository data/repository.json
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server" / "zhixue-agent-server"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_records(user_id: str, session_ref: str, conversation_id: str) -> dict:
    stamp = now()
    return {
        "agentarts_sessions": {
            conversation_id: {
                "conversationId": conversation_id,
                "userId": user_id,
                "chatSessionId": "chat-demo",
                "agentId": "agent-demo",
                "sessionRef": session_ref,
                "createdAt": stamp,
                "lastActiveAt": stamp,
            }
        },
        "profiles": {
            user_id: {
                "userId": user_id,
                "goal": "两周内过数据结构期末",
                "examDate": "2027-01-10",
                "freeTimeSlots": [{"day": "mon", "minutes": 60}],
                "profileVersion": 1,
                "mastery": [
                    {"knowledgePointId": "binary-tree-traversal",
                     "knowledgePointName": "二叉树遍历", "masteryScore": 42, "source": "seed"},
                    {"knowledgePointId": "graph-algorithm",
                     "knowledgePointName": "图算法", "masteryScore": 55, "source": "seed"},
                ],
                "updatedAt": stamp,
            }
        },
        "plans": {
            "plan-demo": {
                "planId": "plan-demo",
                "userId": user_id,
                "version": 1,
                "generatedAt": stamp,
                "reason": "seed",
                "tasks": [
                    {"taskId": "task-postorder", "knowledgePointId": "binary-tree-traversal",
                     "title": "二叉树后序遍历", "durationMinutes": 30, "status": "pending"},
                    {"taskId": "task-graph", "knowledgePointId": "graph-algorithm",
                     "title": "图算法", "durationMinutes": 30, "status": "pending"},
                ],
                "constraints": {"availableMinutes": 60},
            }
        },
        "exercises": {
            "ex-demo-1": {
                "exerciseId": "ex-demo-1",
                "answerKey": "B",
                "knowledgePointId": "binary-tree-traversal",
                "knowledgePointName": "二叉树遍历",
                "stem": "后序遍历的顺序是？",
                "options": ["A. 根左右", "B. 左右根", "C. 左根右", "D. 右根左"],
                "difficulty": "easy",
            }
        },
        "chunks": {
            "chunk-demo-1": {
                "chunkId": "chunk-demo-1",
                "documentId": "doc-demo-1",
                "kbId": "kb-demo",
                "userId": user_id,
                "fileName": "二叉树讲义.pdf",
                "headingPath": "第3章 > 3.2 后序遍历",
                "text": "后序遍历的顺序是左右根：先访问左子树，再访问右子树，最后访问根节点。",
                "contentHash": "hash-demo-1",
            }
        },
        "wrong_book": {
            "%s:ex-demo-1" % user_id: {
                "userId": user_id,
                "exerciseId": "ex-demo-1",
                "knowledgePointId": "binary-tree-traversal",
                "wrongCount": 1,
                "lastPracticedAt": stamp,
            }
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="为 MCP 自检写入最小演示数据")
    parser.add_argument("--repository", default=str(SERVER / "data" / "repository.json"),
                        help="JSON 仓储路径（默认后端 data/repository.json）")
    parser.add_argument("--user-id", default="u-demo")
    parser.add_argument("--session-ref", default="sref-demo-0000000000000001",
                        help="自定义会话引用；平台调试时把它作为工具参数")
    parser.add_argument("--force", action="store_true", help="目标已有数据时覆盖写入")
    args = parser.parse_args()

    target = Path(args.repository).resolve()
    existing = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except ValueError:
            existing = {}
    records = existing if isinstance(existing, dict) else {}
    has_real = bool(records.get("users") or records.get("sessions"))
    if (existing and not args.force) or has_real:
        print("拒绝写入：%s 已有数据%s。" % (target, "（含真实账号）" if has_real else ""),
              file=sys.stderr)
        print("确认要合并演示数据请加 --force；想隔离请换 --repository 指向临时文件。",
              file=sys.stderr)
        return 2

    conversation_id = "conv-demo-0000000000000001"
    for collection, items in build_records(args.user_id, args.session_ref,
                                           conversation_id).items():
        bucket = records.setdefault(collection, {})
        bucket.update(items)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print("已写入 %s" % target)
    print()
    print("平台/curl 调试时使用：")
    print("  sessionRef      = %s" % args.session_ref)
    print("  conversationId  = %s" % conversation_id)
    print("  userId          = %s" % args.user_id)
    print()
    print("可直接调用的示例（把 URL 换成你的地址）：")
    print("  curl -s -X POST https://<IP>:8443/mcp \\")
    print("    -H 'Content-Type: application/json' -H 'X-API-Key: <你的Key>' \\")
    print("    -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\","
          "\"params\":{\"name\":\"get_learning_context\",\"arguments\":{\"sessionRef\":\"%s\"}}}'"
          % args.session_ref)
    return 0


if __name__ == "__main__":
    sys.exit(main())
