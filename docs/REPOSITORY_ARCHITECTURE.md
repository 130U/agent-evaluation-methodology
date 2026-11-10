# 仓库结构

本仓库研究 **Agent Evaluation Methodology | 基于 τ-bench 的 Agent 评测有效性与反馈优化研究**，项目周期为 2026 年 5 月至 9 月。

| 路径 | 用途 |
|---|---|
| [项目卡](../projects/tau-bench/README.md) | 对外摘要与阅读入口 |
| [ICFA 文字报告](../core/04-icfa-report/README.md) | 方法、理论与证据边界 |
| [封存研究报告区](../core/03-tau-feedback-evidence/README.md) | 公开导出的报告及实验终点 |
| [公开复现包](../supporting-evidence/tau-feedback-evidence/README.md) | 来源锁、代码、测试与脱敏结果 |
| `docs/`、`scripts/`、`.github/` | 研究发布契约、完整性清单与自动校验 |

2026-09-17 导出的 202 份研究文件属于当前结论的证据，按原内容封存。`projects/tau-feedback-evidence/` 是导出包内的项目记录；当前阅读入口为 `projects/tau-bench/`。编号 03、04 是稳定文档路径。

证据优先级：原始组件结果与固定版本 → 实验报告及边界 → 综合报告 → 项目摘要。项目展示周期与原始实验日期分别记录。

`python scripts/validate_repository.py` 检查结构、本地链接、凭据模式与全部封存文件的 SHA-256。Git 忽略本机环境、缓存和生成输出。
