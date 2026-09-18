# Agent Evaluation Methodology

## 基于 τ-bench 的 Agent 评测有效性与反馈优化研究

**2026.05 — 2026.09**

本项目于 2026 年 5 月启动，2026 年 9 月完成。围绕“评测反馈如何成为可靠的优化依据”，提出 **ICFA（Intervention-Constrained Feedback Admission）**，即干预约束反馈准入机制，将评价完整性、行为来源与策略更新纳入同一研究框架。

[完整研究报告](core/04-icfa-report/README.md) · [实验与发现](core/03-tau-feedback-evidence/RESEARCH_REPORT.md) · [代码与复现](supporting-evidence/tau-feedback-evidence/README.md)

## 研究概览

**研究基础**：梳理 τ-bench 交互评测与 GEPA 提示词优化，选定 τ2-bench 文本零售任务，研究评测反馈如何转化为可验证的策略改进依据。

**问题发现**：源码审计与组件测试发现，自然语言评分分项在评价结果缺失或漏项时仍可能通过；行为来源审计进一步发现，框架预置消息可能被误归责于 Agent 策略。

**方法设计**：建立“证据核验—来源归责—策略修改—回归验收”的反馈流程，使每条优化建议与其证据、责任组件及可修改范围相对应。

**研究结论**：受控样例中，完整性检查将缺失、漏项和重复断言结果识别为待核验；来源约束进一步排除超出提示词干预范围的反馈，明确了评价可信度与反馈可用性的不同条件。

## 核心观点

**反馈的价值，取决于它能否为具体修改提供可信依据。**

ICFA 将反馈使用拆成三个可检验的问题：诊断是否有证据、修改对象是否匹配、更新后是否通过验收。对应的优化流程为：

1. **核验证据**：检查评价是否完整，诊断是否符合行动当时的信息。
2. **识别来源**：区分模型生成、框架预置与来源不明的行为，明确本轮可修改的对象。
3. **提出修改**：将符合条件的反馈用于生成候选策略。
4. **重跑验收**：分别检查业务结果、回答依据与原有能力，决定是否采用更新。

## 研究启发

| 研究来源 | 对本项目的启发 |
|---|---|
| [ALE · Agents’ Last Exam](https://arxiv.org/abs/2606.05405v2) | 将评测放回真实工作流与可验证结果中，促使本项目关注“一个分数究竟证明了什么”，把评测有效性作为研究起点。 |
| [τ2-bench](https://arxiv.org/abs/2506.07982) | 通过交互环境与细粒度分析区分错误来源，为本项目提供文本零售任务、业务政策和可追踪的执行过程。 |
| [GEPA](https://arxiv.org/abs/2507.19457) | 从执行轨迹中反思并生成提示词修改，启发本项目进一步研究“哪些反馈有资格进入优化”，并沿用其候选生成与测试机制。 |

三条研究思路汇合于本项目的核心问题：**让评测结果有证据，让优化反馈有归属，让策略更新有验收。**

## 研究材料

| 内容 | 入口 |
|---|---|
| 方法、理论与研究贡献 | [ICFA 完整报告](core/04-icfa-report/README.md) |
| 评价完整性实验 | [E1 受控结果](supporting-evidence/tau-feedback-evidence/results/OFFLINE_FINDINGS.md) |
| 行为来源与归责分析 | [E4 来源审计](core/03-tau-feedback-evidence/E4_ACTION_ORIGIN_REVIEW.md) |
| 反馈准入与候选验证 | [C1 校准](core/03-tau-feedback-evidence/C1_SYNTHETIC_RESULT.md) · [G3 试点](core/03-tau-feedback-evidence/G3_PILOT_RESULT.md) |
| 实现、测试与复现说明 | [公开研究代码](supporting-evidence/tau-feedback-evidence/README.md) |
