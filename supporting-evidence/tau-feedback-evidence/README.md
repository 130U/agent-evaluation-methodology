# τ-bench 反馈证据研究：公开复现包

这是经白名单导出的衍生副本。`PUBLIC_EXPORT_MANIFEST.json` 记录原始文件与导出文件 SHA256、变换及未公开类别；它不冒充原始冻结实验目录。

## 离线 E0–E3 复现（Windows，Python 3.12）

首次下载公开依赖需要网络；后续离线审计和受控实验不调用模型。

```powershell
python -m venv .venv
.venv\Scripts\python.exe scripts\download_runtime.py
.venv\Scripts\python.exe scripts\download_archives.py
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe -m pip install --no-deps -e vendor\tau2
.venv\Scripts\python.exe -m pip install uv==0.12.15
.venv\Scripts\python.exe scripts\download_gepa.py
.venv\Scripts\uv.exe --cache-dir vendor\gepa\.uv-cache pip install --python .venv\Scripts\python.exe --no-deps --editable vendor\gepa
.venv\Scripts\uv.exe --cache-dir vendor\gepa\.uv-cache run --no-project --python .venv\Scripts\python.exe python -B -X utf8 scripts\run_offline.py
```

阅读 [离线结论](results/OFFLINE_FINDINGS.md)、[归档审计](results/ARCHIVE_AUDIT.md) 和 [复现边界](PUBLIC_REPRODUCTION_BOUNDARY.md)。公开默认测试只是可独立运行的离线/组件子集，其运行数量不能替代原研究的全量测试记录。下载后保留上游许可证。

## 不调用模型的动作来源控制复现

完成上述公开源码与依赖准备后运行：

```powershell
.venv\Scripts\python.exe -B -X utf8 scripts\reproduce_origin_initialization.py
```

该入口用官方初始化器检查 12 个开发任务、3 种策略；生成与 run/step 均被阻断。它只复现初始化控制，不需要原始 N1/G1 调用账本，也不会重建自然诊断审计。输出目录已存在时拒绝覆盖；原研究结果与复现结果必须分别保存。

## G3 结果与组件复核

[系统规格](research/SYSTEM_SPECIFICATION.md)说明实际输入/输出、信息隔离、状态流转、真实候选案例及假设边界，是登记后的实现说明。

[G3 派生结果](results/G3_PUBLIC_SUMMARY.json) 保留每组12个分配单元、未知/中断/未运行、逐题重复、成本、真实执行策略及诊断筛选数量；其终点可能是完成或按协议停止。以该文件的 `execution_status` 为准，不因打包成功推断36次全部完成。

[对话与审查证据](results/G3_PUBLIC_TRAJECTORIES.json) 提供全部已完成优化/测试的公开合成对话、工具参数/结果和两份AI审查依据，供读者对照固定政策自行判断。宿主元数据及原始CLI事件被省略；工具调用ID替换为保留关联的局部编号。该视图可以支持语义复核，但不能独立认证未公开的原始传输日志。

来源/验收/指标组件测试不调用模型。准备上述依赖后运行：

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

G3 原始运行脚本保留作为实现证据；公开包没有原始冻结manifest、源ZIP、模型目录或调用日志，不能直接用这些脚本恢复作者的历史实验。重新接入模型须建立新登记，不能修改哈希冒充同一研究。完整测试的两次重复不能等同于模型随机种子被控制。


## 最终阶段：G4 停止与 C1 合成校准

G4 在共享父代阶段按登记规则 STOP，候选比较、最终选择与留出没有运行；H1/H2 未被该阶段识别。完整计划分母、已运行/未运行、两份 AI 复核的分歧与成本见 [G4 停止报告](results/G4_STOP_REPORT.json) 和 [机会报告](results/G4_OPPORTUNITY_REPORT.json)。[匿名对话包](research/g4-review/cohort/packets/REVIEW_INSTRUCTIONS.txt)及两份首轮标签可供独立语义复核；包里的参考任务信息不是 actor 当时已知事实。

[C1 结果](results/C1_SYNTHETIC_REPORT.json)是 12 张固定合成卡的准入组件校准。合成正例保留不等于天然反馈保留率，更不等于 Agent/GEPA 性能收益。合成源、匿名包和独立参照标签均显式标记来源。

本副本新增恢复事件、配对测量、固定验收及假工件测试。旧 G3 包的 321 tests 结果只属于旧包，不能作为本副本已通过的复现记录；新副本的隔离复现须另行运行和保存。发布前的 AST/JSON/链接检查也不等于测试套件或全仓 CI。
