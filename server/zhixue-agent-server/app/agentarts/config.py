# -*- coding: utf-8 -*-
"""AgentArts 网关配置：把"能不能上云"变成可判断的状态，而不是一堆空字符串。

设计要点
--------
1. **三态而非布尔**：`cloud` / `local` / `disabled`。布尔量无法区分"没配"和"故意走降级"，
   而这两者在演示时必须是两种界面表现。
2. **缺配置要说清缺哪个**：`missing()` 返回变量名，启动日志与 `/api/v2/agent/runtime`
   直接展示，避免"调用失败但不知道为什么"。
3. **密码不进入任何序列化结果**：`describe()` 是唯一对外描述入口，只暴露布尔与 ID。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

MODE_CLOUD = "cloud"
MODE_LOCAL = "local"
MODE_DISABLED = "disabled"

#: 两种调用形态：
#: * `run_agent` —— 低代码应用的 runAgent 接口（IAM Token + 路径里的 conversation_id）
#: * `runtime`  —— 部署到「智能体运行时」后的调用（工作负载身份 API Key + 会话头）
API_STYLE_RUN_AGENT = "run_agent"
API_STYLE_RUNTIME = "runtime"

DEFAULT_REGION = "cn-north-4"
DEFAULT_WORKSPACE = "default"
DEFAULT_INVOKE_MODE = "published"
DEFAULT_TIMEOUT = 120


def _env(environ, name: str, default: str = "") -> str:
    return str(environ.get(name) or default).strip()


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AgentArtsConfig:
    """一次进程生命周期内不变的静态配置。"""

    enabled: bool
    mode: str
    api_style: str
    region: str
    iam_user: str
    iam_domain: str
    iam_password: str
    project_id: str
    workspace_id: str
    agent_id: str
    agent_version: str
    invoke_mode: str
    endpoint: str
    timeout_seconds: int
    runtime_name: str = ""
    api_key: str = ""

    # ---------------------------------------------------------------- 构造
    @classmethod
    def from_env(cls, environ=None) -> "AgentArtsConfig":
        environ = environ if environ is not None else os.environ
        region = _env(environ, "AGENTARTS_REGION", DEFAULT_REGION)
        project_id = _env(environ, "AGENTARTS_PROJECT_ID")
        endpoint = _env(environ, "AGENTARTS_ENDPOINT") or (
            "%s.studio.agentarts.%s.huaweiapaas.com" % (project_id, region) if project_id else ""
        )

        enabled = _as_bool(_env(environ, "AGENTARTS_ENABLED", "0"))
        raw_mode = _env(environ, "AGENTARTS_MODE", MODE_CLOUD).lower()
        if not enabled:
            mode = MODE_DISABLED
        elif raw_mode == MODE_LOCAL:
            mode = MODE_LOCAL
        else:
            mode = MODE_CLOUD

        try:
            timeout = int(_env(environ, "AGENTARTS_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))
        except ValueError:
            timeout = DEFAULT_TIMEOUT

        return cls(
            enabled=enabled,
            mode=mode,
            api_style=(_env(environ, "AGENTARTS_API_STYLE", API_STYLE_RUN_AGENT).lower()
                       or API_STYLE_RUN_AGENT),
            region=region,
            iam_user=_env(environ, "AGENTARTS_IAM_USER"),
            iam_domain=_env(environ, "AGENTARTS_IAM_DOMAIN"),
            iam_password=_env(environ, "AGENTARTS_IAM_PASSWORD"),
            project_id=project_id,
            workspace_id=_env(environ, "AGENTARTS_WORKSPACE_ID", DEFAULT_WORKSPACE) or DEFAULT_WORKSPACE,
            agent_id=_env(environ, "AGENTARTS_AGENT_ID"),
            agent_version=_env(environ, "AGENTARTS_AGENT_VERSION"),
            invoke_mode=_env(environ, "AGENTARTS_INVOKE_MODE", DEFAULT_INVOKE_MODE) or DEFAULT_INVOKE_MODE,
            endpoint=endpoint,
            timeout_seconds=max(5, min(timeout, 600)),
            runtime_name=_env(environ, "AGENTARTS_RUNTIME_NAME"),
            api_key=_env(environ, "AGENTARTS_API_KEY"),
        )

    @property
    def is_runtime(self) -> bool:
        return self.api_style == API_STYLE_RUNTIME

    # ---------------------------------------------------------------- 判定
    def missing(self) -> list[str]:
        """返回缺失的配置项名称（按接入顺序排列）。"""
        if self.is_runtime:
            required = [
                ("AGENTARTS_ENDPOINT", self.endpoint),
                ("AGENTARTS_RUNTIME_NAME", self.runtime_name),
                ("AGENTARTS_API_KEY", self.api_key),
            ]
        else:
            required = [
                ("AGENTARTS_IAM_USER", self.iam_user),
                ("AGENTARTS_IAM_DOMAIN", self.iam_domain),
                ("AGENTARTS_IAM_PASSWORD", self.iam_password),
                ("AGENTARTS_PROJECT_ID", self.project_id),
                ("AGENTARTS_ENDPOINT", self.endpoint),
                ("AGENTARTS_AGENT_ID", self.agent_id),
            ]
        return [name for name, value in required if not value]

    def ready(self) -> bool:
        return self.mode == MODE_CLOUD and not self.missing()

    def blocking_reason(self) -> str:
        """给前端/日志的一句人话结论。"""
        if self.mode == MODE_DISABLED:
            return "AGENTARTS_ENABLED 未开启：当前走本地降级通道，未调用云端智能体"
        if self.mode == MODE_LOCAL:
            return "AGENTARTS_MODE=local：按配置强制走本地降级通道"
        missing = self.missing()
        if missing:
            return "缺少配置：%s" % "、".join(missing)
        return ""

    # ---------------------------------------------------------------- 地址
    def runtime_url(self, conversation_id: str) -> str:
        url = "https://%s/v1/%s/agents/%s/conversations/%s?workspace_id=%s" % (
            self.endpoint, self.project_id, self.agent_id, conversation_id, self.workspace_id,
        )
        if self.agent_version:
            url += "&version=%s" % self.agent_version
        return url

    def workflow_url(self, workflow_id: str, conversation_id: str) -> str:
        url = "https://%s/v1/%s/workflows/%s/conversations/%s?workspace_id=%s" % (
            self.endpoint, self.project_id, workflow_id, conversation_id, self.workspace_id,
        )
        if self.agent_version:
            url += "&version=%s" % self.agent_version
        return url

    def invocation_url(self) -> str:
        """智能体运行时的调用地址。

        官方形态：`https://{入站网关域名}/runtimes/{运行时名称}/invocations`。
        注意这里的 domain 与低代码 runAgent 的 `*.studio.agentarts.*` **不是同一个**，
        它是运行时列表里「访问域名」的值（`defaultgw-xxxx.<region>.huaweicloud-agentarts.com`）。
        """
        return "https://%s/runtimes/%s/invocations" % (self.endpoint, self.runtime_name)

    # ---------------------------------------------------------------- 描述
    def describe(self) -> dict:
        """对外可序列化视图：**不含密码，也不含完整 endpoint 之外的任何凭据**。"""
        return {
            "mode": self.mode,
            "ready": self.ready(),
            "apiStyle": self.api_style,
            "region": self.region,
            "workspaceId": self.workspace_id,
            "agentId": self.agent_id,
            "agentVersion": self.agent_version,
            "runtimeName": self.runtime_name,
            "invokeMode": self.invoke_mode,
            "hasEndpoint": bool(self.endpoint),
            "hasIamUser": bool(self.iam_user),
            "hasProjectId": bool(self.project_id),
            "hasApiKey": bool(self.api_key),
            "missing": self.missing(),
            "reason": self.blocking_reason(),
        }
