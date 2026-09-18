# Agent Evaluation Methodology | 基于 τ-bench 的 Agent 评测有效性与反馈优化研究

### ICFA：面向智能体反思优化的干预约束反馈准入

**2026.05 - 2026.08**  
[github.com/130U/agent-evaluation-methodology](https://github.com/130U/agent-evaluation-methodology) | 研究范围：τ-bench / GEPA / ALE

- **研究基础：**梳理 τ-bench 交互评测与 GEPA 提示词优化，选定零售任务，研究评测反馈如何转化为可验证的策略改进依据。
- **问题发现：**源码审计与组件测试发现，自然语言评分分项在评价结果缺失或漏项时仍可能通过，暴露了评分结果与评价完整性之间的脱节。
- **方法设计：**提出先核验证据、再区分错误来源、最后修改策略并重跑验收的反馈机制，并实现完整性检查，明确反馈进入策略更新的条件。
- **阶段结论：**受控样例中，新增检查将缺失、漏项和重复评价标为待核验，不直接判为通过，为检验策略改进真实收益提供可复现实验基础。

## 阅读与核验

- [完整文字报告：问题、机制、理论与边界](../../core/04-icfa-report/README.md)
- [封存实验报告](../../core/03-tau-feedback-evidence/RESEARCH_REPORT.md)
- [公开代码与复现材料](../../supporting-evidence/tau-feedback-evidence/README.md)

“重复评价”指重复的断言评价结果；“待核验”对应 `unknown`，表示现有证据不足以判断通过或失败。实际实验对象为固定版 τ2-bench 文本零售；ALE 仅为相关基准背景。

项目展示周期由作者提供。本次公开的实验与封存记录日期为 2026.09.15–17，报告与 ICFA 命名整理于 2026.09.18。展示周期不替代实验时间戳。旧项目卡保留在封存材料中，当前展示以本页为准。
