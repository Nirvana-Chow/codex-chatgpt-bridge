---
title: Codex自动唤醒与额度窗口规划
type: resource
created: 2026-07-09
updated: 2026-07-09 10:16
updated_at: "2026-07-09 10:16"
tags:
  - 资料
  - AI工具
  - 数字资产
  - Codex
project: AI实践与Obsidian系统
status: active
---

# Codex自动唤醒与额度窗口规划

> 更新于：2026-07-09 10:16

#资料 #AI工具 #数字资产 #Codex

## 当前策略

自动唤醒用于在额度紧张时，把 Codex 的 5 小时窗口尽量提前排好，避免真正想用时才发现额度不可用。

手动使用的默认策略也调整为“标准服务档 + 5 小时窗口节奏”：`[CODEX_HOME]/config.toml` 中 `service_tier` 从 Fast/priority 调回 `default`；模型仍是 `gpt-5.5`，推理强度仍按当前用户选择保持 `xhigh`。后续复杂任务不要在窗口刚开始连续启动多个高消耗阶段，优先把一个窗口用来完成一个清晰节点：理解与规划、执行、校验、写回/同步。

当前启用的是每天 4 个动态检查窗口，而不是死等固定点：

- 03:00-05:10
- 08:00-10:10
- 12:00-14:10
- 17:00-19:10

LaunchAgent 在这些窗口内每 10 分钟检查一次。脚本先读取 5 小时额度的自动重置时间，把重置时间向上取整到最近 10 分钟；如果这个时间还落在当前窗口内，就执行本次轻量唤醒。如果已经超过当前窗口的宽限范围，就把本次记为 `miss`，不再临时改当天时间，等下一个窗口继续判断。

选择这组时间的原因是：理想节奏仍然是一天最多 4 个完整 5 小时窗口。正常情况下，03:00 执行后下一次约 08:00；08:00 执行后，12:00 窗口会等实际重置到约 13:00 再执行；后面同理接近 18:00。这样既能吃到 5 小时滚动窗口，又不把晚上 22:00 变成继续使用 Codex 的暗示。

## 执行口径

- 不判断节假日，默认每天都启用。
- 后台执行路径放在 `[CODEX_HOME]/automation/codex-auto-wakeup.sh`，避免 macOS 后台项无法读取 `Documents` 下脚本。
- LaunchAgent 使用 `com.localuser.codex-auto-wakeup`。
- 工作目录固定为 `/private/tmp`，不进入 Obsidian-Codex 项目目录。
- 使用 `gpt-5.4-mini` 和 `model_reasoning_effort="low"`。
- 自动唤醒脚本禁用 hooks，减少无关 hook 报错和额外开销。
- 运行日志看 `[USER_LOG_DIR]/codex-heartbeat-events.log`；这里会记录 `success`、`skip`、`miss` 和失败原因。
- 去重状态看 `[USER_APP_SUPPORT]/CodexHeartbeat/auto-wakeup-state.tsv`；同一天同一窗口成功过就不重复执行，第二天自然恢复到正常窗口。
- stdout/stderr 看 `[USER_LOG_DIR]/codex-auto-wakeup.log` 和 `[USER_LOG_DIR]/codex-auto-wakeup.err`。
- 手动高模型任务先看配额脚本；如果当前 5 小时窗口已经明显消耗过快，优先交付本节点、回显下一步，再等下个窗口继续。


## 任务分档补充

自动唤醒、后台心跳、安装验证和短检查属于低成本档；复杂结构性任务不能靠自动唤醒连续启动。手动任务开始前先判断 `T0` 到 `T4`：`T0` 回 ChatGPT 整理，`T1` 用 Spark / 低成本档，`T2` 用 standard/default，`T3` 才分阶段使用 high/xhigh，`T4` 先确认。

完整规则见 Codex任务分档与模型路由。

## 已验证问题

之前失败过两类问题：

- 中文脚本名在 macOS 后台项里可能被乱码解析，导致 launchd 找不到脚本。
- `Documents/Codex/...` 下的脚本在手动终端可执行，但 launchd 后台进程可能打不开。

当前解决方式是：后台项只指向 `[CODEX_HOME]/automation/codex-auto-wakeup.sh`，项目内脚本仅作为配置副本和维护入口。

## 恢复和排查

如果自动唤醒某次没有执行，不手动改当天时间，先看日志里的原因。当天如果已经错过当前窗口，就等下一个窗口；第二天会回到 03:00、08:00、12:00、17:00 这四个正常检查窗口。

排查顺序：

1. 看 `launchctl print gui/$(id -u)/com.localuser.codex-auto-wakeup` 的 `runs` 和 `last exit code`。
2. 看 `[USER_LOG_DIR]/codex-heartbeat-events.log` 是否有 `start` 和 `success`。
3. 看 `[USER_LOG_DIR]/codex-auto-wakeup.err` 里的错误原因。
4. 先手动测试脚本，再启用定时。

## 关联

- 项目：AI实践与Obsidian系统
- 领域：学习成长
