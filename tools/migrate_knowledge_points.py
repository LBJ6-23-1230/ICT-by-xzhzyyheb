# -*- coding: utf-8 -*-
"""把历史数据里的知识点归一到 kpId（一次性迁移）。

背景
----
以前同一概念会有三个 ID：用户填的"二叉树"、资料抽的"二叉树遍历基础"、
题库的"binary-tree-traversal"。迁移做三件事：

1. 用题库建立 ID↔名称 映射，给画像/错题/题目里的知识点补 kpId；
2. 合并画像中**同一 kpId 的重复掌握度行**（保留 attempts 大的，attempts 相加）；
3. 顺带把 `knowledge_points` 注册表补齐。

用法
----
    python tools/migrate_knowledge_points.py --db /opt/zhixue/data/repository.sqlite
    python tools/migrate_knowledge_points.py --db ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

BANK_CANDIDATES = (
    Path("/opt/zhixue/data/question_bank.json"),
    Path(__file__).resolve().parents[1] / "server" / "zhixue-agent-server" / "data" / "question_bank.json",
)

_PUNCT = re.compile(r"[\s\-_/、，,。.（）()【】\[\]“”\"'：:;；]")


def normalize(text: str) -> str:
    return _PUNCT.sub("", str(text or "")).casefold()


def load_bank() -> dict:
    """返回 {normalize(name): kpId} 与 {kpId: name}。"""
    by_name, names = {}, {}
    for path in BANK_CANDIDATES:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for item in (data.get("exercises") or []):
            kp_id = str(item.get("knowledgePointId") or "").strip()
            name = str(item.get("knowledgePointName") or "").strip()
            if kp_id:
                names[kp_id] = name or kp_id
                if name:
                    by_name[normalize(name)] = kp_id
        if names:
            break
    return {"by_name": by_name, "names": names}


def map_point(raw: str, bank: dict) -> str:
    text = str(raw or "").strip()
    if not text:
        return text
    if text in bank["names"]:
        return text
    key = normalize(text)
    if key in bank["by_name"]:
        return bank["by_name"][key]

    # 包含匹配只在唯一命中时采用："二叉树"同时是"二叉树遍历基础"和
    # "二叉树后序遍历"的子串，猜哪个都是错的。
    hits = [kp_id for name, kp_id in bank["by_name"].items()
            if len(key) >= 2 and (key in name or name in key)]
    hits = list(dict.fromkeys(hits))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print("  ⚠ 歧义：%s 同时匹配 %s，保持原样" % (text, ", ".join(hits)))
    return text


def migrate(db_path: str, dry_run: bool = False) -> None:
    bank = load_bank()
    if not bank["names"]:
        print("⚠ 没找到题库（question_bank.json），只能用已有数据迁移")
    conn = sqlite3.connect(db_path)
    rows = conn.execute("select collection, item_id, value from records").fetchall()
    print("读取 %d 条记录；题库知识点 %d 个" % (len(rows), len(bank["names"])))

    updates = []          # (collection, item_id, new_value)
    registry = {}         # kpId -> name

    for collection, item_id, raw in rows:
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        changed = False

        if collection == "profiles":
            merged = {}
            new_mastery = []
            point_changed = False
            for point in (value.get("mastery") or []):
                if not isinstance(point, dict):
                    continue
                original = str(point.get("knowledgePointId") or "")
                kp_id = map_point(original, bank)
                name = bank["names"].get(kp_id) or point.get("knowledgePointName") or kp_id
                registry.setdefault(kp_id, name)
                point = dict(point, knowledgePointId=kp_id, knowledgePointName=name)
                # 只是改了 ID（没有重复行）也必须写回去——这里漏过标记，
                # 导致"二叉树 37"这种历史行原样留在画像里。
                if kp_id != original:
                    point_changed = True
                if kp_id in merged:      # 合并重复行
                    kept = merged[kp_id]
                    kept["attempts"] = int(kept.get("attempts") or 0) + int(point.get("attempts") or 0)
                    kept["assessed"] = bool(kept.get("assessed") or point.get("assessed"))
                    point_changed = True
                    continue
                merged[kp_id] = point
                new_mastery.append(point)
            if point_changed:
                value["mastery"] = new_mastery
                changed = True

        elif collection in ("wrong_book", "exercises"):
            raw_point = str(value.get("knowledgePointId") or "")
            kp_id = map_point(raw_point, bank)
            if kp_id != raw_point:
                value["knowledgePointId"] = kp_id
                value["knowledgePointName"] = bank["names"].get(
                    kp_id, value.get("knowledgePointName") or kp_id)
                registry.setdefault(kp_id, value["knowledgePointName"])
                changed = True

        if changed:
            updates.append((collection, item_id, json.dumps(value, ensure_ascii=False)))

    print("需要更新 %d 条；涉及知识点 %d 个" % (len(updates), len(registry)))
    for kp_id, name in list(registry.items())[:10]:
        print("  %-32s %s" % (kp_id, name))

    if dry_run:
        conn.close()
        print("（dry-run，未写入）")
        return

    for collection, item_id, value in updates:
        conn.execute("update records set value=? where collection=? and item_id=?",
                     (value, collection, item_id))
    for kp_id, name in registry.items():
        conn.execute("insert or replace into records(collection,item_id,value) values(?,?,?)",
                     ("knowledge_points", kp_id,
                      json.dumps({"kpId": kp_id, "name": name, "aliases": [name],
                                  "source": "migrated", "confirmed": True},
                                 ensure_ascii=False)))
    conn.commit()
    conn.close()
    print("完成：更新 %d 条，注册表补 %d 条" % (len(updates), len(registry)))


def main() -> None:
    parser = argparse.ArgumentParser(description="知识点 ID 归一迁移")
    parser.add_argument("--db", default="/opt/zhixue/data/repository.sqlite")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(args.db, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
