# GEPA 固定版本接入核查

核查日：2026-09-17。前五节为 GitHub 连接器读取官方 REST API 及指定 SHA 的接口核查；第六节记录随后完成的本地源码校验、安装与假 episode 集成测试。**尚未运行真实 GEPA 优化。**

## 1. 可锁定版本

| 事实 | 已核实值 |
|---|---|
| 当前 main 完整提交 | `15ee314f9c7d34ec153b809d401f42f55c4dcd76` |
| 提交时间 | 2026-09-11 06:04:59 UTC |
| GitHub release 列表最新非 prerelease | `v0.1.4`，2026-07-15 14:50:58 UTC |
| v0.1.4 tag 实际 commit | `8b0ce6cd99a234f6b74daf37558a2ac0ce18f975` |
| main 中 pyproject 声明 version | 仍为 `0.1.4`，**不能据此认为等于 release 源码** |
| main Python 要求 | `>=3.10,<3.15` |
| main 必需依赖 | `dependencies=[]`；外部模型接入按实际调用另装依赖 |

[main commit](https://github.com/gepa-ai/gepa/commit/15ee314f9c7d34ec153b809d401f42f55c4dcd76) · [release](https://github.com/gepa-ai/gepa/releases/tag/v0.1.4) · [tag API](https://api.github.com/repos/gepa-ai/gepa/git/ref/tags/v0.1.4) · [固定 pyproject](https://github.com/gepa-ai/gepa/blob/15ee314f9c7d34ec153b809d401f42f55c4dcd76/pyproject.toml)

**建议**：锁定上述 main SHA，并记录本地安装来源/文件哈希，不把 `pip freeze` 的 0.1.4 当充分锁定。该 main 包含恢复运行的已提交修改；如改用 release，重新核对接口，不能混用本笔记。

核查文件及 Git blob SHA：

| 文件 | Git blob SHA |
|---|---|
| `src/gepa/core/adapter.py` | `6f59b20c9156eb97d9c83f688c0471931181e136` |
| `src/gepa/api.py` | `6b09ceb5be336187b65bf8aa67fbb1cee0cbcad0` |
| `src/gepa/strategies/instruction_proposal.py` | `3712c97e07054d3c51661337082ae4620bb8548e` |
| `src/gepa/proposer/reflective_mutation/base.py` | `0a5bce62af1e891552a235fdf16279fa55aaf184` |
| `src/gepa/utils/stop_condition.py` | `4e04804de394f57cde9e2936994d2a9f6d768410` |

## 2. 真正接口，不使用自拟参数名

`GEPAAdapter` 两个必要方法：

```python
def evaluate(self, batch, candidate: dict[str, str],
             capture_traces: bool = False) -> EvaluationBatch: ...

def make_reflective_dataset(self, candidate, eval_batch,
                            components_to_update): ...
```

`EvaluationBatch` 包含 `outputs`、`scores`、`trajectories`、可选 `objective_scores`、可选 `num_metric_calls`。长度按 batch 对齐；`capture_traces=True` 时必须提供相应轨迹。引擎不解释 trajectory，反馈内容由 adapter 决定。minibatch 用分数总和比较，完整验证用均值跟踪候选。[固定 adapter.py](https://github.com/gepa-ai/gepa/blob/15ee314f9c7d34ec153b809d401f42f55c4dcd76/src/gepa/core/adapter.py)

反思记录按 component 返回，例如 `{'strategy': [{'Inputs': ..., 'Generated Outputs': ..., 'Feedback': ...}]}`；可加 source/evidence 字段。B1/B2 共享 `evaluate`，只切换反馈处理；不实现自定义 `propose_new_texts`，这样候选生成仍为同一原生反思流程。

实际 `gepa.optimize` 主要参数及本项目建议：

| 参数 | 核实默认/约束 | 建议 |
|---|---|---|
| `seed_candidate` | 非空 `dict[str,str]` | `{'strategy': 初始策略段}`，政策不可变 |
| `adapter` | 提供后不得同时传 task_lm/evaluator | 一个 runner/evaluator，共用评分逻辑 |
| `trainset / valset` | valset=None 会复用 trainset | 必须显式独立传入验证集 |
| `reflection_lm` | 模型字符串或 callable | 全组同模型、同参数 |
| `candidate_selection_strategy` | `pareto` | 固定默认 |
| `reflection_minibatch_size` | 默认采样器内回退 3 | 显式写 3；先按预算验证是否合适 |
| `use_merge` | False | 显式 False，控制单模块试验 |
| `acceptance_criterion` | `strict_improvement` | 全组相同，不只给 B2 更严格验收 |
| `cache_evaluation` | False | 先保持 False；缓存另经验证后启用 |
| `max_metric_calls` | evaluator 评估计数上限 | 不能代替全部模型调用/成本上限 |
| `stop_callbacks` | 支持自定义 StopperProtocol | 外部总账停止器；完整批次预留资源 |
| `max_reflection_cost` | 只限定反思 LM 成本 | 不是 agent/user/judge 总成本 |
| `run_dir` | 有 state 时自动续跑 | 按 arm/seed/manifest 分开，禁共享 |
| `write_agent_state` | False | 可 True 便于审计，但保护测试内容 |
| `seed` | 0 | 显式多个种子；不视为模型确定性保证 |

[固定 api.py](https://github.com/gepa-ai/gepa/blob/15ee314f9c7d34ec153b809d401f42f55c4dcd76/src/gepa/api.py) · [stopper](https://github.com/gepa-ai/gepa/blob/15ee314f9c7d34ec153b809d401f42f55c4dcd76/src/gepa/utils/stop_condition.py)

## 3. 无单独 API key 时的技术边界

已核实 `LanguageModel` 协议为：

```python
def __call__(self, prompt: str | list[dict[str, Any]]) -> str: ...
```

因此从技术接口看，GEPA 反思可用任何经授权、可记录、可隔离的模型调用适配器，并不强制某供应商 API key。**但本次没有证明本机 Codex CLI 或订阅权限可用，也没有证明其成本/模型标识/上下文隔离足以开展实验。**[固定 LanguageModel 协议](https://github.com/gepa-ai/gepa/blob/15ee314f9c7d34ec153b809d401f42f55c4dcd76/src/gepa/proposer/reflective_mutation/base.py)

即使反思接口可用，tau2 的 agent、user simulator、reviewer、NL judge 仍需分别有可控模型通道；一个能生成文字的终端调用不等于所有角色已接好。每次 actor 只接受允许的当时可见信息，模拟用户不能读 reference action，反思器不能读取最终测试资产。不能用带本研究完整对话历史的同一会话充当盲测 actor。

若没有这样的通道：可执行原生环境、受控评分器测试、静态/类型化反馈准入实验；**不可把手写候选、固定假模型或脚本输出命名为 GEPA 模型优化实测**。

## 4. 两个容易污染研究结果的细节

### 默认反思会积累具体事实

默认 meta-prompt 鼓励把反馈中的领域事实写入新指令；这会放大实例 ID、用户偏好或 gold 泄漏。因此先用两组共同的数据脱敏与泛化规则剥离订单/商品/答案标识，再交给反思器。`reflection_prompt_template` 可固定补充“仅修改可迁移策略”，但必须保留 `<curr_param>`、`<side_info>` 占位符，并全组一致。不要只给 B2 更强的反思模板。[固定 instruction_proposal.py](https://github.com/gepa-ai/gepa/blob/15ee314f9c7d34ec153b809d401f42f55c4dcd76/src/gepa/strategies/instruction_proposal.py)

### 恢复与预算不是简单重启

该 main 在恢复时，若 caller seed 不在保存池，会对新 seed 完整验证并加入候选；相同 seed 才跳过重复 seed 验证。因此误复用 run_dir 会混入历史候选与花费。每个 arm/seed 使用独立目录，恢复前核对原 manifest、模型、prompt、任务和预算；记录实际总消费，不能仅依赖 `max_metric_calls`。[该提交 diff](https://github.com/gepa-ai/gepa/commit/15ee314f9c7d34ec153b809d401f42f55c4dcd76)

## 5. 最小接入验收

1. 在本地确认安装文件对应 SHA，保存环境锁；本笔记的远程读取不替代此步。
2. 用一个开发任务验证 adapter 输出对齐、轨迹及原始评价可追溯；异常不被伪造为正常通过。
3. 固定反思器后，跑一个候选生成→真实环境评估→GEPA接受/拒绝；有模型通道才算此步完成。
4. 验证 B1/B2 模型、评分函数、prompt限制和候选选择一致，差异清单只包含注册反馈处理。
5. 总账覆盖所有角色；中断恢复不改变数据/seed；测试目录对优化进程不可读。
6. 对反馈全部被拒绝的 batch 记录 `no_admissible_feedback`，不偷偷退回原始不可信诊断。是否跳过该 batch 或保留中性事实记录需在开发试点冻结，并给所有可比 arm 相同停止/账本规则。

## 6. 本地接入结果（2026-09-17；无模型调用）

### 固定上游与安装

- 下载至 `vendor/gepa` 的完整官方 ZIP 来自 `https://codeload.github.com/gepa-ai/gepa/zip/15ee314f9c7d34ec153b809d401f42f55c4dcd76`。
- ZIP：27,903,621 bytes，SHA256 `2c136bfcbdec58f7160c0f4426bfb897c6ab074b4cfea1072a1afc49b3c684f6`。
- 587 个上游文件、总计 44,951,976 bytes；逐文件用官方 Git tree 中的 blob SHA 与字节数核对，全部相符。`vendor/gepa/UPSTREAM_TREE.json` 保留官方树，`vendor/gepa/SOURCE_MANIFEST.json` 保留源 URL、提交、ZIP 哈希、每文件 Git blob SHA 与 SHA256。没有修改这些上游源文件。
- 上游 AGENTS 要求通过 uv 执行；本机最初没有 uv，先在项目 `.venv` 安装 `uv==0.12.15`，再用 uv 将该目录以 editable、`--no-deps` 安装为 `gepa==0.1.4`。实际 `gepa.__file__` 指向上述固定 vendor checkout。
- 初次 uv 尝试默认用户缓存目录失败，未安装 GEPA；随后改用项目内 `vendor/gepa/.uv-cache` 成功。`vendor/gepa/INSTALLATION_RECORD.json` 保留全部尝试、stdout/stderr、安装前后包版本。
- `.venv` 新增包只有 `uv==0.12.15` 与 `gepa==0.1.4`，已有包版本未变。未修改根 `requirements.lock`；下一次正式环境锁更新应显式记录 GEPA 的 commit/来源，而不是只记 0.1.4。

### 已实现的边界

`src/tau_feedback/gepa_adapter.py` 使用安装后的真实 `GEPAAdapter`、`EvaluationBatch`，没有重建同名假接口：

```python
runner(example: EpisodeInput, *, strategy: str,
       capture_traces: bool) -> EpisodeResult

EpisodeInput(key, payload, reflection_context, protected_literals)
EpisodeResult(score, output, trajectory, feedback,
              protected_literals=(), metric_calls=1)
```

- `candidate` 只能含一个非空 `strategy`，不能通过 candidate 改写政策或工具。真实环境执行、actor 角色可见信息隔离、评分与全角色成本账本由外部 runner 负责；runner 必须把官方 Task 等对象显式转换为 JSON mapping，并且不能把整个 `EpisodeInput` 转发给 actor。
- 逐样本分数须为有限的 `[0,1]` 数字。缺分、NaN、布尔分数、无效结果 schema、系统配置错误会报错；不会偷偷填成功或把不可评分当作正常失败。
- runner 明确抛出的 `EpisodeFailure` 表示已尝试的单例行为失败，例如参数格式错误；该类记 score=0，并在原始轨迹保留 failure code/details。其他异常原样上抛。`metric_calls` 计评估次数，不等于所有角色的模型调用数。
- `capture_traces=True` 必须有轨迹；反思时检查候选哈希、输出/分数与原始捕获结果对齐，避免把旧候选轨迹混给新候选。输入、返回值均拷贝，避免 runner 或过滤器就地修改数据。
- 默认 `arm='B1'` 使用原生诊断反馈。两组共享字段/字面量清理及 `COMMON_REFLECTION_PROMPT`；反思只读通用 `reflection_context`，不传原始 task payload。明确私有字段、ID、实例数值被移除或替换，行动/消息位置保留以便定位证据，原始运行输出仍保存原始 ID 供审计。
- **该清理只验证定义好的字段与字面量边界，不是任意文本的无泄漏证明。** runner 必须完整提供 `protected_literals`，并避免将未标记的特权答案或其改写放进自由文本。未知的语义改写无法由正则证明安全。这是 B1/B2 共同条件，不能被包装成 B2 创新。
- `arm='B2'` 必须显式提供命名的 `feedback_filter(FeedbackView) -> FeedbackDecision`；本模块没有内置语义准入算法。过滤器先接收共同清理后的视图，输出再清理一次；返回 `feedback=None` 时保留 `no_admissible_feedback`，不回退原生诊断。B2 不改变 evaluate 的 runner、分数或优化器。
- 共同清理后的轨迹供外部过滤器定位证据，**不另作为未经筛选的 Trace 字段传给反思模型**。两组的反思记录均只有 `Inputs`、`Generated Outputs`、`Feedback`、`Score`。原始轨迹留在本地 EvaluationBatch；最终输出仍可能启发 GEPA 自行推理，因此不能声称“禁止一切新诊断”。
- 所有组使用同一 `COMMON_REFLECTION_PROMPT`，保留 GEPA 所需的 `<curr_param>` / `<side_info>`；不实现 `propose_new_texts`，继续采用官方反思提案流程。当前 strategy 若已含已知私有字面量，会拒绝反思，避免从 `<curr_param>` 旁路泄漏。

### 可执行例子与验收

`scripts/run_gepa_example.py` 提供最小 `gepa.optimize(...)` 调用。无 `--factory module:function` 时明确拒跑；factory 须配置真实 runner、反思 callable、独立 trainset/valset、正的 metric budget 和全新 run_dir。示例固定共同反思模板、Pareto、strict improvement、禁 merge、禁 evaluation cache。实例 ID 不重叠的检查不替代研究协议的结构/语义分组；`max_metric_calls` 不替代总 token/墙钟预算。

**已执行 32 个测试全部通过**，仅为假 episode 的接口与集成测试：

1. 安装来源、587 个上游文件仍与清单一致；真实 GEPA batch fallback 与 prompt renderer 可调用。
2. 批次对齐、评分/错误边界、策略唯一可改字段、输入/输出不可变、指标调用计数。
3. B1 原生反馈、显式 B2 注入、拒绝后的无回退、过滤器不改评分/输出、共同模板兼容。
4. 任务/运行标识、隐藏答案 canary、反思前后的私有内容拦截，行动位置保留；这些是边界案例验证，不是自然反馈质量实验。
5. 示例参数可绑定真实 `gepa.optimize` 签名；未配置或 train/val 重叠时拒绝执行。

复现命令（项目根目录）：

```powershell
.venv/Scripts/uv.exe --cache-dir vendor/gepa/.uv-cache run --no-project --python .venv/Scripts/python.exe python -B -X utf8 -m unittest discover -s tests -p test_gepa_adapter.py -v
```

本子任务没有调用本地或远端模型，没有生成候选策略，没有运行 `gepa.optimize`，没有产出 Agent 成功率或优化收益。真实 runner、多角色全预算约束及 B2 语义机制仍需分别接入并实验。
