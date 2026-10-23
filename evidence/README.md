# 自动化联调证据

本目录由 `integration/run_liantiao5.py` 自动写入，用于保存可复核的测试结果，
不是运行缓存，不应随日常工作区清理一起删除。

文件命名：

- `integration_YYYY-MM-DD_HHMMSS.json`：逐项接口检查明细。
- `run_summary_YYYY-MM-DD_HHMMSS.json`：同一轮联调的汇总结果与运行模式。
- `phase9-final-*`：第九批最终测试、构建、签名校验与汇总记录。
- `phase9-retention-dryrun-*`：数据保留策略的只读演练结果。

归档规则：

1. 同一时间戳的明细与汇总必须成对保留。
2. ICT 改造期间至少保留最近一次完整通过的基线证据。
3. 新的 ICT 交付脚本只应选择明确列入资产清单的证据，不能整目录复制历史记录。
4. 临时截图、布局转储和测试数据库放在 `.pytest-tmp/`，不要混入本目录。
