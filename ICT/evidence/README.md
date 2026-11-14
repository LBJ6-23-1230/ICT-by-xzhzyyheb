# 证据归档

存放**可重复验证**的输出，而不是截图。每条证据要能回答三个问题：

1. 这份输出是什么命令/操作产生的？
2. 它证明了什么结论？
3. 换一台机器能不能复现？

## 当前已归档

| 文件 | 产生方式 | 证明了什么 |
|---|---|---|
| `s2-gateway-selftest.txt` | `python tools/verify_agentarts_s2.py` | 54 项检查：配置三态、7 类平台事件归一化、会话映射与运行回放、Token 缓存与 401 处理、14 个文件语法通过 |
| `s3-mcp-selftest.txt` | `python tools/verify_agentarts_s3.py` | 48 项检查：真起 HTTP 服务，验证 initialize/tools/list/tools/call、API Key 401、伪造 sessionRef 拒绝、判分只认服务端答案键、计划版本与幂等、重排与掌握度规则 |
| `s4-wf-diagnosis.md` + 同名目录截图 | AgentArts 工作流试运行 | `wf-diagnosis` 的 D1（正常：证据驱动薄弱点）与 D4（越权：伪造 sessionRef 被拒且不编造） |
| `s4-wf-planner.md` | 工作流试运行 + 工具级 curl | P1 端到端可跑；P2 证明超预算必延期（`unscheduledTasks` 带 `deferredReason`，预算三数自洽） |
| `s4-wf-exercise.md` | 工具级 curl | E1 按资料出题（`source=materials`，带 `chunkId`/`contentHash`）；E3 无资料拒绝出题；记录 JSON 缓存必须重启的坑 |
| `s4-wf-assessment.md` | 工具级 + 工作流级 | A1 满分判分（42→71）、A2 画像落库、A3 满分不重排、A4 低分重排（v1→v2）、A5 题号不存在拒绝判分、A6 端到端总结 |
| `s5-demo-web.md` + 同名目录截图 | Web Demo 实跑 | 三栏界面全部真实数据：云端徽标、`wf-planner` 路由、45 分钟排入/70 分钟延期、用户上传资料后立即可检索 |

## 待归档（按阶段）

- `s4-trace-*.md`：AgentArts 调用链导出（**基础版只留 15 天，必须当期归档**）
- `s4-controller.md`：多智能体控制器的路由证据（简单查询直达 / 复杂目标协作 / 等待输入）
- `s5-vision-ocr.md`：图片错题识别链路（V1–V3）
- `s6-eval-baseline.md` / `s6-eval-after-*.md`：评测基线与优化后对照
- `s7-deploy-*.md`：部署版本表与故障演练记录
