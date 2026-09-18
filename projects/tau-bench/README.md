# Agent Evaluation Methodology

## 基于 τ-bench 的 Agent 评测有效性与反馈优化研究

**2026.05 — 2026.09**

本项目于 2026 年 5 月启动，2026 年 9 月完成。提出 **ICFA（Intervention-Constrained Feedback Admission）**，即干预约束反馈准入机制。

**研究基础**：梳理 τ-bench 交互评测与 GEPA 提示词优化，选定 τ2-bench 文本零售任务，研究评测反馈如何转化为可验证的策略改进依据。

**问题发现**：源码审计与组件测试发现，自然语言评分分项在评价结果缺失或漏项时仍可能通过；行为来源审计进一步发现，框架预置消息可能被误归责于 Agent 策略。

**方法设计**：建立“证据核验—来源归责—策略修改—回归验收”的反馈流程，使每条优化建议与其证据、责任组件及可修改范围相对应。

**研究结论**：受控样例中，完整性检查将缺失、漏项和重复断言结果识别为待核验；来源约束进一步排除超出提示词干预范围的反馈，明确了评价可信度与反馈可用性的不同条件。

## 研究启发

[ALE（Agents’ Last Exam）](https://arxiv.org/abs/2606.05405v2) 对真实工作流与可验证结果的强调，构成本项目理解评测有效性的起点；[τ2-bench](https://arxiv.org/abs/2506.07982) 提供交互任务与错误分析环境；[GEPA](https://arxiv.org/abs/2507.19457) 将轨迹反思接入提示词更新，启发本项目研究反馈进入优化前的证据条件。

[完整研究报告](../../core/04-icfa-report/README.md) · [实验与发现](../../core/03-tau-feedback-evidence/RESEARCH_REPORT.md) · [代码与复现](../../supporting-evidence/tau-feedback-evidence/README.md)
