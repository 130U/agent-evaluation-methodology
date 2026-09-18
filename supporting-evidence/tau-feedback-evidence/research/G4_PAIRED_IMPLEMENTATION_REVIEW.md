# G4 共享父代与全候选测量实现独立审查

2026-09-17；研究代理 red_team。范围为 `g4_paired.py`、原有适配/证据接口及新假环境测试；没有读取新模型结果，没有执行真实模型或修改实现、旧实验。本报告记录首次问题；修复验收在后续小节追加，不抹去初次失败。

## 首次结论

**生命周期核心方向成立，但初版存在三类必须修复的输入与证据绑定问题。** 它们可使复用的父代/验证记录与声称的运行条件不一致，或将重复题计作独立验证单元。最小反例已实际复现，不是推测性的恶意篡改论证。

| 问题 | 实际反例 | 最小修正 |
|---|---|---|
| 父子训练 seed 未绑定 | 共享父代 runner 为 101，传入 child `train_seed=102`，真实固定 GEPA 仍完成提案、比较与后续测量 | 在任何反思/候选调用前检查父代封存 seed 与训练 seed 一致；最终 driver 核对实际父代 request |
| train/val 唯一性与交叠未检查 | 重复两份同一 val，或将 train 同题作为 val，均正常进入 GEPA；`len + set` 不能拒绝 expected 本身的重复 | 入口检查非空、EpisodeInput、全体键唯一且 train/val 不相交；父代批次与注册 train 集合一致 |
| 测量自报元数据未连接实际 request | measurement.json 声称 seed=211/S0/指定任务，但已哈希的 environment/request.json 分别写另一 seed、另一策略、另一任务哈希，三例均被 verify_measurement 接纳 | 必须绑定五个核心源文件；核对 request 的 run/task/任务哈希/策略/seed 与预期，并保持 outcome/run/score 校验 |

这里的缺陷不是“攻击者可改所有文件”的泛化安全要求：反例保留了原始请求差异，但初版读取器没有比较现成的两个矛盾来源。driver 的模型/环境/预算配置封存仍可承担公共配置约束，不必引入签名或新认证系统。

## 独立首次测试

新增 `tests/test_g4_paired_review.py`，使用固定真实 GEPA 与既有 FakeClient/FakeMeasurement；原生进程启动、新恢复 transport 和真实 episode 入口另设拒绝 guard。测试不会产生研究模型请求。

首次实际执行：4 个 test methods，6 个 failures（测量 request 的三个字段分别为子例），全部为预期拒收没有发生。日志：`results/G4_PAIRED_REDTEAM_INITIAL.log`。该失败记录已先交给实现者，再允许其修改源码。

已有 7 项测试由父代理报告通过；独立反例不替代其正常路径覆盖。尤其正式 MeasurementRunner 原始请求/封存检查不能仅由被 mock 的正常 GEPA 测试证明。

## 核对成立的生命周期

- B1/B2 的父代通过共同 records/store 引用；原始记录与源证据变化会触发检查，反思前沿用原绑定与共同隐私投影。B2 的语义/来源筛选不会把拒收理由回灌反思。
- phase 0 为共同 S0 validation，phase 1 为共享父代训练；phase 2/3 执行实际 child。分流依据是阶段，因此 child 文本等于 S0 时仍执行新 episode。
- child 只做真实环境及官方评分，返回 `g4-measurement-reference-v1`，无原生诊断/gate；父代才提供反思材料。`reflected` 与 phase 条件禁止将 child reference 用于第二轮反思。
- 唯一真实提案经过官方 GEPA 严格接受规则；GEPA 拒绝并不跳过固定候选验证。已完成的第一重复仅按完整记录复用，部分验证不按成绩补齐；第二重复按固定顺序新运行。
- GEPA milestone、adapter、measurement、reflection 和 client 的失败状态在终点检查；研究验收仍明确 pending semantic review。实际 episode 计数与逻辑 metric references 分开，不把共享父代引用算第二次调用。

## 尚属 driver 的责任

本模块本身不构成 G4 登记/总预算/全研究完成证明。后续 driver 应在提案前封存四个父代批次和完整 S0 验证，固定配置与运行次序；保证共享基线复用条件及阶段身份；保留全部八次分配机会、异常与实际候选分母。无候选、无输入处理差异、无可采用策略均须按登记终点报告，不得为了补足数量重试或换题。

以上不要求增加模型预检或额外实验；本轮只要求修复已复现的绑定问题。

## 首次证据哈希

- `tests/test_g4_paired_review.py`: `b0d10ee3861dcea795382ad8c406cf6f90ea24a840a3ee0567c0a2649fd3bef4`
- `results/G4_PAIRED_REDTEAM_INITIAL.log`: `da432060573a8cd31a41c4dde1b2aee4b790a34c9c4cf2386e9d90b41e804d1d`

## 修复后独立验收

实现者修复后三类问题已逐项读回：`validate_pair_inputs` 在提案运行前拒收非空/类型/唯一性/交叠错误、父子训练 seed 不一致以及父代/训练集合差异；`verify_measurement` 要求五个核心文件的哈希，随后对原始 request 的 run/task/taskhash/strategy/seed 作一致性校验。

独立重复执行原反例文件，未更改反例：**4/4 test methods 通过，三个 request 字段子例全部通过**。日志 `results/G4_PAIRED_REDTEAM_FIXED.log`。首次 6 个失败仍保留，未重写为首次即通过。父代理另报告原 7 项、红队 4 项和 assessment 5 项共 16 项通过；本轮独立重跑范围仅红队 4 项。

**本有界实现审查的已确认阻断全部解决。** 可以继续新 driver 的登记前集成；这不是对尚未完成的全局封存、语义审查、总预算、统计或研究结果作完成认证。本轮零模型调用，旧实验与实现文件未由红队修改。

修复验收快照：

- `src/tau_feedback/g4_paired.py`: `b01500d2c27e5528428c52c98bf9b51fa27a3c4cf304c9217456fdeb2f1f0814`
- `tests/test_g4_paired_review.py`: `b0d10ee3861dcea795382ad8c406cf6f90ea24a840a3ee0567c0a2649fd3bef4`
- `results/G4_PAIRED_REDTEAM_FIXED.log`: `a0141ea6699fd52df367c1e7a846506fa9591b4d7dd68e5033656d61621d223c`
