# C1：固定合成诊断的准入校准

2026-09-17。正式状态以`experiments/C1_SYNTHETIC_MANIFEST.json`为准；先登记后执行，独立于G4自然反馈研究。

## 问题和参照

N1的自然反馈审查没有充分支持的正例，因此不能从N1估计有效反馈保留率。C1通过12张预先编排的合成卡（6对情境）测量语义准入与来源资格的处理，补齐组件观察，不替代自然发生率、天然反馈保留率、GEPA候选收益或泛化实验。

客户、消息、工具响应、诊断和来源账本均由AI研究代理编排，没有真实环境执行和原生reviewer。来源分类里的model_agent是fixture情境类型，不能说实际模型产生过这些动作。官方政策与工具schema来自固定本地τ2提交，真实gate仅读取诊断、争议行动、之前的可见历史、政策和工具；未来/隐藏的宿主注释和作者标签不发送。

作者标签先落盘，红队只看匿名`C1_BLIND_PACKET.json`独立标注。固定hash乱序及原卡映射另存，12卡不替换。两者语义标签相同才进入supported/unsupported/insufficient确定参照层；不同则记disputed，保留两份原标签，根代理不为匹配gate选边。决定、来源和可修改范围分开比较。作者和复核者均为AI研究代理，不能称人类金标准或天然代表性样本。红队先前参与此项目，可能知道研究动机，匿名不保证完全盲法。

## 执行

按`C1_BLIND_MAP.json`冻结顺序运行全部12卡，每卡至多一次semantic judge调用；即使来源为framework也作为semantic-only诊断探针执行，之后独立报告来源路由。不能把来源提前挡住的卡记成语义判断正确。

复用现有`audit_native_diagnosis`只是接口调用：外层stage为synthetic_admission_calibration，native_reviewer_calls=0、native_call_id=null、environment_episodes=0，不构造原生review sidecar，不接入自然GEPA feedback registry。judge的实际CLI role保持feedback_admission。空/非法view导致的结构弃权单列，无模型调用时不伪造judge结果。

模型`gpt-5.6-sol/low`，使用已预检的新恢复HTTP客户端；12次调用阈值、350,000输入token、12,000输出token、1,200 client墙钟秒，每请求120秒；CLI token为软停止阈值，单请求可能超限。失败、unknown usage和超时即停止该校准，不重试、不替换。正常semantic abstain是结果，不是传输错误。

G4与C1是两个独立固定实验，共用账户额度但分别记CLI账本；不能按账户百分比推算可跑满，也不调用额度重置。C1不改G4已封存源码、提示、门槛或结果。允许C1与G4共享父代阶段并行，记录实际重叠；这可能改变服务等待时间，因此不把墙钟差异解释为机制效率的因果效果。C1未完成前不另安排与候选两臂不同程度重叠的人工调度。

## 预定输出

逐卡保留作者/独立标签、合成来源、语义decision、来源路由、复合准入及全部实际费用。对于agreement层：

- supported且来源可修改：接受、拒收、弃权数/完整预分配正例数。
- unsupported：错误接受、拒收、弃权数/预分配负例数。
- insufficient：接受、拒收、弃权，不能并入已证明误诊。
- disputed：逐条结果，不能合并进确定正负分母或按gate结果再裁。
- framework/unknown来源：路由结果另表；合成来源契约可被识别不等于已认证真实生产者。

若中断，全部12卡仍列出完整、错误/中断、未运行；没有verdict不能按拒收成功计算。报告CLI已知token与时间，无法观察的供应商内部请求/重连成本另标不可得，不推算美元。

样本为目的性合成case series，不做准确率总体推断、显著性或自然误诊率估计，不将5个正例通过等同实际Agent任务能力改善。无论表现如何，不改prompt、schema、truth或预算追回预期答案。

## 封存与复核

登记绑定原卡、匿名包/映射、两方标签、量表、脚本、准入/投影/来源代码和传输代码hash。保留每卡原始CLI events/final/usage及gate sidecar。执行结束或停止即封存整个结果文件集合；后续只读汇总注明实现时间，不回写原始结果。
