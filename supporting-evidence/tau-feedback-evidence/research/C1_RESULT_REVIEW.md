# C1 完成结果独立复核

2026-09-17；复核者：red_team（AI 研究代理）。**核心计数、实际输入投影、来源判定与调用账本一致，未发现使本轮合成结果失效的实质错误。** 本次在结果揭示后审计，复核者也是执行前匿名参照标签的作者，不能算第三份独立真值或人类金标准。原首轮标签未修改。本次没有模型调用、重跑实验或重跑 write-once reporter；没有读取另一位 G4 复核者的标签。

## 已核验的证据链

- 只读 `verify()` 验证正式 manifest、28 个冻结源、原卡→匿名卡映射、原独立标签及固定参照；执行目录 208 个文件的完整集合与原 seal 逐字节匹配。12 个单元均 complete，顺序与预登记一致，没有替换、丢卡或额外单元。
- 对全部 12 卡调用只读 `replay_unit()`：实际 judge 的 payload、完整 prompt、保存输出与重新构造的 action-view 一致；每卡实际 payload 的规范化 hash 也匹配原卡的 `gate_payload_sha256`。未来/隐藏宿主注释、作者标签、配对标识和合成来源台账没有额外进入该任务 payload；诊断本身的待审主张仍然可见，不能把它当成行动时证据。
- 准入决定从已存模型输出纯重放，与 admission sidecar、单元记录和派生报告一致；12 卡均实际调用一次 semantic judge，没有将结构弃权冒充模型结果。12 个 judge call ID 与客户端全部 12 次调用一一对应。
- 另外独立重放全部来源分类与 strategy eligibility，均匹配原结果；再逐卡重算 `semantic accept AND eligible`，与 `compound_admitted` 一致。原卡 `origin_fixture_sha256` 的口径是 `{context,calls}`，不是含 `fabricated_ledger_not_real_calls` 注释的整个外层对象，12 卡按该口径均匹配。来源材料明确为合成账本：11 个 model_agent 情境、1 个 framework_seed 情境，不是真实生成行动的来源认证。
- 原始事件、stdout 字节副本、最终 JSON/schema、角色/模型/传输配置、stderr、退出与进程清理、usage 及预算经 `audit_client()` 只读重放，与派生报告相同。12 次全部接纳，unknown usage 为 0，观察到的重连通知为 0。

## 全分母结果

| 预先固定的 AI 一致参照层 | 分配 | accept | reject | abstain |
|---|---:|---:|---:|---:|
| supported | 5 | 5 | 0 | 0 |
| unsupported | 3 | 0 | 3 | 0 |
| insufficient | 4 | 2 | 1 | 1 |
| 合计 | 12 | 7 | 4 | 1 |

以 supported→accept、unsupported→reject、insufficient→abstain 计算，三态一致为 **9/12**。5 条充分且可修改的正例全部满足组合准入；3 条明确不支持案例全部拒收。语义接受为 7/12，来源交集后为 6/12，其中 5 条充分、1 条证据不足。证据不足层接受由 2/4 变为 1/4，分母始终为原定 4 张卡。

- **C1-05 / C1-06：** 两卡都只有不足以证明认证义务已经被违反的问候证据，模型却都 accept。C1-05 的合成来源为框架开场，来源资格将其排除；C1-06 为可修改的合成模型输出，仍满足组合准入。这里观察到的是来源过滤的具体增量与语义审查的剩余缺口，不能推成总体净收益。原始参照已保留对“at the beginning”解释空间的说明，不将这组 AI 判断称作无争议真值。
- **C1-08 / C1-10：** 两卡分别 reject、abstain。C1-08 的具体红色修正没有可见用户偏好支持；未被接纳不等于准确区分了“不支持”与“信息不足”。C1-10 对未观察余额的具体修正 abstain，与参照一致。报告没有把两种不接纳结果都算成正确三态识别。

这 12 卡属于 6 对目的性构造情境，不能当成 12 个独立自然样本；不能计算自然召回、自然错误率或 Agent 能力提升。

## 费用与运行范围

12 次真实调用均为 feedback_admission：输入 **182,910**、输出 **4,128** token；调用时间求和 **179.157 秒**，与客户端预算一致。无原生 reviewer、无环境 episode、无 GEPA 优化调用。没有观察到恢复事件，不能宣称真实重连恢复分支已验证。费用限于 CLI 报告的 token，供应商内部请求成本不可见；同时运行其他研究的墙钟表现不能用于机制因果效率比较。

## 两项非阻断的表述与公开边界

1. `C1_SYNTHETIC_RESULT.md` 的“进入最终反馈池”宜在公开稿写成“满足组合准入条件”。本轮只计算合成记录的 `compound_admitted`，没有实际进入 GEPA/Agent 优化池；不应让读者误解为完成了下游优化。
2. `results/C1_SYNTHETIC_REPORT.json` 的 `transport_audit.client_directory` 含本地宿主绝对路径。它适合作为内部派生审计文件；公开时应白名单导出计数、逐卡结果和必要哈希，不能直接当作已脱敏公开摘要。

报告已经明确 AI 参照、合成来源、无 Agent 收益及三态错误，结论边界总体恰当。只读报告脚本本身重放语义投影，但来源字段主要取自封存结果；本次审查补做了全部 12 卡的来源和组合条件重算，未发现差异。无需修改冻结材料、重新标注或追加模型调用。

## 本次绑定

| 证据 | SHA256 |
|---|---|
| `experiments/C1_SYNTHETIC_MANIFEST.json` | `b48e5d7d7935b13c77469c38c104caf8764644af7cb9778eb11b43a7cf3feac3` |
| `results/C1_SYNTHETIC_REPORT.json` | `e512db7b14e10491a877ac54df67531de4c1853da4bb2d68116480281810b91c` |
| `research/C1_SYNTHETIC_RESULT.md` | `6b50e70f3aff8a81d4fef2fdee4842a7d43bf363f2b43b38bcccd761a6641cbb` |
| `scripts/report_c1_synthetic.py` | `0cd491875b67e4d5bc5cb534238326645336b7f1d06a0b72e76891f0c6293151` |
| `research/C1_RED_BLIND_LABELS.json` | `9f40552100a39081b9eda5c3610b066c410ffde35f1d7ba09c79b8d4e770f459` |
| `results/c1/c1-synthetic-admission-calibration-v1/seal.json` | `b575c5ea0f16f67e9d03e7c53d4576f034d0b108b0965107605bab6f39971b23` |

## 同日叙述修订复核（保留上表历史 hash）

重新读取结果稿，当前 SHA256 为 `6b50e70f3aff8a81d4fef2fdee4842a7d43bf363f2b43b38bcccd761a6641cbb`。第 13 行已明确 `compound_admitted` 仅是合成组合准入资格、没有实际优化反馈池且未交给 GEPA；第 15 行已将框架来源明确归于合成台账。接受这两项修订，原统计与标签未改。第 17 行“未流入优化”仍建议统一改为“未获得组合准入资格”，属于用语一致性补正，不改变已核验结果。原报告 JSON 的宿主路径仍须在公开派生时剔除。
