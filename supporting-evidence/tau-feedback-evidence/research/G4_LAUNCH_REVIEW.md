# G4 登记、运行与留出测试入口审查

2026-09-17；red_team（研究代理）。本轮只读检查 `register_g4_paired.py`、`run_g4_paired.py`、`run_g4_test.py` 与正式 `G4_PROTOCOL.md`；仅新增独立测试和本报告，不修改实现/旧实验，不执行研究模型调用。

## 初次发现与最小修正

1. **缺行仍能起跑留出测试。** `selected_policies` 对空 baseline_rows 与八个空 candidate_rows 重算出“保持 S0”，随后仍返回可执行策略。固定分配机会和逐例审查尚未完成，不能因不采用候选便略过。要求 S0 与每候选均具有完整、唯一、精确的验证 task×seed 集合；语义 unknown 仍可保留，但不能缺行。基线需限定已登记共享目录/phase，候选限定实际 proposal 目录/phase/candidate_id。
2. **关键用户错误没有终止分支。** 绑定正确的独立 review 标记 critical_user_error=True、user_validity=fail 时，`verify_reviewed_row` 原样通过；后续只会拒绝采用候选，并不能兑现“关键用户错误停止全轮”。需把该第四字段纳入合同，任一独立复核明确 true 时暂停留出测试并写 STOP；null 与普通失败不能改判为 pass。
3. **执行时 seal 未核验。** 运行器写出的 shared/baseline/candidate seal 没有被最终测试入口读取。最终选择文件记录当前哈希，不能替代原始执行封存。要求核验既有 seal 的文件集合与哈希，并把 seal 本身纳入最终选择来源锁。这是利用已有证据检查意外修改，不引入安全认证或签名框架。

独立新增 `tests/test_g4_stage_review.py`，以临时合成文件构造前两项反例；调用模型入口设禁止 guard。首次实际执行 **2 tests / 2 failures**，都为应拒收却返回成功。初始日志 `results/G4_STAGE_REDTEAM_INITIAL.log` 已在修复前保存。第三项为明确源码发现，本轮不扩大伪造全阶段的测试框架。

## 已成立的机制与完成边界

- 登记写入新 manifest、逐源哈希及含 manifest 的源码 ZIP，拒绝覆盖；verify 同时检查当前源、ZIP 和精确任务 payload。旧 G3 终点未被重新解释。
- 优化运行先封存四批共享父代和八个 S0 验证，再按固定交错次序产生候选；阶段输出目录排他创建，不恢复部分目录。异常保留 STOP 与已知客户端费用。
- 最终测试复算候选 assessment 和排序，核对真实生成的文本及 GEPA 选择标记；同策略哈希共享一次真实测试，正确避免伪造三个独立组。
- H1 的全候选质量与 H2 的最终采用策略分离。相同 S0、未激活反馈、无候选被采用，均不得改称机制增益或泛化无效。
- 120 优化 episode 与至多36测试为完整最大安排；发生 STOP 不补题、不重跑、不为凑齐数量开启同题版本。59.5M输入/470k输出/3660调用/77400客户端秒为停止阈值合计，不是预计消费或硬实时上界。

## 登记前需明确的审查细节

最终 review 的源路径中含 B1/B2。若直接交给复核者，则无法兑现提交标签前隐藏组别。可用匿名 review_id 与五个 basename/hash，先封存原始标签，再由宿主映射绑定；保留原标签与映射证据。也可以降低盲法承诺，不能同时暴露路径与声称已隐藏组别。

自动检测发生在原生 reviewer，独立复核发生的时点需明确。若审查中途会写 STOP，运行器应在下一 episode/提案前检查；若只在原始优化全部结束后进行独立审查，不能描述为实时发现并即时停止。任何已检测到的关键用户错误都不能继续启动留出测试。

## 首次证据

- `tests/test_g4_stage_review.py`: `06fca36c71f8036fd1425d4ed85ce93bb71863432b6f2d7ef95e16428e16e169`
- `results/G4_STAGE_REDTEAM_INITIAL.log`: `95e6f8eeda8936598dea8c95e4e273a2a6a925af36ad33df4cfc2decbaedd49d`

## 修复后最终验收

已逐项读回修复：

- S0 与每个候选强制完整、唯一的固定验证 task×seed 覆盖；目录、phase、candidate_id 均核对。
- 四个父代目录、共同基线和八个候选目录的原执行 seal 全部核验文件集合及字节哈希，且 seal 本身必须列入最终选择来源锁。
- 任一独立 review 的关键用户错误抛专用 `CriticalUserReview`，测试入口捕获后写全轮 STOP，尚未创建测试 client。最终又补全范围：先验证两份 review 的本研究/审查者身份，扫描其**全部 items**，包括父代和 child train，故不会仅检查72条验证行而遗漏其他已发现的关键用户错误。
- 协议明确独立审查在完整候选批之后进行；匿名 basename/hash 包、原始标签封存、宿主路径映射与两版本保留的边界已写明，不再暗示执行中已有实时独立审查。

原独立两反例未改，实际复跑 **2/2 通过**；修后独立日志 `results/G4_STAGE_REDTEAM_FIXED.log`。最后“全部 review items”的小修复作只读源码核验，父代理另报告20项G4测试全部通过；本报告不把该20项写成独立重跑。

实际执行了一次**只读 `register_g4_paired.build()`**：通过，1055个源文件、dev/validation/test 为4/4/6；本审查报告已在来源集合中。调用前后 manifest 与源码ZIP均不存在，未登记、未产生模型请求。最终追加文字将由真正登记时重新取哈希，不能沿用预检过程中的报告字节哈希。

**本轮发现的启动阻断已解决，可以登记并按固定协议开始 G4。** 这仅是方法与执行入口放行，不证明研究已完成或能够获得正收益；最终语义审查、候选验收、留出启动门及 STOP 规则仍须执行。不会因负结果改变样本、预算或接纳门槛。

最终验收快照：

- `scripts/register_g4_paired.py`: `4723b3446b30f259a985bcae50cc6044527eb639de59536276280dd77cc3cc8c`
- `scripts/run_g4_paired.py`: `7f8731fe5d67c1d2441e36ac92f90c4cbacef3c5d973ef93e43c6fb273e323a9`
- `scripts/run_g4_test.py`: `a215afece991d36cd70661ec38f9a3fcebd9a63969e1f84a27bb5ac05efeffc0`
- `research/G4_PROTOCOL.md`: `177db635fd2a5cd0d0a7bea161f37b0339ff6a29d83d48774b85baaddb68dc91`
- `results/G4_STAGE_REDTEAM_FIXED.log`: `3032ba9987705721c9747f38b2e1aff5c84573be0047f63100e956b49d3f08ca`
