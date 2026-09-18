# 官方公开 retail 归档审计

## 结论与证据边界

本报告全量复算四份官方历史归档；没有新运行 Agent、judge、GEPA 或环境重放。原始文件保持只读。结果仅描述这些公开任务和归档配置。
历史运行采用嵌入的 DB＋COMMUNICATE 评分。任务中的 NL 检查另行审计，不追溯套用当前 DB＋NL_ASSERTION 规则。

## 输入与历史配置

- 输入归档共 4 份、1824 次运行；每份字节数、SHA256 和 Git blob SHA 均与下载清单匹配。
- 托管归档快照：`2174a603f6d014ef94473ffa95957f6ce27100db`。四份文件内嵌自报运行提交：`c30d59aaa71c65f9b9eb6a8f8636b48945028fcf`。
- 2026-09-17 查询该历史提交：官方 GitHub commit API 返回 422（No commit found），contents API 返回 404。因此历史源码尚无法独立核实，自报 SHA 不等于已复现环境。
- 文件时间字段未附时区；本报告不推断它们为 UTC。模型名、采样设置、时间范围逐档保存在 JSON。

## 历史分数复算

对每任务四次归档运行，按 `comb(c,k)/comb(n,k)` 计算 pass^k，再对任务取等权平均。它估计 k 次均成功的比例（对 k 次子集等权），不是至少一次成功的 pass@k。若任务缺次、重复、缺分或非二值，该组保留且全体均值标为不可估；不悄悄删除。

| 文件内嵌 Agent 模型 | 归档日期 | 任务×重复 | 成功/运行 | pass^1 | pass^2 | pass^3 | pass^4 |
|---|---|---:|---:|---:|---:|---:|---:|
| claude-3-7-sonnet-20250219 | 2025-06-05 | 114×4 | 359/456 | 78.7281% | 69.2982% | 63.3772% | 59.6491% |
| gpt-4.1-2025-04-14 | 2025-06-05 | 114×4 | 338/456 | 74.1228% | 64.1813% | 57.8947% | 52.6316% |
| gpt-4.1-mini-2025-04-14 | 2025-06-06 | 114×4 | 301/456 | 66.0088% | 52.9240% | 44.2982% | 38.5965% |
| o4-mini-2025-04-16 | 2025-06-05 | 114×4 | 326/456 | 71.4912% | 59.3567% | 51.7544% | 45.6140% |

用户模拟器均为 `gpt-4.1-2025-04-14`，temperature=0；o4-mini Agent 使用 reasoning_effort=high，其余三个 Agent 的记录为 temperature=0。这些归档不是同时间当前模型排名，也没有随机代表性保证。

## 完整性与错误分母

| Agent | 结构异常运行 | 异常任务组 | 有期望NL运行 / NL异常 | 有期望COMMUNICATE运行 / 异常 | 工具error=True消息 / 涉及运行 |
|---|---:|---:|---:|---:|---:|
| claude-3-7-sonnet-20250219 | 0 | 0 | 32 / 0 | 152 / 0 | 109 / 85 |
| gpt-4.1-2025-04-14 | 0 | 0 | 32 / 0 | 152 / 0 | 121 / 88 |
| gpt-4.1-mini-2025-04-14 | 0 | 0 | 32 / 0 | 152 / 0 | 258 / 174 |
| o4-mini-2025-04-16 | 0 | 0 | 32 / 0 | 152 / 0 | 136 / 99 |

NL 检查只对嵌入任务非空断言计入覆盖分母；null/空任务断言归为不适用。结构检查包含数量、逐文本身份及重数、met 布尔类型、justification 字符串。工具返回 error=True 是运行中观察到的错误，不等于数据损坏，也不自动等于最终失败。原始 judge 响应未独立保留，不能将记录字段缺失反推为 judge 当时遗漏。

全部终止标记：{'user_stop': 1824}。完整逐任务重复、逐运行奖励分类、异常记录与评分分母见 `archive_audit.json`。

## 嵌入任务与当前任务的描述性差异

同 task id 对齐仅用于版本比较。`full_task_raw` 包含 schema 差异，不能称为全部语义改变；动作另投影为有序 name＋arguments。null 与空断言列表只在明确标记的比较列中视为相同。

| 项目 | 历史嵌入任务 | 当前固定快照 |
|---|---:|---:|
| 任务记录 | 114 | 114 |
| 非空 NL 任务 | 8 | 40 |
| NL 断言条目 | 12 | 61 |
| 非空 communicate 任务 | 38 | 36 |
| communicate 条目 | 63 | 61 |

四档嵌入任务是否完全一致：True。

首档对当前快照的逐字段改变任务数：`{"actions_name_arguments":7,"communicate_info_null_as_empty":2,"description":0,"evaluation_criteria":114,"full_task_raw":114,"initial_state":0,"nl_assertions_null_as_empty":33,"reward_basis":114,"user_scenario":21}`。完整 ID 清单逐档保留在 JSON。

历史 reward_basis：`{"DB+COMMUNICATE":114}`；当前：`{"DB":2,"DB+NL_ASSERTION":112}`。

版本改变本身不证明原任务或原分数错误。任何后续旧轨迹重评分必须明确标为新评价协议下的反事实重评分，并验证任务/环境/工具兼容性；不得把它混入本表或称作历史同协议复现。

## 不可识别的结果

归档含评价理由，但缺乏独立语义金标准，也未检出 simulation 顶层 reviewer 字段。因此无法估计自然反馈语义正确率、误归因率或反馈准入的泛化收益。结构覆盖完整不证明语义正确；没有结构异常也不证明评价器永不会产生异常。受控故障实验的检出率不能当成自然发生率。

## 来源与复现

- [归档目录与托管提交](https://github.com/sierra-research/tau2-bench/tree/2174a603f6d014ef94473ffa95957f6ce27100db/data/tau2/results/final)
- [当前任务快照](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/data/tau2/domains/retail/tasks.json)
- [当前官方 pass^k 实现](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/metrics/agent_metrics.py#L113)
- [历史提交核查端点（本次未找到）](https://api.github.com/repos/sierra-research/tau2-bench/commits/c30d59aaa71c65f9b9eb6a8f8636b48945028fcf)
- 分析登记：`experiments/OFFLINE_REGISTRATION.md`；输入清单：`data/archives/DOWNLOAD_MANIFEST.json`。
- 执行：`.venv/Scripts/python.exe -X utf8 scripts/audit_archives.py`；脚本仅用 Python 标准库，运行阶段不联网。
- JSON 中保留脚本 SHA256、输入哈希、完整分母与源链接。报告时间字段会变化；分析数值应保持确定性。
