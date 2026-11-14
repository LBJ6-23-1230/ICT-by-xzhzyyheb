# -*- coding: utf-8 -*-
"""课程与知识点目录的增删改查（用户可维护）。

设计
----
* 课程存在 `courses`，key = `{userId}:{courseName}`（与其它集合的隔离口径一致）。
* 知识点存在全局注册表 `knowledge_points`（跨用户共享词汇），
  但"我的学习范围"是 `profiles.mastery`——两者分开，互不污染。
* 用户改知识点名 = 改注册表 `name`，别名保留旧名，历史数据仍能解析到同一个 kpId。
"""

from __future__ import annotations

from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _course_key(user_id: str, name: str) -> str:
    return "%s:%s" % (user_id, name)


def list_courses(repository, user_id: str) -> list:
    rows = [row for row in repository.list("courses")
            if isinstance(row, dict) and row.get("userId") == user_id]
    points = [row for row in repository.list("knowledge_points") if isinstance(row, dict)]
    by_course = {}
    for row in points:
        by_course.setdefault(row.get("course") or "", []).append(row)
    return [{
        "courseName": row.get("courseName"),
        "examDate": row.get("examDate") or "",
        "pointCount": len(by_course.get(row.get("courseName") or "", [])),
    } for row in sorted(rows, key=lambda r: str(r.get("courseName") or ""))]


def save_course(repository, user_id: str, name: str, exam_date: str = "",
                old_name: str = "") -> dict:
    name = str(name or "").strip()[:60]
    if not name:
        raise ValueError("课程名不能为空")
    if old_name and old_name != name:
        repository.delete("courses", _course_key(user_id, old_name))
    repository.save("courses", _course_key(user_id, name), {
        "userId": user_id, "courseName": name,
        "examDate": str(exam_date or "").strip()[:20], "updatedAt": _now(),
    })
    return {"courseName": name, "examDate": exam_date}


def delete_course(repository, user_id: str, name: str) -> bool:
    key = _course_key(user_id, str(name or "").strip())
    if not isinstance(repository.get("courses", key), dict):
        return False
    repository.delete("courses", key)
    return True


def list_points(repository, user_id: str, course: str = "") -> list:
    """按课程列出知识点。

    知识点**必须属于一门课程**：指定 course 时只返回该课程的条目，
    未归类的单独在 `unassigned` 里返回，由前端提示用户归入某门课。
    """
    profile = repository.get("profiles", user_id) or {}
    mine = {str(row.get("knowledgePointId")): row
            for row in (profile.get("mastery") or []) if isinstance(row, dict)}

    result = []
    for row in repository.list("knowledge_points"):
        if not isinstance(row, dict):
            continue
        row_course = row.get("course") or ""
        if course and row_course != course:
            continue
        kp_id = str(row.get("kpId") or "")
        mine_row = mine.get(kp_id) or {}
        result.append({
            "kpId": kp_id,
            "name": row.get("name") or kp_id,
            "aliases": row.get("aliases") or [],
            "course": row_course,
            "source": row.get("source") or "declared",
            "inScope": bool(mine_row),
            "masteryScore": mine_row.get("masteryScore"),
            "assessed": bool(mine_row.get("assessed")),
        })
    result.sort(key=lambda item: item["name"])
    return result


def unassigned_points(repository) -> list:
    """还没有归属课程的知识点（题库初始化的那些）。"""
    return [{"kpId": row.get("kpId"), "name": row.get("name") or row.get("kpId")}
            for row in repository.list("knowledge_points")
            if isinstance(row, dict) and not (row.get("course") or "").strip()]


def upsert_points(repository, user_id: str, course: str, names: list) -> dict:
    """批量创建/加入知识点，并同步进"我的学习范围"（未评估）。

    课程必填：知识点必须挂在课程下，否则学情无法按课程聚合。
    """
    course = str(course or "").strip()
    if not course:
        raise ValueError("必须先选择或创建课程，知识点必须属于一门课程")

    from app.services import knowledge_registry as registry

    # 课程不存在时自动创建，避免"有知识点但没有课程"的孤儿数据
    if not isinstance(repository.get("courses", _course_key(user_id, course)), dict):
        save_course(repository, user_id, course)

    profile = repository.get("profiles", user_id)
    profile = dict(profile) if isinstance(profile, dict) else {"userId": user_id}
    mastery = [row for row in (profile.get("mastery") or []) if isinstance(row, dict)]
    index = {str(row.get("knowledgePointId")): row for row in mastery}

    created, added = [], []
    for raw in names:
        name = str(raw or "").strip()[:60]
        if not name:
            continue
        kp_id = registry.resolve(repository, name)
        if kp_id is None:
            kp_id = registry.ensure(repository, name, name=name,
                                    source="declared", course=course)
            created.append(name)
        else:
            # 复用已有条目时，把它的课程补上（题库初始化的是空课程）
            row = registry.get(repository, kp_id) or {}
            if not (row.get("course") or "").strip():
                repository.save("knowledge_points", kp_id,
                                dict(row, course=course, kpId=kp_id))
        if kp_id in index:
            continue
        row = {"knowledgePointId": kp_id,
               "knowledgePointName": registry.name_of(repository, kp_id, name),
               "masteryScore": 0, "source": "declared",
               "assessed": False, "attempts": 0}
        mastery.append(row)
        index[kp_id] = row
        added.append(row["knowledgePointName"])

    profile["mastery"] = mastery
    profile["userId"] = user_id
    profile["profileVersion"] = int(profile.get("profileVersion") or 0) + 1
    profile["updatedAt"] = _now()
    repository.save("profiles", user_id, profile)
    return {"created": created, "added": added, "scopeSize": len(mastery),
            "course": course}


def assign_course(repository, user_id: str, kp_id: str, course: str) -> bool:
    """把某个知识点归入课程（用于处理题库初始化出来的"未归类"条目）。"""
    from app.services import knowledge_registry as registry

    course = str(course or "").strip()
    if not course:
        return False
    row = registry.get(repository, kp_id)
    if row is None:
        return False
    if not isinstance(repository.get("courses", _course_key(user_id, course)), dict):
        save_course(repository, user_id, course)
    repository.save("knowledge_points", kp_id, dict(row, course=course, kpId=kp_id))
    return True


def rename_point(repository, user_id: str, kp_id: str, new_name: str) -> bool:
    from app.services import knowledge_registry as registry

    new_name = str(new_name or "").strip()[:60]
    if not new_name:
        return False
    row = registry.get(repository, kp_id)
    if row is None:
        return False
    old = row.get("name") or ""
    aliases = list(dict.fromkeys([*(row.get("aliases") or []), old, new_name]))
    repository.save("knowledge_points", kp_id,
                    dict(row, name=new_name, aliases=[a for a in aliases if a]))
    # 画像里的展示名同步更新（ID 不变）
    profile = repository.get("profiles", user_id)
    if isinstance(profile, dict):
        for point in (profile.get("mastery") or []):
            if isinstance(point, dict) and point.get("knowledgePointId") == kp_id:
                point["knowledgePointName"] = new_name
        repository.save("profiles", user_id, profile)
    return True


def remove_point(repository, user_id: str, kp_id: str) -> bool:
    """从"我的学习范围"移除（注册表保留，历史数据仍可解析）。"""
    profile = repository.get("profiles", user_id)
    if not isinstance(profile, dict):
        return False
    mastery = [row for row in (profile.get("mastery") or [])
               if isinstance(row, dict) and str(row.get("knowledgePointId")) != kp_id]
    if len(mastery) == len(profile.get("mastery") or []):
        return False
    profile = dict(profile, mastery=mastery,
                   profileVersion=int(profile.get("profileVersion") or 0) + 1,
                   updatedAt=_now())
    repository.save("profiles", user_id, profile)
    return True
