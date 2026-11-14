# -*- coding: utf-8 -*-
"""业务模块装载器：拿到"已有的、被测过的"业务函数，而不是重写一遍。

为什么要绕一道
--------------
`import app.tools.plan_tools` 会先执行 `app/__init__.py`，而那里 import 了 flask /
dotenv。MCP 进程本身只需要纯逻辑，不应该被 Web 框架的依赖绑住；更现实的原因是：
开发机只装了 Python 3.14 时，完整依赖装不上，MCP 服务就一行都跑不起来。

这里用一个空壳 `app` 包（只给 `__path__`）占位，让 Python 的导入器直接按路径找子包，
从而**跳过 `app/__init__.py`**。代价是必须显式列出要哪些模块；好处是依赖面从
"整个 Flask 应用"缩到"几个纯函数模块"，MCP 服务可以独立启动、独立部署。

⚠️ 这里只允许装载**纯逻辑**模块（domain / tools / decision / agent.knowledge）。
凡是依赖外部服务的（grounded_exercises → 大模型），在工具内部延迟导入并在缺依赖时
返回 `TOOL_UNAVAILABLE`，避免一个可选能力拖垮整个服务。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]

_CACHE = None


def _install_stub_app_package() -> None:
    if "app" in sys.modules:
        return
    package = types.ModuleType("app")
    package.__path__ = [str(SERVER_ROOT / "app")]
    sys.modules["app"] = package


def modules():
    """返回业务模块集合（进程内只装载一次）。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    if str(SERVER_ROOT) not in sys.path:
        sys.path.insert(0, str(SERVER_ROOT))
    _install_stub_app_package()

    try:
        from app.agent import knowledge
        from app.agentarts import session
        from app.decision import priority
        from app.tools import assessment_tools, exercise_tools, plan_tools, time_budget
    except Exception as error:  # noqa: BLE001 - 启动期错误要带上下文
        raise RuntimeError(
            "无法装载业务模块（%s）。MCP 服务需要 %s 下的纯逻辑模块可导入。" % (error, SERVER_ROOT)
        ) from error

    _CACHE = types.SimpleNamespace(
        knowledge=knowledge,
        session=session,
        priority=priority,
        assessment=assessment_tools,
        exercises=exercise_tools,
        plans=plan_tools,
        budget=time_budget,
    )
    return _CACHE


def grounded_exercises():
    """可选能力：依赖大模型链路，缺依赖时由调用方翻译成 TOOL_UNAVAILABLE。"""
    from app.services import grounded_exercises as module

    return module
