# 订阅通路：真实优化入口的接入记录

2026-09-17。**代码与接口验收；尚未运行自然反馈优化实验。** 新模块不在正在执行的 G1 v2 路径中，未修改其冻结源文件。

## 已接通的接口

`subscription_optimization.run_gepa_arm` 将原始 τ2 环境、官方评分、原生 reviewer、诊断抽样、行动时准入、共同隐私投影、真实 GEPA 引擎和订阅反思调用串联起来。模型调用共用同一 CLI 账本，策略文本是唯一可改组件。

- `subscription_evidence` 不只保存 call id：还核对实际 CLI 请求、输出、角色和用量，并从已接受模型文本重放上游 reviewer 的纯解析。宿主证据绑定任务、策略、episode、轨迹、诊断和准入；读取时复核源文件哈希。
- B1/B2 使用相同原生诊断抽样池。B2 只选择原文，不把 gate 理由或新证据卡送入 GEPA。空原生池的输入一致。
- 评分 unknown、基础设施故障、未知费用和原生 reviewer 的关键用户错误标记会停止运行。最后一项仍是待独立核查的模型判断，不是自动真值。
- `episode_privacy` 从实际可见消息提取实例字面量。最终渲染的反思 prompt 和返回文本也检查相同字面量。检查覆盖不完整、单词碰撞及无法识别改写等限制保留；不能据此宣称语义去泄漏已获保证。
- 整个下一批的 episode 数先验收，再开始执行。token 消耗不能预先精确知道，仍受 CLI 的软上限和逐调用停止控制；不保证整批最坏 token 预留。

## 真实引擎接入时发现并修正的区别

固定 GEPA 的 `write_agent_state=True` 会使验证集请求 `capture_traces=True`（`core/engine.py` 的 seed 与新候选验证分支）。在本包装层里，该标记意味着还生成 reviewer/gate 记录。因此新入口设为 **False**；原始验证轨迹仍由宿主 runner 保存，验证集不产生反思诊断。这与旧示例入口的记录设置不同，未来实验 manifest 必须记录最终选项。

父、子训练 minibatch 都由固定 GEPA 以 `capture_traces=True` 评价。因此当前实现为两者均调用 reviewer，B2 对选中池另调 gate，即使子候选随后没有被反思。**这些费用必须计入**，不能只按一次反思所见的父 batch 估算。后续若改为延迟生成诊断，需另外冻结版本。

固定 proposer 会吞掉某些反思/记录异常，`raise_on_exception=True` 并不覆盖所有内部异常。新入口保存 adapter 和 reflection 的失败状态；即使 GEPA 正常返回，只要出现上述失败，实验仍写为 stopped，且不输出可供晋级的 best candidate。反思最多一次实际 transport 调用，内部 fallback 不能再消耗一次模型请求。

`gepa_milestones` 另用固定版本的官方回调检查真实阶段顺序。返回前必须区分预算止于初始评价、满分跳过、候选拒绝、候选通过但 S0 留任和新策略胜出；缺失提案结束、未知空结果或记录错误都不能写为完成。生成候选数来自提案事件，不用最终候选池反推。`MaxCandidateProposalsStopper(1)` 在此版本实际限制一次迭代，满分跳过也消耗该机会；已执行的父 batch reviewer/gate 费用仍保留。

## 验收与证据范围

- `tests/test_subscription_evidence.py`：8 个 fake ledger 用例，通过实际上游 reviewer/parser 和准入投影检查绑定、交换、篡改及用量状态。首轮 fixture 缺少 EvaluationCriteria 导致失败，修正 fixture 后通过；两份日志保留。
- `tests/test_subscription_optimization.py`：截至 V4 日志为 8 个用例，包含真实固定 GEPA 引擎的一次 proposal、4 次 **fake episode**、原生 review/gate 接口、反思抽取和候选接受；不调用真实模型。首次端到端用例揭示上述验证 capture 设置问题，修复后通过。
- 测试日志中的 `0 → 1` 是 fixture 预设的分数，用于检查调用顺序。**它不属于研究数据，也不表示 Agent 能力提升。**
- 独立红队另有 `SUBSCRIPTION_OPTIMIZATION_REVIEW.md` 与对应测试，快照和统计以各自记录为准；之后的入口变化须另行验收，不能沿用旧文件哈希。

首次真实 G3 仍需满足或明确另行登记其前置条件，冻结任务分组、S0、全部角色预算、源文件和停止规则。G1 可行、代码通过和自然收益分别是三件需要独立证据的事。
