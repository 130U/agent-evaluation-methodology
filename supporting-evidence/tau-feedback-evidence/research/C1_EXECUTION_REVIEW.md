# C1 合成准入校准执行审查

审查者：related_work；日期：2026-09-17。结论：**构造异常的封存缺口已修复；当前未见阻断本协议登记执行的实质问题。** 本审查仅覆盖源码、冻结材料和假客户端契约；真实 gate 尚未在本审查中执行，不能提前报告校准结果。

## 范围及来源

只读检查 `scripts/c1_synthetic_calibration.py`、`research/C1_SYNTHETIC_PROTOCOL.md` 及其明确引用的 C1 材料和实现；没有读取 G4 在途轨迹，没有修改 G4 或既有 C1 材料，没有登记实验。审查者曾编写合成卡，因此本次属于执行契约审查，不是新增独立真值标注。独立参照仍使用已经封存的红队首轮标签。

本次最终核验的 SHA256：

| 文件 | SHA256 |
| --- | --- |
| `scripts/c1_synthetic_calibration.py` | `379ee84a06a6bb1c99e7a432b64971b291dcc8f258fd14de34279e69172029d6` |
| `research/C1_SYNTHETIC_PROTOCOL.md` | `66300f26643276affa2dd7ba4bec40156409320ddd93a04516b139e797a8962f` |
| `research/C1_RED_BLIND_LABELS.json` | `9f40552100a39081b9eda5c3610b066c410ffde35f1d7ba09c79b8d4e770f459` |

只读 `build()` 核验了 27 个来源、12 张卡及全部独立标签绑定；两份预先标签在当前材料上同为 5 supported、3 unsupported、4 insufficient。此处是参照一致性计数，不是 gate 表现。登记器随后若仅将本审查加入来源集合，脚本 hash 会改变，应以正式 manifest 的最终 hash 为准；不能把上表旧 hash 写成最终运行 hash。

## 已核验的执行合同

- **合成来源明确。** manifest、单元记录和协议均标记 synthetic，原生 reviewer 调用为 0、native_call_id 为 null、环境 episode 为 0。复用名为 `audit_native_diagnosis` 的接口不会建立原生来源；实际角色为 `feedback_admission`。
- **逐卡绑定。** 原卡、匿名包/映射、独立标签、争议 turn、诊断、消息、policy/tools 及 action-view 均有绑定检查；材料变化不能沿用原参照。
- **语义与来源分开。** 12 卡全部进入语义探针，包括 framework 卡；来源分类及 strategy eligibility 独立记录，compound_admitted 再取交集。不能将来源提前拒收等同语义判断正确。
- **宿主真值未传入提示。** 假客户端捕获的全部 12 个实际调用提示，在 `ADMISSION_EVIDENCE_JSON` 后的规范化 payload 与各卡锁定 gate payload 一致；未加入作者标签、pair_id、未来/隐藏注释或来源账本。诊断中的待验证断言仍会出现，这是待审主张，不是隐藏事实的证据。
- **冻结与停止。** 正式登记及输出目录拒绝覆盖；运行前后核验来源；异常和 unknown usage 传播并停止，无 runner 重试或替换；正常 semantic abstain 保留为结果。预算由共用客户端累计，全部角色只有本阶段的 gate 调用。

## 零模型契约检查及修补经过

以项目 `.venv/Scripts/python.exe -B -X utf8 -` 执行临时检查，显式阻断 `subprocess.Popen`、`subprocess.run` 及恢复客户端 `_run_bounded`；只使用假客户端。临时输出位于项目内自有 TemporaryDirectory，结束后清理。**实际模型调用为 0，CLI 进程启动为 0。**

修补前的 13 项检查通过：

1. 真实只读 build 的 27 来源/12 卡完整性；独立参照一致性，共 2 项。
2. 改诊断、改标签 packet hash、重复标签、改标签 view hash 均拒绝，共 4 项。
3. 12 卡假调用均执行语义且来源单列；全部实际提示与锁定 payload 一致，共 2 项。
4. 第 3 次发生 KeyboardInterrupt、预算异常、返回后 unknown usage，均停止、不重试、保留已完成前缀及 stopped 单元、封存且没有 COMPLETE，共 3 项。
5. 临时假 manifest 的来源核验通过；伪造 source hash 拒绝，共 2 项。未运行正式 register。

另发现实质缺口：客户端构造和材料加载原位于受保护块外；假构造异常只留下 PLAN，缺少 STOP/results/seal。已立即报告，主代理将构造及加载移入外层 try，并在构造前定义 client/results。

修补后追加 2 项检查，均通过：

- 构造异常原样传播；STOP、空 results、seal 完整且每项 hash 可核对，无 COMPLETE；client_initialized=false、known_cli_invocations=0、token ledger=null。
- 构造完成后材料加载异常原样传播；同样封存并停止，client_initialized=true、calls=0、已初始化 token ledger=0，无任何 generate。

合计 **15 项契约检查通过（13 项修补前、2 项修补后）**；这是分阶段检查数，不是 15 次模型实验，也不是声称全部旧检查在修补后重新运行。

## 报告必须保留的边界

本设计能检查固定合成情境的接受/拒收/弃权及来源路由，不能估计天然正例保留率、自然误诊率、GEPA 候选收益或 Agent 泛化。AI 作者与 AI 独立复核者的一致标签不是人类金标准；合成来源账本只检验来源契约，不证明现实生产者身份。

报告应保留全部 12 张预分配卡，以及各真值层的完整分母；中断、无 verdict、结构弃权及未运行不能并为成功拒收。CLI token 是生成后的停止阈值，单次可越界；应读取封存客户端预算中的实际已知 token、调用和时间，unknown usage 不能填零，供应商内部重连请求及其成本仍不可得。常规落盘成功时的异常封存已经验证，不把磁盘写入失败或进程强杀也声称为无条件可封存。

本审查不建议新增模型探针、改卡或追回预期答案；可按当前固定协议进行一次正式登记和执行，真实结果再独立汇总。
