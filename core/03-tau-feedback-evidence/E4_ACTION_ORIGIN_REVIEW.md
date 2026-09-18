# E4 执行后来源与干预范围复核

**结论：记录支持限定范围内的来源判断，未发现实质阻断。** 在固定 τ2 版本、新会话、非 solo 的 retail 设置中，起始问候由框架插入；本次仅追加 Agent `system_prompt` 的三种配置没有改变它。这是 **N1 揭盲后的受控确认**，不是重新解释原 N1 标签，也不是 Agent 或 GEPA 改进证据。

复核者 `related_work` 编写过 E4 脚本。本次在另一代理完成执行后，重新核对源码、哈希与产物，**不是完全独立审查或盲审**。未重复构造 orchestrator、初始化、运行 episode 或调用模型；原 E4、N1 和 G2 文件均未修改。

## 已核验结果

| 检查 | 结果 |
|---|---:|
| PLAN 与其摘要哈希一致；绑定源文件 SHA256 | 434/434 |
| 固定官方运行时文件 Git blob SHA1 | 271/271 |
| 原 STOP 独立审查所绑定证据 SHA256 | 151/151 |
| 原 24 单元顺序中全部完成轨迹 | 18/18，覆盖 12 题 |
| assistant 消息逐条绑定 | 181/181 |
| 框架插入起始消息 | 18 |
| 模型生成消息，匹配唯一 agent 调用及原 client final | 163/163 |
| 原调用账本逐行核验 | 248：163 agent、81 user、4 NL judge |
| 初始化记录、精确 prompt 哈希重建及 seed=43 | 36/36 |
| 三种 prompt 不同、去除 timestamp 后起始轨迹相同 | 12/12 题 |
| E4 生成尝试、backend 调用记录、run/step 尝试 | 均为 0 |

18 条框架来源判断同时满足：任务初始 history 为空；绑定源码为非 solo 初始化分支；消息为 turn 0、无生成元数据且 cost 为 0；原第一条调用属于 user simulator，其输入确实接收到该问候。163 条模型消息逐一比较了原文、工具名称、参数、生成工具调用 ID、请求方和唯一 CLI 调用身份；其中 100 条是工具调用消息。上述来源计数没有缺失或 `unknown`，也没有把未知默认归为模型。

36 次初始化没有原调用账本，通用来源分类器因此全部保留 `unknown`。它们另有实际初始化执行记录、固定源码分支、零生成保护和常量匹配作为来源证据；不能为了得到 `framework_seed` 标签而补造调用记录。复核重新构造了 policy 与官方模板对应的 prompt 文本并比对全部哈希；状态中确实收到 prompt 的依据是原执行脚本的逐字断言及其保存记录，未再次观察运行时对象。

## 源码与证据定位

- 官方 τ2 固定提交：`2174a603f6d014ef94473ffa95957f6ce27100db`。[初始消息常量](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/orchestrator/orchestrator.py#L47)、[fresh/non-solo 初始化分支](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/orchestrator/orchestrator.py#L630)、[Agent 初始系统消息](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/agent/llm_agent.py#L79)。本轮核查的是本地哈希固定源码，没有重新访问远端。
- E4 PLAN SHA256：`95455f6826aaacc7bfd4ae0904789b1651c997bee40707d06d205607df698702`。
- 逐项复核、源/产物/原始 client final 哈希与 36 行矩阵：`research/E4_ACTION_ORIGIN_REVIEW.json`。原始 E4 产物共 59 个文件，本轮仅只读计算哈希。
- 公开白名单摘要：`results/E4_PUBLIC_SUMMARY.json`，SHA256 `b097657515ba6d7bd9a00b8742ab400803c8bba29502bde2089c2fc162f7cbcc`。仅含计数、初始化矩阵、策略文本、来源解释与哈希；不含原调用、原始对话、CLI 身份、文件路径或实例个人值。

## 解释边界

**18 个 framework seed 不等于 18 次 reviewer 误诊。** E4 没有逐条重审全部诊断，也没有测量错误反馈率、准入准确率、策略收益或泛化。`model_agent` 只表示来源与策略控制范围，不能证明针对该动作的诊断正确；来源门也尚未证明会改善优化结果。

样本为原计划 24 单元中的全部 18 个完成者；另 1 个中断未评分、5 个未运行，继续列入原分母。12 题中 6 题重复两次、6 题一次，存在重复相关和按执行顺序中断的选择偏差。本次初始化使用固定 seed 43、3 种策略与 system-prompt-only 干预；不涉及改写 harness、solo 模式或有预置历史的场景。哈希及调用绑定用于检测本地记录错配，不认证潜在恶意记录生产者。

可用于公开结论的措辞：**“在揭盲后的来源核验中，我们确认了一个策略干预范围盲点：框架插入的起始消息会出现在 assistant 轨迹中，但不由 Agent 策略生成。12 个既用开发任务的 36 次零模型调用初始化验证了这一边界；其对反馈质量及优化收益的影响仍需另行实验。”**
