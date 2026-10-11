> **已停用（owner，2026-10-12）**：以下内容为历史接入记录。MCP、提交钩子和后台同步已关闭，脚本已改为不执行工作的停用入口。开发使用实际源码与必要文档；恢复需 owner 明确批准。

# 提交后同步 AOCI

本仓库 `.githooks/post-commit` 调用 `scripts/aoci-commit-sync.ps1`，在后台用独立 Codex CLI 的 **gpt-6-luna** 同步 AOCI。模型使用当前 CLI 的认证；不从桌面聊天借用模型入口，也不自动切换模型。

Windows PowerShell 5.1 或 pwsh 均可运行。安装只设置本仓库的 `core.hooksPath`，会拒绝覆盖已有其他 Git hooks。脚本和提示词需要随仓库保留；新 clone 要再次安装。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/aoci-commit-sync.ps1 -Action Install
# 已有支持 GPT-6 Luna 的 Codex profile 时：
# ... -Action Install -Profile <名称>
```

安装后 `git commit` 只入队并启动隐藏的后台进程，提交无需等待模型和扫描完成。后台等待业务工作区干净，再等待默认 120 秒静默期；新的提交或未提交业务改动会延后执行，连续提交合并处理。`-Action Install -IdleSeconds <秒数>` 可配置静默期。生成的三个 `aoci*.txt` 及 `.aoci/` 可以保留未提交变化，其余 Git 可见变化（包括 `AGENTS.md` 和未跟踪文件）都会阻止开始同步。排他文件锁保证本钩子同一时间只有一个同步进程。

开发会话收尾按 `AGENTS.md` 入队，独立任务负责维护。已启动后继续开发仍可能改变绑定，AOCI 的证据绑定与 CAS 检查保持有效；脚本最终也检查 HEAD 与业务工作区，变化时保留请求，不能宣称对齐。该门禁改善触发时机，不提供运行中的独占工作区。

读取按 `AGENTS.md` 的 owner 策略及 `docs/aoci-read-policy.md`：日常开发和独立同步都使用任务相关条目与源码，取消新会话及上下文压缩后的自动全量 Overview；完整总览由用户明确触发。同步仍读取候选绑定的完整源文件并完成所有批次，最终验收继续以实际 Verify / Check / Guide 为准。修改读取策略不表示现有索引已对齐或底层扫描已加速。

```powershell
# 查看最近状态和日志目录
powershell -NoProfile -File scripts/aoci-commit-sync.ps1 -Action Status
# 请求同步或重试失败请求（无其他 worker 时在前台执行）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/aoci-commit-sync.ps1 -Action Run
# 停用提交触发（保留脚本与运行记录）
git config --local --unset core.hooksPath
```

本机绝对可执行路径、可选 profile、请求队列、Codex 输出和验收结果在 Git 已忽略的 `.aoci/commit-sync/`。状态只有通过实际 Verify / Check / Guide 才成为 `aligned`；模型认证失败或治理未通过会成为 `failed`，保留请求供下一次提交或手动重试。同步只写认知与治理资产，结果留在工作区，不自动提交，因此不会制造递归 commit。

`Run` 也遵守业务工作区门禁。已有 worker 占锁时，请求留在队列中，由该 worker 处理；命令成功退出可能只表示已排队，实际完成以 `Status` 的 `aligned` 和验收日志为准。

**认证验证（2026-10-11）**：桌面附带 CLI 0.147.0 拒绝本机 Plus 登录调用 Luna；独立安装官方 npm 包 `@openai/codex@0.162.1` 后，同一 Plus 登录的 `gpt-6-luna` 无工具冒烟成功，返回 `LUNA_OK`。本机同步设置已绑定独立新版 CLI，不修改桌面安装或全局 PATH，无需 API 密钥。新机器需确认自己的模型可用性；脚本保持指定模型，失败如实记录。

本次诊断发现 AOCI rc20 的近期 Overview 读取耗时约 190–304 秒，同期正式索引摘要未变；Check 一次约 96 秒。这些底层耗时尚未修复。后台安排与避免重复最终验收能减少开发被打断，不等于扫描或完整认知传输本身已提速。

本地隔离集成测试：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/tests/aoci-commit-sync.tests.ps1`。使用临时 Git 仓库和本地替身程序，不调用模型，不计入本仓 CI。
