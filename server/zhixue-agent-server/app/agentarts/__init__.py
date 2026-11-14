# -*- coding: utf-8 -*-
"""AgentArts 接入层。

对外只暴露三样东西：配置、客户端、事件归一化。业务代码不应直接碰 urllib，
也不应直接读 `AGENTARTS_*` 环境变量——统一从这里拿，便于换实现与测试注入。
"""

from .auth import AgentArtsAuthError, IamTokenProvider
from .client import AgentArtsClient, AgentArtsError, AgentArtsUnavailable
from .config import MODE_CLOUD, MODE_DISABLED, MODE_LOCAL, AgentArtsConfig
from .events import normalize

__all__ = [
    "AgentArtsAuthError",
    "AgentArtsClient",
    "AgentArtsConfig",
    "AgentArtsError",
    "AgentArtsUnavailable",
    "IamTokenProvider",
    "MODE_CLOUD",
    "MODE_DISABLED",
    "MODE_LOCAL",
    "normalize",
]
