# N1 匿名证据包与标签封存独立复核

2026-09-17｜red_team｜限时源码与合成数据审查。没有模型调用，未读在途 G1 task76/29，未修改实现或其他代理的文件。

## 信息边界和入口顺序

`build_n1_review_packets.py` 按共同哈希重排入选卡，再使用匿名 card ID。面向复核者的 packet 只含原诊断、政策/工具、被指动作及此前可见历史；分层和源单元映射另放 HOST 文件。原诊断可以包含待质疑的隐藏事实，rubric 明确它不是证据；不能宣称所有任务识别线索都被匿名化。先前读过 G1 的代理仍可能认出情景。

独立重跑已有 `test_n1_review_packets.py`：**3/3 通过**。用合成消息验证没有带入未来回合或 raw_data，缺失或重复 turn_idx 不猜最近动作。未发现本轮需要修复的信息投影缺口。

`run_phase` 的 gate 分支先验证 selection，再调用 `_locked_independent_labels`；两者均在任何 gate client 创建之前执行。检查包括标签文件哈希、packet seal、selection 对应关系、卡片完整覆盖，以及每个 gate outcome 绑定本次 labels hash。顺序符合“先标注并锁定、后运行 gate”的设计。

## 两项真实复现的封存缺口

新增 `tests/test_n1_label_review.py` 只在临时目录中构造一张合成卡，且同时禁止真实 client.generate 与原生进程启动。初次结果：**3 项，1 通过、2 FAIL，退出码 1**；完整原始输出保存在 `results/N1_LABEL_REVIEW_INITIAL.log`。

1. **原始标签覆盖没有验证。** 首轮记录只需有 reviewer、prior_familiarity 和正确文件哈希；即便其中 cards 为空，完整的最终 labels 也可获得背书。哈希证明文件未变，不能证明首轮标注真的存在。应验证原始记录对全部入选卡有合法标签覆盖，并保留首轮内容；若后续裁决改变标签，另留修订依据，不能覆盖原记录。
2. **无法定位状态可以被升级。** packet 已是 unlocatable/action_view=null，最终 labels 却可以写 located，三项全部 supported。应以 packet 的不可定位状态约束原始与最终标签，不得借标签猜一个未提供的动作位置。对定位成功但证据不足的卡，仍允许复核者标 insufficient，不能强迫肯定判断。

控制例——不可定位卡及完整的 insufficient 首轮/最终标签——通过。因此这不是所有标签都被拒绝的测试构造。两项均为 `ValueError not raised`，未涉及真实实验结果。

已报告 root，请其在注册前完成最小修复；修后独立验收将在下节追加。本轮不扩展到概率校准、正式人工标注或研究收益验证。


## 修复后独立验收

root完成最小修复后，red_team只读复核新增检查并独立重跑同一3项测试：**3/3通过，退出码0，0.098秒**。首轮记录逐卡验证合法维度，全部首轮记录的并集须覆盖完整选卡；首轮和终稿均与packet定位状态一致。不可定位卡只能标insufficient。终稿维度与首轮不同须留下非空adjudication_notes，原始记录继续按哈希封存。

已有入口仍在创建gate client之前完成上述锁验证，未将标签作为gate正确性的证明。root提供的23项端到端回归日志results/N1_LABEL_LOCK_FIXED_TESTS.log亦已读取尾部，23/23通过；该23项是root运行，本次独立执行的是上述3项，不混淆执行者。N1尚未执行，本轮不扩大测试或据此声称反馈判断有效。

本次两项实质阻断已解除；初次失败日志保留。

- src/tau_feedback/n1_audit.py: 870a7a2a555339602730eb2c355f412ed52c0ab775259ad235412d6cf52d3845
- scripts/build_n1_review_packets.py: 27a3a4cec6caf30686ed42e49ca0171c97458f11f85e79ad6cd6d0339ce306a7
- tests/test_n1_label_review.py: dd33fe7d648704192bc9ef30b9d280ca6eee9d3af5f5b115fe06ba24eaa0d4c7
