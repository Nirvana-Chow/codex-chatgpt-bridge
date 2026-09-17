# Read-only MCP：第一阶段运行与验收

更新时间：2026-09-17。当前版本仅为合成数据接入实验。任何本地测试通过都不代表普通 Chat、iPhone 或真实数据接入已通过。

## 用途与边界

现有 GitHub 连接读取已交付的知识与快照；新增 MCP 的未来职责是补充本机当前来源。第一阶段只验证同一组查询接口与 ChatGPT 接入入口，不读取任何实际 Apple、iCloud、业务仓库或执行状态，不建立 writer。

当前只支持 UTF-8 Markdown、txt、CSV/TSV、JSON/NDJSON。PDF、Word、HTML 解析和真实 Apple adapter 留在第二阶段；未知格式返回 coverage gap。来源、正文、配置、访问记录和凭据不能提交本公开仓库。`synthetic://` 是测试来源标识，不伪装成可在手机打开的 HTTPS 链接。

## 本地启动

使用固定的 Python 3.13.14、`mcp==2.2.0` 和 `uv.lock`。所有依赖来自 PyPI，SDK 为 MIT。不要通过 `latest` 自动升级。

```sh
uv sync --frozen --python 3.13.14
uv run --frozen pytest -q
uv run --frozen readonly-context-mcp init-lab /path/to/new-synthetic-lab
```

`init-lab` 是操作者显式创建测试数据的命令，不向 MCP 暴露；目标已存在则拒绝覆盖。将 `config.example.toml` 复制到 Git 外的私人配置目录，填入新生成的实验目录。服务只接受 `mode="synthetic"`，没有关闭安全限制或启用真实 adapter 的开关。

```sh
uv run --frozen readonly-context-mcp serve --config /path/to/private/synthetic.toml
```

这是 stdio 服务，不能直接把本机文件路径或 localhost 填入 ChatGPT。服务不会监听公网端口，也不会发起模型 API 请求。关闭 stdin 即结束服务，索引和游标随进程丢弃。

## 查询接口

| 工具 | 行为 |
| --- | --- |
| `list_sources` | 列出4个合成来源、可达性和索引状态 |
| `search` | 正文搜索；query为字面文本，返回授权文档ID和版本 |
| `fetch` | 按ID分页读当前字节；不接受文件路径 |
| `list_reminders` | 全模拟清单发现后按状态和清单筛选 |
| `list_calendar_events` | 带时区窗口，右端不含；默认北京时间今天起7天，最多90天 |
| `get_execution_status` | 只读模拟记录，保留 phase 与 reported_status 区别 |

所有工具声明 readOnlyHint=true、destructiveHint=false、openWorldHint=false。服务端也没有 shell、业务写入、删除、重跑或任务派发实现。所有正文视为不可信资料，不执行文中命令或外部链接。stdio 本身没有用户鉴权；只能接到已验证身份隔离的私有隧道，绝不能用无鉴权公共 HTTP 包装器暴露。

返回来源、authority、观察时间、修改时间、SHA-256版本、freshness、覆盖缺口及下一页。第一阶段的 freshness 只能为 synthetic 或 unavailable，不能冒充 live。全文索引是进程内 SQLite FTS5，可随时重建；每60秒检查，首屏搜索强制重新哈希并二次检查，mtime不作版本依据。中文3字及以上用trigram，短词用字面匹配。

列表默认50条、最大200条；继续分页保留相同筛选条件，依据 `snapshot_as_of` 解读快照。正文每页最多32 KiB，位置单位为Unicode码点；文件变更使旧正文游标返回 SOURCE_CHANGED。列表快照在翻页中保持原集合，新查询才反映新增删除。游标5分钟过期、最多256个、保留快照序列化总预算8 MiB（不是进程总内存上限）；进程重启或淘汰后返回 CURSOR_EXPIRED。

文件最大20 MiB，模拟records最大2 MiB，索引输入预算256 MiB；索引遍历最多10000个条目、深度12、刷新预算20秒。达到边界必须报告 INDEX_INCOMPLETE/具体缺口，不能把未搜索部分当无结果。离线不回落到旧缓存。隐藏目录、常见凭据路径、私钥、符号链接、硬链接、非普通文件拒绝读取；文件描述符相对打开并复核读取前后身份。已知凭据内容模式检测为补充措施，不能当成通用敏感信息检测器。

## 官方安全连接

采用 [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)。Mac只出站连接OpenAI；工具结果仍会进入ChatGPT，不承诺“数据不出本机”。

基线客户端 `openai/tunnel-client v0.0.14`，Apache-2.0；macOS arm64发布包SHA-256：`b540493c5bdbcdbb755700c8e2e16597e28b1569e425007e0f73111047bd6a64`。下载来源为[官方发布](https://github.com/openai/tunnel-client/releases/tag/v0.0.14)，核对发布校验和再运行。包带许可证和SPDX清单；不启用其附带Cloudflare运行路径。

需要本人 Platform 与 ChatGPT 登录、隧道 Read/Manage/Use 权限、专用运行凭据，以及隧道与目标工作区的正确关联。凭据由本人在本地安全录入，不贴聊天、不传命令行参数、不提交仓库；用 `env:CONTROL_PLANE_API_KEY` 引用。没有凭据不读取其他项目的 `.env`、Codex auth.json 或浏览器cookie。无法完成本人隔离即 NO-GO，不退到公共隧道。

在正式创建好隧道且确认权限和可能费用后，使用官方客户端初始化独立profile：

```sh
tunnel-client init --sample sample_mcp_stdio_local --profile readonly-context-lab \
  --tunnel-id '<actual tunnel id>' \
  --mcp-command '/absolute/path/to/.venv/bin/readonly-context-mcp serve --config /path/to/private/synthetic.toml'
tunnel-client doctor --profile readonly-context-lab --explain
tunnel-client run --profile readonly-context-lab
```

启动前检查生成profile只含合成服务路径、官方control-plane、loopback健康端口，且凭据为环境引用；只以前台方式运行，不创建登录项、launchd或Automation。仅在健康、ready后创建ChatGPT连接。开发者模式和隧道角色是独立权限；若账户不支持、需要付费或扩大未授权权限则停止。隧道费用尚未核实，不能承诺免费；普通Chat仍受账户用量约束。

## 入口验收矩阵

每项记录客户端版本、模型、工作区、时间、是否手动启用工具、工具名、参数、来源版本和结果。不要记录密钥或无关会话。

| 门禁 | 操作 | 合格证据 |
| --- | --- | --- |
| 网页普通Chat | 非项目新聊天，自然询问“读取接入测试资料的随机标记” | 调用search/fetch返回实验目录唯一标记，而非复制提示词 |
| iPhone App新聊天 | 网页测试后由本地操作者改probe.md标记，再在手机独立新建聊天查询 | 新值及新版本；不能用手机浏览器或旧聊天续接替代 |
| 工具发现 | 点名、自然措辞、追问分页、无关问题分别测试 | 选对工具；需要每次手选连接须明确记录为manual，不假称自动发现 |
| GitHub混合 | 同一问题读取公开桥README及合成probe.md | 两种来源分别引用，不能把合成资料当用户事实 |
| 身份隔离 | 未登录、错误账号/工作区、撤销后旧会话 | 均不能继续读取；本机stdio测试不替代此项 |
| 离线 | 停前台客户端、断网或休眠，再问最新值 | 明确不可达，不称实时或无待办 |

本地测试覆盖501条分页、Unicode与超长行、同大小同mtime变更、读中修改、旧游标、symlink/hardlink、离线、清单删除中途分页、提示注入文本和源文件哈希不变。真实stdio子进程测试验证协议与6工具发现。它们不验证普通Chat模型是否正确选工具，也不验证iPhone或云端鉴权。

只有上述入口和身份门禁全部有证据通过，才另行进入真实来源阶段。其前禁止将配置指向业务资料；即使配置伪造synthetic标志也不能视为已授权或已验收。

## 回滚

停止本次前台客户端和MCP子进程；撤销本次连接、专用隧道及凭据（仅限本次对象，按平台支持方式）。不操作已有GitHub连接或日终Automation。代码通过PR revert回退，索引可重建，不需要恢复业务资料。配置和证据保留，删除另行授权。

## 已调查的复用候选

`codex-with-chatgpt`核对到`9663b88753e35c76796c5bce000293e0bd22cd9e`（MIT、v0.1.3、带OAuth和测试）。现有工作区分页和搜索契约不能直接满足版本一致性、多来源覆盖；未运行其安装器，不复制源码。选择官方SDK薄适配，固定锁文件，后续更新先回归。
