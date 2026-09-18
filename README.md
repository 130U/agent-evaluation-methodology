# Agent Evaluation Methodology | 基于 τ-bench 的 Agent 评测有效性与反馈优化研究

### ICFA：面向智能体反思优化的干预约束反馈准入

**项目展示周期：2026.05 - 2026.08**  
[github.com/130U/agent-evaluation-methodology](https://github.com/130U/agent-evaluation-methodology) | 研究范围：τ-bench / GEPA / ALE

- **研究基础：**梳理 τ-bench 交互评测与 GEPA 提示词优化，选定零售任务，研究评测反馈如何转化为可验证的策略改进依据。
- **问题发现：**源码审计与组件测试发现，自然语言评分分项在评价结果缺失或漏项时仍可能通过，暴露了评分结果与评价完整性之间的脱节。
- **方法设计：**提出先核验证据、再区分错误来源、最后修改策略并重跑验收的反馈机制，并实现完整性检查，明确反馈进入策略更新的条件。
- **阶段结论：**受控样例中，新增检查将缺失、漏项和重复评价标为待核验，不直接判为通过，为检验策略改进真实收益提供可复现实验基础。

## 从这里开始

| 阅读目的 | 入口 |
|---|---|
| 理解问题、方法和理论依据 | [完整文字报告](core/04-icfa-report/README.md) |
| 查看与项目卡一致的摘要 | [项目概览](projects/tau-bench/README.md) |
| 追溯实验设计与结果 | [封存研究报告](core/03-tau-feedback-evidence/RESEARCH_REPORT.md) · [实验材料索引](core/03-tau-feedback-evidence/README.md) |
| 检查代码与复现条件 | [公开复现包](supporting-evidence/tau-feedback-evidence/README.md) · [复现边界](supporting-evidence/tau-feedback-evidence/PUBLIC_REPRODUCTION_BOUNDARY.md) |

## 研究主线

一个任务得分，无法同时回答三个问题：评价是否完整、错误应归于哪个组件、当前允许的修改能否改变该行为。研究以这三个问题为起点，将反馈进入提示词优化的条件写成可检查的规则。

ICFA（Intervention-Constrained Feedback Admission，干预约束反馈准入）的流程是：**核验证据 → 核对行为来源与可修改范围 → 提出候选修改 → 重跑验收。** 只有满足准入条件的反馈才用于提出修改，候选不达标则保留原策略。来源匹配是使用反馈的必要审查条件，不能单独保证修改有效。

## 结论与证据

| 观察 | 证据 | 可支持的结论 |
|---|---|---|
| 评分通过可能与评价完整性脱节 | [E1：评价契约受控实验](supporting-evidence/tau-feedback-evidence/results/OFFLINE_FINDINGS.md) | 对缺失、漏项及重复的断言结果增加完整性检查，避免将证据缺失视为通过 |
| 角色标签不能说明行为生成来源 | [E4：来源审计](core/03-tau-feedback-evidence/E4_ACTION_ORIGIN_REVIEW.md) | 在固定初始化配置下，提示词更新不能直接改写框架预置开场 |
| 来源核对可以补充语义审查 | [C1：合成反馈校准](core/03-tau-feedback-evidence/C1_SYNTHETIC_RESULT.md) | 在固定案例中，来源约束排除了一条语义审查接纳、但超出本轮干预范围的建议 |
| 官方任务通过与完整服务质量存在覆盖差异 | [G3：真实候选验证](core/03-tau-feedback-evidence/G3_PILOT_RESULT.md) | 业务结果与回答依据需要分别验收；候选最终未被采用 |

现有结果支持评价完整性检查和反馈准入的机制结论；不将组件检查、合成校准或未采用的候选表述为已证明的任务成功率提升。各实验的终点与适用边界见[完整报告](core/04-icfa-report/README.md)。

## 项目说明

- **研究对象：**标题沿用 τ-bench；实际实验使用固定版本的 τ2-bench 文本零售环境和 GEPA。ALE 保留为相关基准背景，本次不报告 ALE 实验结果；旧 ALE 独立专题已从当前版本移除。
- **时间口径：**2026.05 - 2026.08 为作者提供的项目展示周期。本仓库所附实验与封存记录为 2026.09.15–2026.09.17，文字报告整理于 2026.09.18；不将这些记录追溯为 8 月前已完成的实验。
- **方法命名：**ICFA 为 2026.09.18 对已有实现和发现的理论整理名称，不作为此前实验的预登记名称。
- **材料版本：**2026.09.17 封存的 202 份公开材料按原字节保留；当前标题、项目卡与文字报告单独维护。封存导出清单中的 `not_published` 等状态描述导出时点，当前发布范围见[发布清单](supporting-evidence/UPLOAD_MANIFEST.md)。

[仓库结构](docs/REPOSITORY_ARCHITECTURE.md) · [公开边界](docs/PUBLICATION_AND_PRIVACY_BOUNDARY.md) · [变更记录](docs/repository/CHANGELOG.md)
