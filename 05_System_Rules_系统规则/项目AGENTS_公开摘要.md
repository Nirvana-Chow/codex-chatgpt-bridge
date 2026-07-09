# 项目 AGENTS 公开摘要

更新时间：2026-07-09 15:37

Obsidian-Codex 的本地完整规则仍以本机项目 `AGENTS.md` 和 Obsidian Skill 为准；公开 bridge 只提供安全摘要。

- 每轮先做配额预检和任务分档。
- 每次真正写回 Obsidian、更新规则或更新后台表后，都要检查受影响报告是否滞后；日报、周报、月报、季度报、半年报和年报按当前日期自动确认是否存在并更新。
- XDB 表格按手机竖屏优先验收：第一列是打开按钮，第二列是人类可读名称；后台 ID、路径和排序字段默认隐藏。
- GitHub bridge 是 ChatGPT 可读的公开安全上下文，不是完整备份。
- 工作、财务、朋友/人名和敏感具体事项默认只留在本地 Obsidian 与后台数据。
- 只有用户当轮明确点名的主题包才能进入 GitHub bridge；本次只上传训练计划上下文。
- 公开同步前必须重建 bridge、做敏感扫描、检查 git 状态，再 commit/push。
- 如果公开 bridge 与本地 Obsidian 冲突，以本地 Obsidian 主库和 `Dan_后台数据` 为准。
