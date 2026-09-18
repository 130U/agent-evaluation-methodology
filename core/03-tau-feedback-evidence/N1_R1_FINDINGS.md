# N1-R1：反馈诊断审计公开派生摘要

**完整执行不等于机制有效。** 18 条已完成轨迹产生 8 条原生诊断；gate 给出 5 次拒收、2 次弃权、1 次接受。独立研究代理标签中，0 条在事实支持、Agent 责任和修正可观察性三个维度均获支持。本阶段没有修改 Agent 策略，也没有测得下游收益。

## 分母与成本

原 G1 计划仍为 24 条：18 条已评价、1 条中断未评价、5 条未运行。N1-R1 审计其中全部 18 条，涉及 12 题（6 题各两次、6 题各一次）。18 次 review 中，6 条轨迹产生全部 8 条诊断；其余 12 个空池没有 gate 模型调用。18 个 gate 单元完成只包含 8 次实际 gate 调用。

| 阶段 | CLI 调用 | 输入 tokens | 输出 tokens | 未知用量调用 |
|---|---:|---:|---:|---:|
| review | 18 | 284,840 | 6,894 | 0 |
| gate | 8 | 130,049 | 4,315 | 0 |
| 合计 | 26 | 414,889 | 11,209 | 0 |

用量来自 CLI 记录，包含提示开销；不是货币费用或底层供应商请求次数。旧 G1 生成、探针及研究代理工作另计。

## 标签、准入与解释边界

研究代理标签不是人工金标准，也未经过概率校准。标注者及裁决者有既往项目熟悉度，并非完全盲法。两份首轮标签原件未改；差异在 gate 前另行裁决并保留说明。最终标签锁定于 11:49:02 UTC，最早 gate 请求本地记录为 11:49:42 UTC；这不是第三方可信时间戳。

| 最终合并标签 | reject | abstain | accept |
|---|---:|---:|---:|
| unsupported | 2 | 0 | 0 |
| insufficient | 3 | 2 | 1 |
| supported | 0 | 0 | 0 |

合并规则为三维均 supported 才记 supported，任一 unsupported 则记 unsupported，其余为 insufficient；这是展示规则，不是 gate 决策规则。两张 unsupported 被拒收不等于总体准确率；没有全维度支持的诊断，也无法估计有效反馈的保留率或召回率。

卡 1/2 的逐单查找建议可执行，但原动作是否构成应归责的错误仍缺证据。gate 分别给弃权和拒收，两次均承认可执行性；不能把未准入解释为建议无用，更不能宣称已测得有用反馈损失。

## 揭盲后确认的来源问题

唯一 accept 是卡 3（task81 / replicate29 / turn0）的固定问候。原标签保持 insufficient。揭盲后，固定版本源码与本地原始调用链共同表明：该句由 orchestrator 的 DEFAULT_FIRST_AGENT_MESSAGE 直接插入，首个模型调用属于用户模拟器；首个 Agent 模型响应在 turn2，已经请求认证。gate 却声称该句由 actor 控制。

因此，可见的 assistant 消息不必然由可优化策略生成。仅修改 StrategyAgent.system_prompt 无法替换框架的固定开场。这是额外的 post-unblinding 来源分析，未回改标签或 gate；不是原创首次、普遍频率或策略增益主张。

[固定官方初始化源码](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/orchestrator/orchestrator.py#L626)

## 八条原诊断及结果

下列诊断与修正字段逐字保留，因此其中的指控是被审查的模型主张，不是本摘要认可的事实。完整三维首轮标签、裁决说明和 gate 原理由见 [派生 JSON](../../supporting-evidence/tau-feedback-evidence/results/N1_R1_PUBLIC_SUMMARY.json)。

### card-0001 · task85 / replicate29 / turn8

最终三维标签（事实 / 责任 / 修正）：**insufficient / insufficient / supported**；gate：**abstain**。

动作短引：`Which order contains the Fleece Jacket?`

**原诊断 reasoning：**

After authentication, the agent had access to the user's five order IDs and could inspect the orders to determine which one contained the Fleece Jacket. Instead, it asked the user to identify the order even though the scenario did not provide the user with that information. This prevented the exchange workflow from continuing and directly led to the out-of-scope termination.

**原修正 correct_behavior：**

The agent should have queried the user's orders one at a time, identified the order containing the Fleece Jacket, checked whether it was pending or delivered, and then continued the appropriate modification or exchange workflow, collecting all required details and confirmation before updating the order.

### card-0002 · task85 / replicate17 / turn8

最终三维标签（事实 / 责任 / 修正）：**insufficient / insufficient / supported**；gate：**reject**。

动作短引：`Which order ID contains the Fleece Jacket you want to exchange?`

**原诊断 reasoning：**

After authenticating Yusuf and retrieving his profile, the agent had access to the list of his order IDs and could help identify which order contained the Fleece Jacket. Instead, it required the user to supply an order ID that was not provided in the User Instructions. This prevented the simulator from continuing and directly led to the unsuccessful termination.

**原修正 correct_behavior：**

The agent should have inspected Yusuf's listed orders one at a time using the available order-information tool(s), identified the order containing the Fleece Jacket, checked its status and item details, and then continued gathering the exchange and payment details required by policy.

### card-0003 · task81 / replicate29 / turn0

最终三维标签（事实 / 责任 / 修正）：**insufficient / insufficient / supported**；gate：**accept**。

动作短引：`Hi! How can I help you today?`

**原诊断 reasoning：**

The policy requires authentication at the beginning of the conversation, but the agent first asked how it could help rather than initiating authentication. Authentication was completed shortly afterward, so this did not itself determine the outcome.

**原修正 correct_behavior：**

Begin by requesting the account email, or the customer's name and ZIP code, before handling the request.

### card-0004 · task81 / replicate29 / turn6

最终三维标签（事实 / 责任 / 修正）：**insufficient / insufficient / insufficient**；gate：**reject**。

动作短引：`Please provide the order ID, including the leading # symbol, and identify the items you no longer need.`

**原诊断 reasoning：**

After authenticating James, the agent asked him for an order ID that was not supplied in the User Instructions. The policy explicitly allows the agent to provide information about the authenticated user's orders and help look up an order ID. It should have retrieved James's orders and identified the pending orders containing the named items. Requiring unavailable information left the simulator unable to continue and prevented the expected cancellations.

**原修正 correct_behavior：**

Use the authenticated user ID to look up the customer's orders, inspect their statuses and items, identify the relevant pending orders, and then collect the permitted cancellation reason and explicit confirmation before cancelling each applicable order.

### card-0005 · task59 / replicate17 / turn10

最终三维标签（事实 / 责任 / 修正）：**insufficient / insufficient / supported**；gate：**reject**。

动作短引：`get_user_details`

**原诊断 reasoning：**

The user had already been authenticated, both orders had been confirmed to belong to the authenticated user, and the order records contained the payment information needed for the requested tasks. Retrieving the full user profile was unnecessary and did not advance the cancellation or address change.

**原修正 correct_behavior：**

After retrieving both order records, the agent should have proceeded using those results without calling get_user_details.

### card-0006 · task59 / replicate17 / turn12

最终三维标签（事实 / 责任 / 修正）：**insufficient / insufficient / insufficient**；gate：**abstain**。

动作短引：`Please tell me which order is the older one and choose its cancellation reason:`

**原诊断 reasoning：**

The agent established that the available records could not identify the older order, explain the status difference, or support the requested five-day processing guarantee. The customer’s conditional cancellation therefore could not be completed without information unavailable to the agent. Under the policy, a request that cannot be handled within the agent’s capabilities must be transferred to a human. Instead, the agent asked the user to supply which order was older, information the user’s instructions did not provide, causing the interaction to end out of scope before either requested database update was completed.

**原修正 correct_behavior：**

The agent should have transferred the customer by first calling transfer_to_human_agents and then sending exactly 'YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON.' A human could resolve the unavailable order-age and processing information while preserving the address-change request.

### card-0007 · task76 / replicate29 / turn24

最终三维标签（事实 / 责任 / 修正）：**unsupported / insufficient / insufficient**；gate：**reject**。

动作短引：`for the reason “no longer needed,”`

**原诊断 reasoning：**

The agent proposed cancelling order #W8367380 for ‘no longer needed,’ even though the user’s stated reason for the fleece-jacket cancellation was that it was ordered by mistake. The user corrected the reason before the cancellation, so this did not affect the final outcome.

**原修正 correct_behavior：**

The agent should have requested confirmation to cancel #W8367380 for ‘ordered by mistake,’ while separately using ‘no longer needed’ only for the skateboard order if the requested variant was unavailable.

### card-0008 · task76 / replicate17 / turn22

最终三维标签（事实 / 责任 / 修正）：**unsupported / insufficient / insufficient**；gate：**reject**。

动作短引：`transfer_to_human_agents`

**原诊断 reasoning：**

The agent transferred the user even though the requests remained within the retail agent's supported scope. Removing an individual item was not permitted, but the user's instructed fallback was to cancel order #W8367380 for 'ordered by mistake'; likewise, the agent should have elicited the desired skateboard specifications, checked whether the requested variant was available, and, if modification was impossible, pursued cancellation of #W1242543 for 'no longer needed.' The agent also calculated the grill total as $1,939.05 but never communicated it to the user. The unsupported transfer ended the interaction without obtaining confirmation or completing either cancellation and without satisfying the required price assertion.

**原修正 correct_behavior：**

The agent should have told the user that individual-item removal was unavailable and asked for explicit confirmation to cancel #W8367380 for 'ordered by mistake.' It should also have asked for the skateboard's desired specifications, checked the corresponding product variants, and either completed the modification after collecting all required details and confirmation or asked for confirmation to cancel #W1242543 for 'no longer needed' if the requested variant was unavailable. It should have directly told the user that all prior grill purchases totaled $1,939.05, and it should not have transferred the conversation.

## 公开范围与复核限制

这是本地封存材料的白名单派生摘要。它不包含完整 CLI 调用、事件或请求，完整对话、整段政策、账号/付款资料、凭据或绝对路径。原诊断中必要的公开基准案例姓名与订单标识保留；没有复制完整工具结果或 gate 引用对象。

**原始调用未在这里公开，仅给出哈希不足以独立认证调用真实性或完整复现实验。** 哈希只能在取得相应原始材料后用于比对。该局限也适用于固定开场案例中的本地调用记录；公开源码可独立检查初始化分支，不能单凭它证明一条未公开调用的实际执行。

本研究代理审计不建立因果净效应、泛化收益或旧 G1 继续门通过。18 条轨迹受既定顺序、可用性和中断影响；重复任务与同轨迹诊断不独立。导出器只准备本地文件，没有向 GitHub 发布。

### 来源承诺（SHA-256）

- N1_FINDINGS.json: `739a7fb7d16f990fabad0b8fbb7fbfce5f90aaa08b1c435d0eb60f7787968530`
- independent_result_review.json: `ddb437350eb8bc18b1e58539ff4882cfd79b57081863a816a45ffe87b17bcbc4`
- independent_result_review.md: `fc32a2ed1d5a3012e242057c9993860bde58dd13ed381cff9070be178367f004`
- locked_labels.json: `4b4dccc56f24f83ce41ad96aaabcd1f53b15954f3d2fee5401064e2f8a24208a`
- registered_manifest: `f9bb795341b1b02dbaa7a0e09ac4c0b377b5f17fd7330c50a09b29dfe937b166`
- first_labels_red_team.json: `0345a1c1f20e9db36d022c241a3669a6051163bb8fc84df94a4c4374bb3b3acb`
- first_labels_related_work.json: `4b76737c4ed7186d22848140b7243b9b30427d545eb2dd958b4e92f53ce040d2`
