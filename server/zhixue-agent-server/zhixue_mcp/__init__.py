# -*- coding: utf-8 -*-
"""知学 Mate 的自定义 MCP 服务（Streamable HTTP）。

对外只有一个入口：`python -m zhixue_mcp.server`。
工具清单与契约见 `zhixue_mcp/contracts/tools.json`，部署说明见 `zhixue_mcp/README.md`。
"""

__all__ = ["envelope", "registry", "server"]
