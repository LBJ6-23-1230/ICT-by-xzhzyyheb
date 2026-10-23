# 知学 Mate · 华为 ICT 大赛创新赛道改造仓库

当前目标赛题：**基于华为云 AgentArts 智能体开发平台的 Agent 设计和应用**。
旧赛事专用的规程、模板、答辩材料、视频稿和提交脚本已从当前工作区清理；项目现按
AgentArts 多智能体学习辅助应用的目标继续开发。

> 当前阶段是改造起步：方案与任务拆解已完成，AgentArts 云端资源、MCP、工作流、评测与
> 运行时部署尚未完成。在取得真实平台证据前，不宣称项目已经基于 AgentArts 上线。

## 开发入口

| 内容 | 路径 |
|---|---|
| 华为 ICT 改造方案与任务清单（当前真源） | `06-华为ICT大赛改造/` |
| HarmonyOS / ArkTS 前端（DevEco打开此目录） | `app/` |
| Python统一后端 | `server/zhixue-agent-server/` |
| 接口契约真源 / 前端镜像 | `contracts/` / `app/contracts/` |
| 联调与工具脚本 | `integration/` / `tools/` |
| 演示样例 | `演示测试数据/` |
| 可复用技术记录与自动化证据 | `项目文档/` / `evidence/` |
| 兼容验证用已签名安装包 | `hap/`（不作为 ICT 提交材料） |

根目录只保留一套开发源码；旧赛事提交目录和旧交付脚本已清理。

## 后端启动

```powershell
cd server/zhixue-agent-server
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# 按需在本机 .env 填写模型Key；不配置时会明确以规则回退。
.venv/Scripts/python.exe run.py
```

默认端口5000；模拟器地址 `http://10.0.2.2:5000`，真机通过同一网络访问开发机地址。
接口环境可在应用的「我的→关于→连续点击版本号5次」入口中配置。

## 前端与签名

使用DevEco Studio打开 `app/`，同步依赖后配置本机签名。
公开源码保留SDK、包名和工程配置，移除了本机签名配置与密码；不会影响原开发工作区的签名。
已有开发机复用环境见 [本机签名配置说明](本机签名配置说明.md)。

## 华为 ICT 改造入口

- [总体改造方案](06-华为ICT大赛改造/华为ICT-AgentArts改造方案-20261023.md)
- [可执行修改任务清单](06-华为ICT大赛改造/华为ICT-AgentArts修改任务清单-20261023.md)

推荐采用“AgentArts 低代码多 Agent + 自定义 MCP + 现有 HarmonyOS Demo”的混合架构。
AgentArts 负责控制器、工作流、知识库、Trace、评测和运行时；当前 Python 后端的排程、
判分、画像与计划提交能力逐步改造成 MCP 工具，避免重复实现已经过测试的业务规则。

## 迁移前技术基线

`evidence/` 保存迁移前的自动化测试、构建和签名验证结果，用于判断 AgentArts 改造是否
引入回归。这些记录不是华为 ICT 赛题已完成的证明；新平台证据将在改造过程中单独生成。

默认关闭演示重置和全量实验快照，仅受控本机调试可显式设置 `ZHIXUE_ENABLE_DEV_TOOLS=1`。公开试用环境保持关闭。

在后端目录运行：

```powershell
.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider
```

在仓库根运行：

```powershell
python tools/diff_contracts.py
```

ICT 交付包将在 AgentArts 主链路完成后使用新的资产清单和打包脚本生成。
