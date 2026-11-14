# -*- coding: utf-8 -*-
"""MCP 侧鉴权：服务身份（API Key）+ 终端用户身份（sessionRef）。

两层身份，缺一不可
------------------
* **服务身份**：平台调用 MCP 时带 API Key。它证明"请求来自我们的 AgentArts 应用"，
  但不代表任何具体学生。
* **用户身份**：工具参数里的 `sessionRef`。网关在调用智能体前生成它，随 `inputs`
  下发给智能体，再由智能体作为工具参数回传。MCP 用它换出 `userId`。

为什么不让模型直接传 `userId`
----------------------------
模型可能被提示词注入影响。一旦 `userId` 成为工具入参，就等于把"能不能查别人的数据"
交给模型判断。`sessionRef` 是 32 位随机串，模型看不到别人的，也伪造不出来——越权面
从"模型自觉"变成"密码学随机"。
"""

from __future__ import annotations

import secrets

from . import envelope

#: 幂等记录集合。与 api/plans.py 的 `plan_adjustments` 同样思路：写操作带 operation id，
#: 重复提交直接返回首次结果，而不是再写一遍。
IDEMPOTENCY = "mcp_idempotency"


def check_api_key(presented: str, expected: str) -> bool:
    """常量时间比较，避免按位爆破。未配置 Key 时**拒绝**而不是放行。"""
    if not expected:
        return False
    return secrets.compare_digest((presented or "").strip(), expected)


def resolve_actor(repository, session_ref: str):
    """`sessionRef` → 会话记录。返回 None 表示无效或已失效。"""
    if not session_ref:
        return None
    # 延迟导入：避免模块加载时就触发 app 包初始化。
    from .business import modules

    return modules().session.resolve_by_session_ref(repository, session_ref)


def idempotency_get(repository, user_id: str, key: str):
    if not key:
        return None
    return repository.get(IDEMPOTENCY, "%s:%s" % (user_id, key))


def idempotency_put(repository, user_id: str, key: str, value) -> None:
    if not key:
        return
    repository.save(IDEMPOTENCY, "%s:%s" % (user_id, key), {
        "userId": user_id,
        "key": key,
        "response": value,
    })


def require_actor(repository, arguments: dict):
    """取出并校验 `sessionRef`，返回 (actor, error_envelope)。"""
    session_ref = str(arguments.get("sessionRef") or "").strip()
    if not session_ref:
        return None, envelope.fail(envelope.UNAUTHORIZED,
                                   "缺少 sessionRef：该工具必须由网关签发的一次性会话引用调用")
    record = resolve_actor(repository, session_ref)
    if not isinstance(record, dict):
        return None, envelope.fail(envelope.UNAUTHORIZED,
                                   "sessionRef 无效或已失效，拒绝访问")
    user_id = str(record.get("userId") or "")
    if not user_id:
        return None, envelope.fail(envelope.UNAUTHORIZED, "会话记录缺少 userId")
    return record, None
