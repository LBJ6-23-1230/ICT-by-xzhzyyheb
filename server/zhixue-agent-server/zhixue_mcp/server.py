# -*- coding: utf-8 -*-
"""MCP 服务：Streamable HTTP + JSON-RPC 2.0（仅标准库）。

为什么不用官方 SDK
------------------
1. 这个服务的职责很窄：暴露 11 个工具、做鉴权与信封。SDK 带来的主要价值是协议细节，
   而 MCP over HTTP 的必需部分（initialize / tools/list / tools/call）只有几十行。
2. 团队当前环境装不上完整依赖（锁在 Python 3.12），如果服务强依赖 SDK，本地连
   "能不能起、工具列表对不对"都无法验证。**能自测的代码才敢上演示台。**
3. 少一个依赖，就少一个部署时挂掉的点。

协议覆盖：`initialize`、`notifications/initialized`、`tools/list`、`tools/call`、`ping`。
未实现的方法返回 -32601，不假装支持。

启动::

    python -m zhixue_mcp.server --check            # 只打印工具清单
    python -m zhixue_mcp.server --dump-contracts   # 生成 contracts/tools.json
    python -m zhixue_mcp.server --port 8765        # 本地起服务（默认 127.0.0.1:8765）

生产环境必须设 `MCP_API_KEY`（平台侧创建 MCP 时填同一个值）。
未设 Key 时默认**拒绝所有请求**——只有显式 `MCP_ALLOW_NO_KEY=1` 才放行，供本机调试。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import auth, envelope
from .business import SERVER_ROOT
from .tools import build_registry

logger = logging.getLogger("zhixue.mcp")

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "zhixue-learning-tools"
TOOL_REGISTRY = None


def registry():
    global TOOL_REGISTRY
    if TOOL_REGISTRY is None:
        TOOL_REGISTRY = build_registry()
    return TOOL_REGISTRY


# ------------------------------------------------------------------ 存储
def open_repository():
    """按环境变量打开仓储。与 Flask 后端共用同一份数据，MCP 只做读写，不建表。

    路径优先级与 `create_app()` 保持一致：
        `ZHIXUE_DB=sqlite[:path]` > `ZHIXUE_REPOSITORY` > 仓库内 `data/repository.json`

    ⚠️ 曾经漏读 `ZHIXUE_REPOSITORY`，导致自检把测试数据写进了开发库
    （实测：`data/repository.json` 被塞入 u-demo 的画像/计划/幂等记录）。
    测试必须能把数据导向临时目录，否则"测试会改真实数据"是必然踩的坑。
    """
    from .business import modules

    modules()  # 确保 stub 包就绪，避免直接 import app.* 触发 app/__init__
    spec = (os.getenv("ZHIXUE_DB") or "json").strip()
    backend, _, explicit = spec.partition(":")
    explicit = explicit.strip()

    if backend.lower() == "sqlite":
        from app.repositories.sqlite_repository import SQLiteRepository

        target = Path(explicit) if explicit else SERVER_ROOT / "data" / "repository.sqlite"
        target.parent.mkdir(parents=True, exist_ok=True)
        return SQLiteRepository(target)

    from app.repositories.json_repository import JsonRepository

    configured = (os.getenv("ZHIXUE_REPOSITORY") or "").strip()
    target = Path(configured) if configured else SERVER_ROOT / "data" / "repository.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    return JsonRepository(target)


# ------------------------------------------------------------------ JSON-RPC
def _rpc_result(request_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def handle_message(payload, repository) -> dict | None:
    """处理单条 JSON-RPC 消息。通知（无 id）返回 None。"""
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "invalid request")

    method = str(payload.get("method") or "")
    request_id = payload.get("id")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

    if method == "initialize":
        return _rpc_result(request_id, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": envelope.VERSION},
        })

    if method.startswith("notifications/"):
        return None

    if method == "ping":
        return _rpc_result(request_id, {})

    if method == "tools/list":
        return _rpc_result(request_id, {"tools": registry().describe()})

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        context = type("Context", (), {
            "repository": repository,
            "user_id": "",
            "session_ref": str(arguments.get("sessionRef") or ""),
            "conversation_id": "",
            "idempotency_key": str(arguments.get("idempotencyKey") or ""),
        })()
        result = registry().call(name, arguments, context)
        return _rpc_result(request_id, {
            "content": [{"type": "text", "text": envelope.as_text(result)}],
            "isError": not result.get("ok", False),
        })

    return _rpc_error(request_id, -32601, "method not found: %s" % method)


# ------------------------------------------------------------------ HTTP
class McpHandler(BaseHTTPRequestHandler):
    server_version = "ZhixueMcp/1.0"
    repository = None
    api_key = ""
    allow_no_key = False

    # 默认日志会带上每个请求的完整路径与客户端地址，演示环境足够，且不打印请求体。
    def log_message(self, fmt, *args):  # noqa: A003 - 覆盖基类签名
        logger.info("mcp %s", fmt % args)

    # -------------------------------------------------------------- 工具
    def _authorized(self) -> bool:
        if not self.api_key:
            return self.allow_no_key
        presented = (self.headers.get("X-API-Key")
                     or self.headers.get("Authorization") or "").strip()
        if presented.lower().startswith("bearer "):
            presented = presented[7:].strip()
        return auth.check_api_key(presented, self.api_key)

    def _send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -------------------------------------------------------------- 路由
    def do_GET(self):  # noqa: N802 - http.server 约定
        if self.path.split("?")[0] in ("/healthz", "/health"):
            if not self._authorized():
                self._send_json(401, {"errorCode": "UNAUTHORIZED", "message": "invalid api key"})
                return
            self._send_json(200, {
                "status": "ok",
                "server": SERVER_NAME,
                "version": envelope.VERSION,
                "protocolVersion": PROTOCOL_VERSION,
                "tools": len(registry().describe()),
                "writes": registry().writes(),
            })
            return
        self._send_json(404, {"errorCode": "NOT_FOUND", "message": "use POST /mcp"})

    def do_POST(self):  # noqa: N802
        if self.path.split("?")[0] != "/mcp":
            self._send_json(404, {"errorCode": "NOT_FOUND", "message": "use POST /mcp"})
            return
        if not self._authorized():
            self._send_json(401, {"errorCode": "UNAUTHORIZED", "message": "invalid api key"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            self._send_json(200, _rpc_error(None, -32700, "parse error"))
            return

        if isinstance(payload, list):  # 批量请求
            responses = [item for item in
                         (handle_message(entry, self.repository) for entry in payload)
                         if item is not None]
            if not responses:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_json(200, responses)
            return

        response = handle_message(payload, self.repository)
        if response is None:  # 通知不需要响应
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(200, response)


def create_server(host: str, port: int, repository=None,
                  api_key: str = "", allow_no_key: bool = False) -> ThreadingHTTPServer:
    handler = type("BoundMcpHandler", (McpHandler,), {
        "repository": repository if repository is not None else open_repository(),
        "api_key": api_key,
        "allow_no_key": allow_no_key,
    })
    return ThreadingHTTPServer((host, port), handler)


# ------------------------------------------------------------------ 入口
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="知学 Mate MCP 服务（Streamable HTTP）")
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8765")))
    parser.add_argument("--cert", default=os.getenv("MCP_TLS_CERT", ""),
                        help="PEM 证书路径；与 --key 同时给出才启用 HTTPS")
    parser.add_argument("--key", default=os.getenv("MCP_TLS_KEY", ""),
                        help="PEM 私钥路径")
    parser.add_argument("--check", action="store_true", help="只打印工具清单，不启动服务")
    parser.add_argument("--dump-contracts", action="store_true",
                        help="把工具契约写入 contracts/tools.json")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.dump_contracts:
        target = Path(__file__).resolve().parent / "contracts" / "tools.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "version": envelope.VERSION,
            "protocolVersion": PROTOCOL_VERSION,
            "tools": registry().describe(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写入 %s（%d 个工具）" % (target, len(registry().describe())))
        return 0

    if args.check:
        print("MCP 工具清单（%d 个，其中写操作 %d 个）" %
              (len(registry().describe()), len(registry().writes())))
        for tool in registry().describe():
            print("  - %-28s %s%s" % (tool["name"], tool["title"],
                                      "  [写]" if tool["name"] in registry().writes() else ""))
        return 0

    api_key = (os.getenv("MCP_API_KEY") or "").strip()
    allow_no_key = (os.getenv("MCP_ALLOW_NO_KEY") or "").strip() in ("1", "true", "yes")
    if not api_key and not allow_no_key:
        print("拒绝启动：未配置 MCP_API_KEY。平台侧创建 MCP 时要填同一个 Key；"
              "本机调试可设 MCP_ALLOW_NO_KEY=1。", file=sys.stderr)
        return 2

    use_tls = bool(args.cert and args.key)
    if bool(args.cert) != bool(args.key):
        print("拒绝启动：--cert 与 --key 必须同时提供。", file=sys.stderr)
        return 2

    server = create_server(args.host, args.port, api_key=api_key, allow_no_key=allow_no_key)

    scheme = "http"
    if use_tls:
        # 直接在本进程做 TLS，不引入 Nginx：少一个组件就少一层配置错误。
        # 证书用自签即可跑通"平台能不能连"的验证；正式域名再换正式证书。
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=args.cert, keyfile=args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    print("MCP 服务已启动：%s://%s:%d/mcp  （工具 %d 个）"
          % (scheme, args.host, args.port, len(registry().describe())))
    print("健康检查：%s://%s:%d/healthz" % (scheme, args.host, args.port))
    if args.host in ("127.0.0.1", "localhost"):
        print("提示：当前只监听本机。给平台调用时用 --host 0.0.0.0，并确认安全组已放行端口。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
