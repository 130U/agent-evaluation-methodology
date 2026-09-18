# 2026-09-18 发布清单

## 当前交付

- [与项目截图一致的项目卡](../projects/tau-bench/README.md)
- [ICFA 完整文字报告](../core/04-icfa-report/README.md)
- [2026-09-17 封存研究材料](../core/03-tau-feedback-evidence/README.md)
- [代码、来源与公开复现材料](tau-feedback-evidence/README.md)

封存导出包中的 202 份项目文件逐字节迁入本仓库，原首页和原仓库清单不迁入。封存材料的历史状态、标题和实际日期保持不变；当前标题与展示以首页、当前项目卡和 ICFA 文字报告为准。

原导出清单 SHA-256：`992c16deae870d2614d4cccbcbff6e25a75507c42032822242e29059fa4896db`。清单中的 `derived_local_overlay_not_published` 描述 2026-09-17 导出时点，不是当前仓库的发布状态。

当前版本的保留文件哈希、新增展示文件范围及移除路径见[机器可读发布清单](../docs/repository/publication-2026-09-18.json)。

## 已移除的旧专题

移除旧 ALE 核心交付、基础解读、1,000-task 设计、项目入口、报告构建脚本、研究压缩包和采访/字幕材料。旧导航决策由 ADR 0002 替代，Git 历史保留。

两份 `auxiliary-interface-research/` 文件按原字节保留。未发布原始订阅日志、账号资料、模型缓存或完整本机研究目录。

## 验证口径

封存公开包此前完成 373 项隔离组件测试，见[原验证报告的公开摘要](../docs/repository/export-validation-2026-09-17.json)。本次发布以逐文件哈希核验其代码、测试和结果未变，不将迁移表述为新增模型实验。仓库结构与链接在本次发布中重新检查，在线校验以对应 GitHub 提交的 Actions 结果为准。
