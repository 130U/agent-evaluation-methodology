# Content lifecycle

研究材料按以下状态推进：

1. **Captured** — 保存来源、版本、访问日期与原始上下文。
2. **Audited** — 检查来源权威性、计数单位、权利边界和相互冲突。
3. **Synthesized** — 将证据转化为结论，明确事实、推断、假设和建议。
4. **Reviewed** — 独立检查关键主张、可执行性和遗漏的反例。
5. **Promoted** — 从 `supporting-evidence/` 提升到 `core/`，或由核心材料引用。
6. **Released** — 在明确的 commit/tag 上形成可交付快照。
7. **Superseded or retired** — 保留历史依据，标明替代版本和不可继续使用的原因。

## Promotion gate

材料进入 `core/` 前至少确认：

- canonical source 与 revision 已记录；
- 所有数量说明都标注单位和范围；
- 缺失证据没有被包装为确定事实；
- 权利、隐私、保密与 contamination 风险已检查；
- 关键结论有可复核的依据；
- 与现有核心文件的冲突已解决或显式登记；
- 文件名和位置符合仓库规范；
- repository validator 通过。

## Change classes

### Editorial

不改变含义的错别字、格式和链接修复。可直接修改原文件，但变更记录应说明不影响结论。

### Evidence update

增加来源或改变证据权重。必须固定版本，并解释旧结论是否仍成立。

### Decision change

改变 allocation、gate、RACI、SLA、统计协议或交付定义。必须说明依据、下游影响和迁移方式。

### Source-surface migration

论文、代码、数据集或 evaluator 版本发生变化。更新 `docs/repository/repository-manifest.json`、`docs/repository/evidence-manifest.json`、受影响报告及跨版本可比性说明。

## Retirement

不要静默删除曾支撑交付的核心材料。优先：

- 在文件顶部标记 superseded；
- 链接替代材料与生效日期；
- 说明旧版本不能继续使用的原因；
- 在确认不存在活跃引用后，再移除。
