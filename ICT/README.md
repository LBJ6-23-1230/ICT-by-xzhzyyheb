# ICT 参赛工作区

本目录集中放**华为 ICT 大赛相关的过程材料与证据**，与工程源码分开，避免赛事文件混进源码或被打进后端交付包。

## 目录约定

| 目录 | 放什么 | 谁维护 |
|---|---|---|
| `evidence/` | 可核验的运行证据：Trace 导出、评测报告、自检输出、断链测试日志 | 全员，按里程碑命名 |
| `materials/` | 参赛材料：作品说明、架构图、资产清单、答辩稿、视频 | A（材料主笔） |

## 命名规则

- 证据文件带**阶段号 + 内容**，不带日期：`s2-gateway-selftest.txt`、`s4-trace-controller.md`
- 同一阶段的多次结果用序号区分：`s6-eval-baseline.md`、`s6-eval-after-prompt.md`
- 涉及凭据的内容一律脱敏（Token 只留长度、账号只留用户名）

## 和代码的关系

- 后端网关代码在 `server/zhixue-agent-server/app/agentarts/`
- 契约在 `contracts/openapi.json`
- 本目录**不参与** Python 打包与 App 构建，只做归档与提交

## 当前进度

| 阶段 | 状态 | 证据 |
|---|---|---|
| S0 基线冻结 | 待做 | — |
| S1 云资源与 IAM | 已完成（公测通过、基础版开通、IAM 授权） | `evidence/s3-platform-mcp-install.md` |
| S2 后端网关 | 已完成（54 项自检；运行时调用已打通，配置写入 `.env`） | `evidence/s2-gateway-selftest.txt`、`evidence/s3-platform-mcp-install.md` |
| S3 MCP 服务化 | 已完成（**12 个工具**，57 项端到端自检通过；平台侧已发现全部工具） | `evidence/s3-mcp-selftest.txt`、`evidence/s3-platform-mcp-install.md` |
| S4 平台资产 | **控制器 + 四条能力工作流全部在真实 API 上跑通**（诊断 / 规划 / 出题 / 评估，均真调 MCP） | `S4-平台资产搭建步骤.md`、`S4-控制器配置与验收.md`、`evidence/s4-wf-*.md` |
| S5 Web Demo + 上传 + 拍题 | 已跑通：三栏界面、用户上传资料后立即可检索、拍照识别（V1/V2 已验）；错题入库会真实下调掌握度 | `evidence/s5-demo-web.md`、`S5-图片错题诊断.md` |
