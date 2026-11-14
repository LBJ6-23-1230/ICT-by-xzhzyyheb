# -*- coding: utf-8 -*-
"""知识点注册表与解析器（P0）。

为什么需要它
------------
知识点是学习域的**主键**。以前同一概念有三个名字：

    用户填   "二叉树"            → ID 就是"二叉树"
    资料抽   "二叉树遍历基础"      → ID 是"二叉树遍历基础"
    题库里   "binary-tree-traversal" → ID 是英文

于是判分会新建一条掌握度、诊断里同一知识点并列出现、出题按名字子串匹配还可能失败。

口径（只此一条）
----------------
**任何地方都不许把"用户/资料/题库给的说法"直接当 ID。**
一律先过 `resolve()` 换成 kpId，换不出来再决定"新建"还是"拒绝"。

存储
----
集合 `knowledge_points`，key = kpId：

    {"kpId":"kp-xxxx", "name":"二叉树遍历", "aliases":["二叉树", "binary-tree-traversal"],
     "course":"数据结构", "source":"bank|declared|extracted", "confirmed":true}
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_BANK_PATHS = (
    Path(__file__).resolve().parents[2] / "data" / "question_bank.json",
)

_PUNCT = re.compile(r"[\s\-_/、，,。.（）()【】\[\]“”\"'：:;；]")


def normalize(text: str) -> str:
    """归一化：去空白与标点、全角转半角、大小写统一。

    中文场景下"二叉树" "二叉树 " "二叉树（遍历）" 都应该等价。
    """
    value = str(text or "")
    value = value.translate({0x3000: 0x20})
    for full, half in zip("０１２３４５６７８９", "0123456789"):
        value = value.replace(full, half)
    return _PUNCT.sub("", value).casefold()


def _slug(text: str) -> str:
    return "kp-" + hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:10]


def _rows(repository) -> list:
    return [row for row in repository.list("knowledge_points") if isinstance(row, dict)]


def get(repository, kp_id: str) -> dict | None:
    row = repository.get("knowledge_points", kp_id)
    return row if isinstance(row, dict) else None


def name_of(repository, kp_id: str, fallback: str = "") -> str:
    row = get(repository, kp_id)
    return (row or {}).get("name") or fallback or kp_id


def resolve(repository, raw: str) -> str | None:
    """把一个"说法"换成一个已注册的 kpId；换不出来或**有歧义**返回 None。

    匹配顺序：精确（name / alias / kpId）→ 包含（双向，≥2 字）。

    包含匹配只在**唯一命中**时才算数：像"二叉树"这种简称，
    同时是"二叉树遍历基础"和"二叉树后序遍历"的子串——猜哪个都是错的。
    宁可返回 None（调用方会新建或追问），也不替用户做决定。
    """
    key = normalize(raw)
    if not key:
        return None

    containment: list[tuple[int, str]] = []   # (优先级, kpId)
    for row in _rows(repository):
        kp_id = str(row.get("kpId") or "")
        if normalize(kp_id) == key:
            return kp_id
        candidates = [row.get("name") or "", *(row.get("aliases") or [])]
        for candidate in candidates:
            other = normalize(candidate)
            if not other:
                continue
            if other == key:
                return kp_id                      # 精确命中，直接返回
            if len(key) < 2 or len(other) < 2:
                continue
            if key in other:
                # 输入是候选的一部分（"二叉树遍历" ⊂ "二叉树遍历基础"）——更具体的知识点，优先
                containment.append((0, kp_id))
            elif other in key:
                # 候选是输入的一部分（"二叉树" ⊂ "二叉树遍历"）——伞形条目，次优
                containment.append((1, kp_id))

    if not containment:
        return None
    best_priority = min(priority for priority, _ in containment)
    best = list(dict.fromkeys(kp for priority, kp in containment if priority == best_priority))
    return best[0] if len(best) == 1 else None


def _known_id(repository, raw: str) -> bool:
    """raw 是否已经作为知识点 ID 被使用（画像/计划/题目/错题本）。

    用途：注册时**沿用已有 ID**，不要另生成 kp-xxx。
    否则历史数据（binary-tree-traversal 之类）会全部失联。
    """
    if not raw:
        return False
    for row in repository.list("profiles"):
        if isinstance(row, dict):
            for point in (row.get("mastery") or []):
                if isinstance(point, dict) and point.get("knowledgePointId") == raw:
                    return True
    for row in repository.list("plans"):
        if isinstance(row, dict):
            for task in (row.get("tasks") or []):
                if isinstance(task, dict) and task.get("knowledgePointId") == raw:
                    return True
    for collection in ("exercises", "wrong_book"):
        for row in repository.list(collection):
            if isinstance(row, dict) and row.get("knowledgePointId") == raw:
                return True
    return False


def ensure(repository, raw: str, *, name: str = "", source: str = "declared",
           course: str = "", chapter: str = "") -> str:
    """解析或新建，返回 kpId。命中已有条目时顺便把新说法并成别名。"""
    existing = resolve(repository, raw)
    if existing:
        row = get(repository, existing) or {}
        aliases = list(dict.fromkeys([*(row.get("aliases") or []), str(raw).strip()]))
        if aliases != (row.get("aliases") or []):
            repository.save("knowledge_points", existing,
                            dict(row, aliases=aliases, kpId=existing))
        return existing

    display = (name or raw).strip()[:60]
    # 已有系统内 ID（历史数据/题库）→ 直接沿用，避免断链
    kp_id = str(raw).strip() if _known_id(repository, str(raw).strip()) else _slug(display)
    if get(repository, kp_id) is not None:      # 哈希冲突：名字加后缀
        kp_id = _slug(display + "#" + str(len(_rows(repository))))
    repository.save("knowledge_points", kp_id, {
        "kpId": kp_id,
        "name": display,
        "aliases": list(dict.fromkeys([str(raw).strip(), display])),
        "course": course,
        "chapter": chapter,
        "source": source,
        "confirmed": source != "extracted",
        "createdAt": _now(),
    })
    return kp_id


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def register(repository, kp_id: str, name: str, aliases=(), source: str = "bank",
             course: str = "", chapter: str = "") -> str:
    """按指定 ID 注册（题库导入用，保留原 ID 便于和历史数据对齐）。"""
    row = get(repository, kp_id) or {}
    merged = list(dict.fromkeys([*(row.get("aliases") or []), *aliases, name]))
    repository.save("knowledge_points", kp_id, {
        "kpId": kp_id,
        "name": name or kp_id,
        "aliases": [item for item in merged if item],
        "course": course or row.get("course") or "",
        "chapter": chapter or row.get("chapter") or "",
        "source": row.get("source") or source,
        "confirmed": True,
        "createdAt": row.get("createdAt") or _now(),
    })
    return kp_id


def bootstrap(repository) -> dict:
    """幂等初始化：题库 → 注册表；已有画像/错题里的知识点名 → 别名。"""
    registered = 0
    for path in _BANK_PATHS:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for item in (data.get("exercises") or []):
            kp_id = str(item.get("knowledgePointId") or "").strip()
            name = str(item.get("knowledgePointName") or "").strip()
            if kp_id and name:
                register(repository, kp_id, name, course=str(item.get("courseName") or ""))
                registered += 1

    # 既有数据里的知识点：名字并入别名，保证老 ID 还能被解析到
    for row in repository.list("profiles"):
        if not isinstance(row, dict):
            continue
        for point in (row.get("mastery") or []):
            if not isinstance(point, dict):
                continue
            kp_id = str(point.get("knowledgePointId") or "").strip()
            name = str(point.get("knowledgePointName") or "").strip()
            if not kp_id:
                continue
            register(repository, kp_id, name or kp_id, aliases=[name] if name else [],
                     source="declared")
    return {"registered": registered, "total": len(_rows(repository))}
