# obsidian-lifeos Skill 公开摘要

更新时间：2026-07-09 15:37

本地 Skill 负责把用户输入写入 Obsidian 当前状态系统：先读输入、拆事实、维护待办和项目，再由 AI 写自然语言报告。

公开 bridge 只暴露执行方法，不暴露用户事实：

- facts、todos、report_index、input_impact_graph、report_source_map 和 ai_model_usage_log 不上传。
- 工作、财务、人物、朋友、私密日记和敏感具体事项不上传。
- 只上传不含敏感明细的公开规则摘要和用户当轮明确点名的主题包；本次主题包是训练规划上下文。
- 可公开复用的执行方法包括：报告滞后检查、按日期生成周期报告、读后反馈闭环、XDB 手机端列顺序验收和公开白名单同步。
- 需要 ChatGPT 评审具体私密内容时，应在本地由 Codex 生成临时脱敏摘要，并由 用户 单独确认是否提供。
