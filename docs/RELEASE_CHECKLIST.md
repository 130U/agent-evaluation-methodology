# 发布核验

- [ ] 首页、项目卡、文字报告的标题与展示周期一致。
- [ ] 展示周期与实际实验、封存及报告日期分别说明。
- [ ] 研究结论与对应实验终点一致，组件结果不表述为策略收益。
- [ ] 封存公开材料与原导出版本逐文件哈希一致。
- [ ] 公开文件不含凭据、本机绝对路径或原始订阅日志。
- [ ] 所有本地 Markdown 链接可解析。
- [ ] 删除的专题没有遗留为当前导航入口。
- [ ] `python scripts/validate_repository.py` 通过。
- [ ] PR 的实际提交通过仓库 CI。
- [ ] 合并后回读主分支、README、文件树及提交对应的 CI。

仓库 CI 负责结构、文件大小、凭据模式与导航检查；研究组件测试的适用范围见[公开复现说明](../supporting-evidence/tau-feedback-evidence/PUBLIC_REPRODUCTION_BOUNDARY.md)。
