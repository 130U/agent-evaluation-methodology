# G2：已知开发任务上的探索性 GEPA 案例

2026-09-17｜拟登记协议。仅新增独立入口；本文存在不等于已登记、执行或得到正结果。原 G1 v2 保持停止；本阶段不恢复原自然收益比较，不以 N1 的判定结果选择任务、反馈或候选。

## 问题与选择边界

检验已接通的真实任务→原生 reviewer→可选语义准入→固定 GEPA→候选重评链条，在一个有限开发案例中实际停在哪里、产生什么反馈和候选、耗费多少资源。训练任务固定 **85 / structural_43**；开发验证任务固定 **75 / structural_35**。两题均在此前开发集中使用，选择发生在知晓 G1 结果之后，属于目的性选择；两个不同结构组也不使它们成为盲测或未见任务。

S0 严格为：

> Follow the retail policy. Help the authenticated customer complete their request using the available tools, and accurately communicate the outcome.

模型为 `gpt-5.6-sol / low`，使用 `HttpCliTextClient`。每臂独立重新生成 S0 的训练/开发验证轨迹，不复用 G1 或另一臂轨迹。模型采样种子和云权重不可控，`simulation_seed=43`、`optimizer_seed=0` 不能被表述为使两臂随机输出相同。

登记前已知控制范围缺陷：N1 结束后的独立揭盲来源核查发现，turn 0 的问候来自官方 orchestrator 的 `DEFAULT_FIRST_AGENT_MESSAGE`，而非真实 acting 模型生成；现有 gate 仍可能把它归责于 Agent。原 N1 首轮标签和结果保持不变。G2 原样使用现有 B1/B2，不是已修复版；行动来源与策略可修改范围的改进需另行受控验证，不能混入本次结果。

## 固定顺序、实现与上限

1. 注册器固定当前本项目源码、τ2 `2174a603f6d014ef94473ffa95957f6ce27100db`、GEPA `15ee314f9c7d34ec153b809d401f42f55c4dcd76`、两题完整 payload、结构分组、依赖锁、CLI EXE/模型目录哈希、本协议及 source ZIP。已有 HTTP readiness 仅作运输和组件前置证据，不转译为 G2 任务效果。
2. 显式单臂入口先执行 B1；其结果完整封存且阶段没有 STOP 才能执行 B2。独立研究复核可在两次调用之间进行，不新增用户确认或人工签名要求。额外研究笔记放阶段 `reviews/`，不得改写已封存的 B1/B2 目录。
3. 原样调用 `run_gepa_arm`：每臂最多一次 proposal opportunity、4 个 episode、每次原生 agent 诊断池最多 2 条，盐 `g2-retail-case-20260917-v1`；策略上限 6000 字符。保留 `skip_perfect_score=True`、`strict_improvement`、不 merge、不缓存、`write_agent_state=False`、milestone 分类及所有错误锁存逻辑。
4. 每 episode 最多 40 步、6 次工具错误、900 秒。每臂所有 acting/user/NL evaluator/reviewer/gate/reflection 共用一个客户端：最多 160 次 CLI、2,000,000 输入 token、20,000 输出 token、2400 秒；单请求 120 秒。两臂阈值之和为 320 次、4,000,000 输入、40,000 输出、4800 客户端秒、最多 8 个 episode。token 是可被单次调用超越的阈值，不是服务端硬上限；预算相同不意味着实际支出相同。
5. 默认验证轨迹 `capture_traces=False` 不另生成 reviewer/gate；训练父/子轨迹仍有 reviewer，B2 仍对选定池执行 gate，即使子轨迹以后不再用于反思，其成本照计。不得把 GEPA 的 metric call、proposal opportunity、reflection 和 CLI 调用数量混用。

## 合法终点与停止

- S0 训练满分则按原 GEPA 跳过反思，允许以 `skipped_all_perfect` 结束；不改任务、S0 或 `skip_perfect` 强制造候选。
- 候选严格改善训练分数才可能接纳；若拒绝或开发验证仍选择 S0，如实记录。不能只以候选池大小推断生成了几个候选。
- 原生诊断为空时沿用现有共同无诊断表示；不人工注入、挑选另一条轨迹或编造反馈。解析未知、关键用户错误、评分/费用不完整、隐私投影/绑定异常、模型传输或里程碑不一致均由现有逻辑中止。
- 任一臂发生异常，保存既有原始资产和费用，再写整个 G2 阶段 STOP；B2 未运行则明确保留。失败请求与未知费用不能按零成本删除。已有目录不得重跑；完整目录仅可核验读取；中断目录触发停止。进程硬终止遗留 active.lock 也禁止自动恢复。
- 完成 B1/B2 只表示技术执行结束。报告前仍需逐轨迹独立研究复核用户遵循度、官方评价和反馈解释；不因原生 reviewer 未报错就默认语义合格。

## 记录与可陈述结果

逐例报告 S0 和候选的实际轨迹、官方/结构评价、原生诊断、B2 自己原始池的准入去向、共同隐私投影后的实际反思输入、候选文本/哈希、官方回调终点、最佳候选及所有角色费用。拒收建议和 gate 理由留在宿主审计材料，不回灌 acting/reflection。

**两臂基线和诊断池均可能不同。** 这里只能描述各自的执行路径、建议如何进入候选及各自结果；B2 的“保留/拒收”仅与它自己的原始池比较，不能拿 B1 的另一组诊断当匹配反事实。即使 B2 通过而 B1 失败，也不能推导 gate 的净效应、平均成功率提高或泛化。顺序/时间效应、同模型角色共享偏差和已知开发集选择均保留。

有价值的负结果包括：全 S0 跳过、反思自行忽略问题建议、准入不改变输入、候选被拒绝、子轨迹或评价异常、预算先耗尽。工程闭环成立与科研正结果是不同结论；本阶段不保证方向。

## 执行接口（需先实际登记，不在本实现回合运行）

```powershell
.venv\Scripts\python.exe -B -X utf8 scripts\register_subscription_g2.py
.venv\Scripts\python.exe -B -X utf8 scripts\run_subscription_g2.py --arm B1
.venv\Scripts\python.exe -B -X utf8 scripts\run_subscription_g2.py --arm B2
```

原始本地输出为 `results/g2/g2-retail-subscription-http-case-v1/`；登记和阶段入口的导入均不会调用模型。
