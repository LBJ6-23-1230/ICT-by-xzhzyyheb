# -*- coding: utf-8 -*-
"""把演示数据复位到固定基线（SQLite）。

为什么需要它
------------
调试过程中会不断产生副作用：掌握度被推到 100、计划版本涨到 v2、出题落库一堆
`llm-*` 记录。演示视频和现场答辩要求"每次开场数字一致"，靠手工改库既慢又容易漏。

这个脚本把 `demo-user` 的数据一次性写回基线，其余账号不动。

用法
----
    python tools/reset_demo_data.py                      # 默认 /opt/zhixue/data/repository.sqlite
    python tools/reset_demo_data.py --db ./data/repository.sqlite
    python tools/reset_demo_data.py --dry-run            # 只打印将要写入的内容
    python tools/reset_demo_data.py --keep-runs          # 保留历史运行记录（默认清空）

注意
----
* 只清 `demo-user` 名下与演示相关的集合，以及与之无关的 `llm-*` 题目。
* SQLite 每次查询都读盘，**不需要重启** MCP/网关；这一点和 JSON 仓储相反。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone

DEMO_USER = "demo-user"
CONVERSATION_ID = "conv-demo-0000000000000001"
SESSION_REF = "sref-demo-0000000000000001"
KB_ID = "kb-demo"
DOCUMENT_ID = "doc-demo-1"
CHUNK_ID = "chunk-demo-1"
PLAN_ID = "plan-demo"
EXERCISE_ID = "ex-demo-1"
NOW = "2026-11-10T00:00:00+00:00"

#: 复位时整表清掉这些集合（只清 demo-user 的条目）
OWNED_COLLECTIONS = (
    "users", "profiles", "plans", "knowledge_bases", "documents", "chunks",
    "exercises", "wrong_book", "agentarts_sessions",
)


def baseline() -> dict:
    """返回 {(collection, item_id): value} 形态的基线数据。"""
    records: dict[tuple[str, str], dict] = {}

    records[("users", DEMO_USER)] = {
        "userId": DEMO_USER, "nickname": "演示用户", "grade": "大三",
        "authProvider": "demo", "createdAt": NOW,
    }

    records[("profiles", DEMO_USER)] = {
        "userId": DEMO_USER, "goal": "过数据结构期末", "examDate": "2027-01-10",
        "profileVersion": 1, "updatedAt": NOW,
        "mastery": [
            {"knowledgePointId": "binary-tree-traversal", "knowledgePointName": "二叉树遍历基础",
             "masteryScore": 42, "source": "seed"},
            {"knowledgePointId": "graph-algorithm", "knowledgePointName": "图算法",
             "masteryScore": 55, "source": "seed"},
            {"knowledgePointId": "hash-collision", "knowledgePointName": "哈希冲突",
             "masteryScore": 63, "source": "seed"},
        ],
    }

    records[("plans", PLAN_ID)] = {
        "planId": PLAN_ID, "userId": DEMO_USER, "version": 1,
        "generatedAt": NOW, "reason": "seed",
        "tasks": [
            {"taskId": "task-postorder", "knowledgePointId": "binary-tree-traversal",
             "title": "二叉树后序遍历", "durationMinutes": 30, "status": "pending"},
            {"taskId": "task-graph", "knowledgePointId": "graph-algorithm",
             "title": "图算法", "durationMinutes": 30, "status": "pending"},
        ],
        "constraints": {"availableMinutes": 60},
    }

    records[("knowledge_bases", KB_ID)] = {
        "kbId": KB_ID, "userId": DEMO_USER, "name": "课程资料库",
        "courseName": "数据结构", "description": "演示用讲义库",
        "documentCount": 1, "createdAt": NOW,
    }

    records[("documents", DOCUMENT_ID)] = {
        "documentId": DOCUMENT_ID, "kbId": KB_ID, "userId": DEMO_USER,
        "fileName": "二叉树讲义.pdf", "status": "ready",
        "charCount": 120, "chunkCount": 1, "mimeType": "application/pdf",
        "parsedAt": NOW, "uploadedAt": NOW, "sha256": "demo-sha", "extractionVersion": 1,
    }

    records[("chunks", CHUNK_ID)] = {
        "chunkId": CHUNK_ID, "documentId": DOCUMENT_ID, "kbId": KB_ID,
        "userId": DEMO_USER, "index": 0,
        # 章节里必须出现知识点名或 id：检索按"知识点名出现在文本/章节"匹配
        "headingPath": "第3章 > 3.2 二叉树遍历基础（binary-tree-traversal）",
        "text": "二叉树遍历基础：分为前序、中序和后序。后序遍历的顺序是左右根，"
                "先访问左子树，再访问右子树，最后访问根节点。中序遍历的顺序是左根右，"
                "常用于二叉搜索树的有序输出。前序遍历的顺序是根左右。",
    }

    records[("exercises", EXERCISE_ID)] = {
        "exerciseId": EXERCISE_ID, "userId": DEMO_USER, "answerKey": "B",
        "knowledgePointId": "binary-tree-traversal",
        "knowledgePointName": "二叉树遍历基础",
        "stem": "后序遍历的访问顺序是？",
        "options": ["A. 根左右", "B. 左右根", "C. 左根右", "D. 右根左"],
        "difficulty": "easy", "source": "seed", "createdAt": NOW,
    }

    records[("agentarts_sessions", CONVERSATION_ID)] = {
        "conversationId": CONVERSATION_ID, "userId": DEMO_USER,
        "chatSessionId": "chat-demo", "agentId": "agent-demo",
        "sessionRef": SESSION_REF, "createdAt": NOW, "lastActiveAt": NOW,
    }

    return records


def reset(db_path: str, dry_run: bool = False, keep_runs: bool = False) -> None:
    data = baseline()
    connection = sqlite3.connect(db_path)

    if dry_run:
        print("将写入 %d 条基线记录：" % len(data))
        for (collection, item_id) in sorted(data):
            print("  %-20s %s" % (collection, item_id))
        connection.close()
        return

    try:
        # 只清 demo-user 的数据，别把队友/真实账号的记录一起删了
        for collection in OWNED_COLLECTIONS:
            rows = connection.execute(
                "select item_id, value from records where collection=?", (collection,)
            ).fetchall()
            for item_id, raw in rows:
                try:
                    value = json.loads(raw)
                except ValueError:
                    value = {}
                owned = value.get("userId") in (DEMO_USER, None) or item_id.startswith(DEMO_USER)
                if owned:
                    connection.execute(
                        "delete from records where collection=? and item_id=?",
                        (collection, item_id))
            # 残余：出题生成的 llm-* 记录（判分依赖它，清掉可保证演示时题号唯一）
            connection.execute(
                "delete from records where collection=? and item_id like 'llm-%'", (collection,))

        if not keep_runs:
            for collection in ("agentarts_runs", "agentarts_events"):
                connection.execute("delete from records where collection=?", (collection,))

        for (collection, item_id), value in data.items():
            connection.execute(
                "insert or replace into records(collection,item_id,value) values(?,?,?)",
                (collection, item_id, json.dumps(value, ensure_ascii=False)),
            )
        connection.commit()
    finally:
        connection.close()

    print("已复位演示基线（用户 %s）：" % DEMO_USER)
    print("  画像     掌握度 42 / 55 / 63，版本 1")
    print("  计划     %s v1，两个任务各 30 分钟" % PLAN_ID)
    print("  资料     %s（%s，1 个切片）" % (KB_ID, DOCUMENT_ID))
    print("  题       %s，标准答案 B" % EXERCISE_ID)
    print("  会话     %s" % SESSION_REF)
    if not keep_runs:
        print("  运行记录 已清空（时间线从零开始）")
    print("\n提示：SQLite 即时生效，无需重启服务。")


def main() -> None:
    parser = argparse.ArgumentParser(description="复位演示数据到固定基线")
    parser.add_argument("--db", default="/opt/zhixue/data/repository.sqlite",
                        help="SQLite 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    parser.add_argument("--keep-runs", action="store_true", help="保留历史运行记录")
    args = parser.parse_args()
    reset(args.db, dry_run=args.dry_run, keep_runs=args.keep_runs)


if __name__ == "__main__":
    main()
