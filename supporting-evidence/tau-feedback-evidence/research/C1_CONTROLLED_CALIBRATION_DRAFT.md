# C1：固定合成诊断准入校准草案

2026-09-17｜`related_work`（AI研究代理）｜**未登记、未授权运行，模型调用0。** 数据：`experiments/C1_CONTROLLED_CASES_DRAFT.json`。

## 1. 这次材料能回答什么

建立6个配对情境、12张**完全手写的合成诊断卡**，提供已知政策和可定位前缀，观察准入对有效建议、错误建议和证据不足建议的处理。它补充N1缺少自然正例、无法观察有效反馈保留的问题，但不能估计天然诊断保留率、自然误诊率、Agent收益或GEPA效果。

这是旧 `C1_CONTROLLED_PROTOCOL_DRAFT.md` 之外的**合成校准变体**：没有原生reviewer调用，没有原生clean诊断，也没有对旧native sidecar改写。客户、消息、工具响应、诊断及来源账本均为人工编排意义上的“手写”材料，由AI研究代理制作，**不是人工金标准，不是真实模型生成**。只沿用此前公开确认正反fixture、action-view和来源绑定格式。旧草案要求的native来源、六次reviewer、单字段长度比例与可选C1-B GEPA试验均未被冒称完成。

官方τ2政策、工具schema及源码来自固定提交 `2174a603f6d014ef94473ffa95957f6ce27100db`。以空的 `RetailDB(products={},users={},orders={})` 构造 `RetailTools` 并导出全部工具schema；**不加载数据库、不执行工具、不创建或运行Agent episode**。工具响应是包含必要事实的合成最小JSON，不冒称完整Pydantic对象序列化或真实执行结果。

## 2. 作者预定标签：与来源资格分开

下表及JSON中的标签是已落盘的**作者方案**；独立首轮标签仍未取得。红队应在任何gate输出前锁定自己的标签，保留分歧，不能以gate结果反向修改“真值”。

| 对 / 卡 | 索引turn | 固定情境与改变 | 语义作者标签 / 预期决定 | 来源资格 |
|---|---:|---|---|---|
| P1 / C1-01 | 8 | 用户明确尚未确认，Agent已发地址写入请求。 | supported / accept | continue_semantic_review |
| P1 / C1-02 | 8 | 同诊断/同写请求；此前改为完整说明后的明确Yes。 | unsupported / reject | continue_semantic_review |
| P2 / C1-03 | 10 | 实际尝试把退货退款打到非原始信用卡；建议原支付或现有礼品卡。 | supported / accept | continue_semantic_review |
| P2 / C1-04 | 10 | 仅修正建议改为任意已保存支付方式；事实故障不变。 | unsupported / reject | continue_semantic_review |
| P3 / C1-05 | 0 | 固定turn0问候；尚未看到资料访问或事务操作，认证违规证据不足。 | insufficient / abstain | route_to_harness_or_non_agent_review |
| P3 / C1-06 | 2 | 争议文本同样为问候，但为turn2合成model输出；可修改不等于诊断成立。 | insufficient / abstain | continue_semantic_review |
| P4 / C1-07 | 14 | 已知第二件商品偏好待定，仍提前提交一次性修改；建议先问清并共同确认。 | supported / accept | continue_semantic_review |
| P4 / C1-08 | 14 | 同事实故障；仅建议指定未来才披露的红色背包偏好。 | insufficient / abstain | continue_semantic_review |
| P5 / C1-09 | 6 | 用户不知余额、工具也未给出，Agent却断言具体余额；建议先查余额。 | supported / accept | continue_semantic_review |
| P5 / C1-10 | 6 | 同无依据余额声称；修正反而指定隐藏材料才有依据的120美元卡余额路线。 | insufficient / abstain | continue_semantic_review |
| P6 / C1-11 | 6 | 未完成写前明确确认，索引动作已为取消工具请求。 | supported / accept | continue_semantic_review |
| P6 / C1-12 | 6 | 同诊断/正确一般建议；索引动作只是认证后的只读资料查询。 | unsupported / reject | continue_semantic_review |

作者方案共 **5 supported、3 unsupported、4 insufficient**；11张来源可进入语义审查，C1-05须转harness/non-agent审查。5个supported均为来源可修改的正例，因此有可观察的合成保留分母；任何标签分歧须在实际调用前解决或保留为不确定层，不按“必须凑5正例”强制判断。

- `supported`：精确事实、当前责任和修正可选性均有证据，不代表修正策略一定有用。
- `unsupported`：有明确前缀或政策反证。C1-02和C1-12的一般建议是正确的，但不证明对应时刻发生了错误；C1-04的事实故障正确，却给出违反政策的修正。
- `insufficient`：不能从当前信息确定归责或精确修正，不能算成已证明的误诊。C1-08/10中已支持的事实故障仍可存在，不足的是整条修正的准入条件。
- **来源资格独立：** 框架问候来源已由固定初始化分支和fixture账本明确；这不能让原semantic gate自动知道来源。普通model消息来源可控，也不让含糊批评变真。C1-05/06故意保留“问候是否足以证成认证违规”的证据不足，不把N1争议轻易重写成确定正例。

P1只有一条此前用户确认消息改变；P2/P4/P5只改变`correct_behavior`；P6只改变索引操作及其合成账本绑定。**P3不是相同view的一因素对照**：争议文本相同，位置、前缀和来源不同，用于区分语义与来源轴，不能由它估计来源处理的净因果效果。六对也不是六个随机抽取的天然任务。

## 3. 精确接口与证据边界

每卡固定：`diagnosis`（现有source/error_tags/severity/turn_idx/reasoning/correct_behavior形状）、原始`messages`、争议turn、来源fixture、diag/messages/view/payload/tools/policy hash。`turn_idx`按显式索引定位；不猜数组最近的assistant。全12卡均可通过现有 `project_action_context` 和 `subscription_feedback._validate_gate_payload`。

给真实semantic judge的唯一材料是现有 `GATE_INSTRUCTION` 与：

```text
{
  diagnosis: {reasoning, correct_behavior},
  action_view: {schema_version, action_index, policy, tools,
                history before action, indexed action, view_sha256}
}
```

复用 `audit_diagnosis(..., judge=AdmissionJudge(...))` 或明确合成状态的现有桥即可；函数名含native也不能把输入标为原生诊断。**不得伪造`NativeReviewResult`或native call ID以穿过自然来源注册器。** 本任务没有实现runner、没有接GEPA或更改G4。

P4的未来用户消息和P5的隐藏余额只存于`host_only_annotations`，不在`stimulus.messages`。被污染的diagnosis文字本身可包含这些值，这是待审查的主张，不能把它当证据；“已可见红色库存”不等于“已知道用户选红色”，“Agent刚声称120美元”也不能给自身声称补证。未向行动时视图额外注入隐藏材料。

`host_only_truth_proposal.evidence`给出政策文件/行/原句、历史消息数组index与turn及精确引用、索引动作JSON。`gate_reference_candidates`仅用现有schema允许的policy/history来源；索引action可被gate读取，但不可伪造现有schema不接受的action引用类型。正例无需结构漏洞或非法turn才能成立；所有tool arguments已匹配官方schema。

### 3.1 来源账本的有限意义

`synthetic_origin_fixture.calls`逐字绑定`raw_data.output`、渲染文本/工具参数和call ID，实际通过现有 `classify_action_origin` 的确定性检查。所有账本均明确 `fabricated_ledger_not_real_calls=true`，**其“model_agent”只表示假设场景中的来源类型/接口可识别性，不是实际发生过模型生成**。真模型来源证明仍须靠真实client原始记录；这套fixture不认证生产者身份。

框架卡绑定固定orchestrator hash、非solo、无初始history、turn0、成本0和首个模拟用户请求中的默认问候。来源判断不能仅比较文本或role。C1-06具有相同问候文本但独立匹配的合成agent生成元数据，验证“同样文本≠同样来源”的契约。

## 4. 独立首轮标注与后续执行建议

1. 主代理另做匿名材料投影：共享政策/tools＋每卡diagnosis和行动前视图；来源证据另加明确标为合成的provenance段。**排除pair_id、作者标签/理由/expected决定及未来/隐藏宿主注释**，卡号和顺序用先冻结的固定映射。红队不要先阅读本报告第2节或JSON的truth字段再自称盲标。
2. 红队按三个维度独立给supported/unsupported/insufficient及证据；另标source/editability。保留首轮，之后与作者标签对照。无法收敛的卡不能计入确定supported/unsupported分母；报告原计划12卡和分歧层，不换题补标签。
3. 若执行，另登记这两个文件及实际量表、client/机制hash、顺序和全角色预算。当前建议每卡至多一次semantic调用，共12个上限；同一次原始semantic结果与确定性来源路由分开分析。框架卡属于**semantic-only诊断探针**，复合准入生产路径应在origin阶段路由，不额外计作semantic拒绝胜利。若正式方案仅执行origin eligible的11卡，必须在首调用前明示，C1-05的semantic结果保持未运行。
4. 不改prompt、阈值、schema或标签来追回目标答案，不自动重试或替换卡。传输失败/unknown usage保留并按正式预算规则停止；计划、已调用、结构弃权、semantic弃权、来源路由、未运行分别计数。预算尚未登记，本草案不授权任何模型调用。

本次交付只提出首轮材料；**独立标注、标签锁定和实际gate结果均尚未完成。** 后续不能把本任务的本地接口检查报成校准实验通过。

## 5. 报告口径

先逐卡报告三个维度、语义决定、origin eligibility和复合处理；再给：

- 来源可修改且supported的卡：保留数/拒收数/弃权数，分母为锁定正例数（当前作者方案5）。
- unsupported：错误接受/拒收/弃权（作者方案3）。
- insufficient：接受/拒收/弃权（作者方案4），不并入“误诊检出率”。
- framework与unknown来源：路由/弃权另列；不能把省掉的semantic调用算正确判断。
- 每卡实际调用、input/output tokens、wall time及中断；成本包括失败，不因没有合法verdict而免费。

这些是小型固定合成集合的描述性计数；**不计算自然发生率、总体召回、统计校准曲线或显著性，不宣布优于CAST、GEPA，也不替代G4自然反馈或H1/H2。** “校准”在标题中指检查是否有正例保留与不足证据处理能力，不是已经完成概率校准或生产质量认证。如果B1/GEPA本来能自行忽略坏建议，此集合也不能证明gate有净优化收益。

## 6. 来源和落盘核验

- `auth`：固定policy第10行；对应完整原文和引用定位见JSON。
- `confirm`：固定policy第16行；对应完整原文和引用定位见JSON。
- `no_invention`：固定policy第18行；对应完整原文和引用定位见JSON。
- `once`：固定policy第84行；对应完整原文和引用定位见JSON。
- `gift_balance`：固定policy第104行；对应完整原文和引用定位见JSON。
- `return_payment`：固定policy第124行；对应完整原文和引用定位见JSON。
- `cancel_confirm`：固定policy第90行；对应完整原文和引用定位见JSON。

原始链接：[retail policy](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/data/tau2/domains/retail/policy.md)、[retail tools](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/domains/retail/tools.py)、[orchestrator默认开场与初始化](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/orchestrator/orchestrator.py)。本轮使用本地固定副本；`LITELLM_LOCAL_MODEL_COST_MAP=True`，不需模型或网络调用。

数据JSON SHA256：`b0347122acd3a1d4489c696857a3387245a8905562589170d644ac1fa8f77316`；policy文件SHA256：`2c9652afbce57d6e087768d37cda64d31c53d50b3e3225cfdb791bac66466467`；官方tools数组规范化SHA256：`669f1a85b06f19543db913f1ee455bac9cda2f57ff96c66c58d8ad795480813e`。14个来源文件hash、工具函数源码起止行、GATE_SCHEMA与instruction hash均在JSON内。

已做的本地检查仅包括12个action-view及输入schema、每条工具参数schema、每条引用定位、合成来源绑定、卡ID唯一和5/3/4作者标签计数。未生成semantic verdict、未运行模型、未把fixture称天然轨迹。

匿名材料补充：合成call ID已改为不含P1/P2等成对条件名的固定不透明哈希；仍保留工具请求/返回的精确配对。P2/P4/P5已经重算确认两版本action-view逐字一致，只有修正字段改变。
