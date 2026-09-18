# G4 后置选择脚本：实现与契约检查

2026-09-17 15:57 UTC；作者 related_work。这是登记后新增的分析实现，执行冻结 `G4_PROTOCOL.md`、`g4_assessment.py` 与 `run_g4_test.py` 的原公式，不是重新预注册，也没有改变既有 G4 源、规则或输出。

## 接口及边界

新增 `scripts/finalize_g4_selection.py`，参数为 `--manifest`（默认 `experiments/G4_MANIFEST.json`）、`--review-red`、`--review-related`。后两项是宿主映射版复核文件，reviewer 必须分别为 red、related。未执行真实 finalizer，未读取 G4 的实际轨迹、策略、诊断、评分或 STOP 内容。主代理告知本阶段已经 STOP；该前缀不符合本脚本条件，本脚本只作为完整 cohort 的工程交付。

完整输入才允许选择：无 active.lock/STOP/test/已有选择，RAW_COMPLETE 与原登记一致；4 批父代、共同基线、8 个候选的原 stage seals 及全部文件集合逐一核验；共 120 条实际 episode 必须全部得到两份五源 hash 绑定的复核。验证区基线及每候选均须覆盖 4 task × 2 seed，不能用缺行替代 unknown。

实际候选文本、GEPA best/execution、生成次数、执行阶段、task payload、精确策略、seed、request、simulation/trajectory/outcome 及反思输入 hash 均重核。映射版复核若提供 top-level `source_sha256`，其原匿名标签、packet、映射等来源也验证并纳入最终绑定；脚本不重新裁定标签。任一审查者任一 cohort 行 critical_user_error=true 会写 STOP 与 SELECTION_BLOCKED，并禁止选择；普通失败与分歧按冻结公式保留。分歧 status→unknown、critical→null，随后原样调用 `assess_candidate`、`choose_final`，最后再次通过未改动的 `run_g4_test.selected_policies` 验收。

成功时 write-once 生成 `FINAL_STRATEGIES.json` 及 `.sha256`；分析脚本本身 hash、执行开始 UTC 和登记时间进入结果。既有文件不能覆盖。其他执行期校验错误留下 SELECTION_BLOCKED，不能改为选择成功。实际磁盘写失败或进程强杀不保证事务式封存；缺少合法最终 hash 的输出不能通过测试入口。

## 验证

新增 `tests/test_finalize_g4_selection.py`，使用临时目录构造 120 条完全虚构工件，阻断 model generate、CLI 底层入口及 subprocess：

`python -B -X utf8 -m unittest discover -s tests -p test_finalize_g4_selection.py -v`

7/7 通过，53.091 秒，exit 0，真实模型调用 0。覆盖：完整 120 条/72 验证行及原测试入口重放、缺复核、来源篡改/外来映射、分歧转 unknown、父代关键用户错误封存 STOP、活动/停止/缺 cohort/已有输出拒绝、成功输出 hash 与重复执行不覆盖。只验证工程契约，不代表任何真实候选被采用或 G4 研究完成。

| 新文件 | SHA256 |
| --- | --- |
| `scripts/finalize_g4_selection.py` | `8e68a5dffaac61d465224e491f61fe0c9c3c96284ae6d172501774e20b8ff8cd` |
| `tests/test_finalize_g4_selection.py` | `33511870e5e112e22085f49e538b6dc3a14410444dcfd5bd528234b3f2d28f99` |

来源认证的边界：该工具核对已封存字节及显式对应关系，不能独立证明 AI 复核者完全盲法、上游标注语义正确或宿主映射者的身份；这些仍由原始包、首轮标签、映射和过程披露共同审查。没有为这份登记后的实现补写“早于实验”的虚假时点。
