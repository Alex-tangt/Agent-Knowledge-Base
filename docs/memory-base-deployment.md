# 正式个人记忆基座部署

本阶段交付一个运行包 `memory_base/`，复用 OpenViking 0.4.23，不启动旧 `memory_agent` 引擎。当前支持总结与笔记的追加、搜索、读取；项目来源新鲜度和偏好升级仍按 ADR-0032 / #78 的后续边界处理。

## 安装和运行

需要 Python 3.12。Windows 在仓库根目录运行：

```powershell
.\install-memory-base.ps1 -Python <Python3.12可执行文件>
```

本轮现场交付与验收针对 Windows，默认使用系统凭据管理器，不将访问密钥写入应用配置。安装固定 OpenViking 0.4.23、SDK 0.1.13、MCP 1.30.0、llama-cpp-python 0.3.36，并固定已验证的传递依赖；原生依赖使用作者提供的 CPU wheel。`install-memory-base.sh` 已保留 POSIX 薄壳，但首次创建上游身份后的凭据持久化尚待补齐，Linux/macOS 部署未完成验收。

默认状态目录为 `~/.agent-memory-base`；专用 Python 位于该目录的 `runtime/`。安装后使用它执行：

```powershell
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base start
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base status
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base logs
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base stop
```

`restart` 重启所管理的服务；`install --fresh-runtime` 选择新的专用环境，保留同一状态目录里的数据与身份。`--home` 可指定另一个部署目录。生命周期命令校验 PID、创建时间和启动命令，不接管占用端口的其它服务。

运行包含三个仅监听本机的进程：OpenViking 8786，进程外串行嵌入 8796，统一接入服务 8788。`status=ready` 要求三个受管理进程均健康。OpenViking 显式采用 key 模式、开启账户 ACL，注册密钥以哈希保存；宿主凭据从系统凭据管理器注入进程环境。手机只连接原有 Bubble 后端 8787。

## 宿主接入

MCP 统一入口为：

```text
<专用Python> -m memory_base.mcp_stdio --profile <宿主名> --home <部署目录>
```

已登记身份名为 `codex`、`dsh`、`opencode`、`agent-bubble`（writer）及 `reader`（只读）。接口只暴露 `memory_add`、`memory_search`、`memory_get`；不向普通宿主暴露覆盖、删除或管理工具。HTTP 对应 `/api/memory/add`、`/search`、`/get`，与 MCP 共用实现。

Codex 可通过其本机 `codex mcp add personal-memory -- <专用Python> -m memory_base.mcp_stdio --profile codex --home <部署目录>` 登记。DSH 的实际入口为 `~/.dsh/profiles/desktop/cordis.patch.yml` 中的 `@deepseek-ai/dsh-mcp-client` 插件，以 `insert` 插入一条 stdio 配置；opencode 在其 `mcp` 配置登记同一入口。配置里只有路径与身份名。宿主规约源文件为 `memory_base/skill/SKILL.md`。

本机注册复跑脚本为 `experiments/memory-base-live/configure_hosts.py`（参数见 `--help`），会备份宿主配置、登记三个 MCP 宿主、落位规约，并补上 Bubble 三个非密钥字段。它保留已有配置，只禁用 opencode 的旧 `memory-agent` 注册。MCP 宿主在新会话生效，Bubble 通过自身 `npm run host:stop` / `npm run host:start` 生效。

Bubble `.env` 只增加 `MEMORY_BASE_URL`、`MEMORY_BASE_PYTHON`、`MEMORY_BASE_HOME` 三个非密钥字段。launcher 调用凭据提供器，把返回 token 放入后端进程环境。首次成功总结原样保存；普通追问不自动新增条目；用户要求保存笔记时可使用 pi 写入工具。保存失败保留本地总结并给出重试状态，重试不再调用模型。Bubble 细节见其 `docs/knowledge-base-integration.md`。

## 数据和边界

正文保存在 `viking://resources/captures/<请求地址哈希>/content.md`，同目录 `capture.json` 保存身份、时间、来源、请求及正文哈希。地址由身份和 request_id 派生；同一请求内容变更返回冲突，不能覆盖原文。并发保障以正式部署的单 gateway 进程为边界。

成功响应的 `index.status=pending` 表示正文已保存但派生索引尚未就绪；重试同一请求会再次尝试索引。不可用为明确错误，不能解释为没有记忆。此增量不实现项目文件就地映射、自动来源更新、偏好全局升级或完整产品化裁剪。

最终运行证据与复跑方法见 [真实接入验收](../experiments/memory-base-live/README.md)。安装和接口测试通过不替代真实宿主验收。
