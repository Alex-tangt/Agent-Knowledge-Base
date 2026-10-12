# 剩余真实设备与宿主验收

2026-10-12，待执行。对应[交付任务板](memory-base-delivery-plan-2026-10-11.md)中跨设备、真实登录触发和 DSH/opencode 实际对话的缺口。已有 Windows 独立安装、原文、实时项目、完整恢复和 Bubble/Codex 接入证据继续有效；本页提供取得缺失证据的步骤，不把步骤写好当作验收通过。

## 第二台设备读写互查

使用实际手机或另一台设备，经已有私网访问原 Bubble 后端。桌面另开 Codex 记忆会话；两端必须连接同一部署，不创建第二个数据根。ADB 未连接不等于手机一定离线；验收依据是真实设备操作与服务回执，不能用同机浏览器标签替代。

1. 在手机 Bubble 对话明确要求：“请保存这条笔记到知识库”，提供两行不含私人信息的正文，例如 `部署验收标记：phone-<本次唯一标记>` 与 `第二行：原样保存。`。记录工具调用参数中的 request_id，以及成功回执的 URI、revision/content_hash 和索引状态；回执本身不包含 request_id。
2. 在桌面另一宿主用 `memory_list(scope="captures")` 定位，并以该 URI、预期 revision 调用 `memory_get`。核对逐字正文与回执哈希；不把 `search` 暂无结果解释为原文丢失，索引 pending 和读取结果分别记录。
3. 桌面明确保存另一条 `desktop-<本次唯一标记>` 笔记，手机从原 Bubble 会话以外的新会话读取；核对新的 URI、版本与正文。不要通过复制正文进新会话替代从共同服务读回。
4. 记录写入成功到另一端第一次读回的观察时间，以及 search 何时命中。人工操作/刷新测得的是包含反应时间的可见延迟上界，不能声称精确后端时延。未设定 SLA，不编造“低于某秒”门槛；保留实测值和索引状态。

成功判据：实际两设备、两次相反方向的写读、相同服务与内容哈希，以及可见延迟记录。成功保存后的验收笔记按当前追加合同保留，不使用未经交付的删除工具。

## Windows 实际登录启动

在下一次正常注销/登录时验证，不为验收强制注销正在工作的用户。先检查本部署的当前用户启动项已登记，登录后先做只读检查，不提前执行 `start`、`restart` 或手动启动该 Run 命令。

```powershell
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base autostart --mode status
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base status
```

记录新的实际登录时刻、启动项归属、受管理服务的创建时间与就绪结果，并通过 MCP 读取一份既有原文核对 revision。若服务未就绪，先保留本部署日志与状态，再按[部署说明](memory-base-deployment.md)恢复服务；恢复后的手动启动不能算登录触发成功。当前用户 Run 只覆盖用户登录后的启动，不承诺无人登录时的系统服务启动。

## DSH 与 opencode 实际会话

2026-10-13 补验：[有限 OpenCode 本地模型实验](../experiments/memory-base-opencode-dialogue/README.md)已取得 3B 实际 list/get 与当前 README 正文/hash 一致的证据，0.6B 未 get 的失败保留。该会话未传 expected_revision、最终文字漏报 path/hash；未读取 capture，也未使用日常默认商业模型。以下完整两宿主及共同 capture 判据继续待验，不把有限通路证明扩大为完整规约通过。

分别打开实际使用的 DSH/opencode 新会话，让其加载已经登记的 personal-memory MCP 与规约。使用该宿主实际配置的模型和工具通路；合成 SSE/直接 SDK 探针、配置存在和 CLI help 均不能代替本项。

向两个宿主分别提出：“请通过 personal-memory 在 `projects/agent-kb` 范围找到当前 README，读取原文并报告逻辑路径与 revision。”检查会话内实际工具调用，核对返回的字节 SHA256 与本机当前 README；不得只依据模型口述“已读过”。再从 `captures` 列举并读取一份已有回执，确认同一 URI/revision；不生成新文章或视频总结。

成功判据：两个真实宿主各自新会话中的工具调用与响应、正确范围/逻辑路径/版本、当前项目哈希及共同 capture 读回。若模型未调用工具或宿主报错，记录实际缺口；不要把工具配置成功标成对话成功。

## 证据与完成状态

验收原始会话和必要设备信息留在本机，仓库仅记录宿主/设备类别、时间、命令、状态码、URI、哈希和范围，不提交凭据、私有地址、原文全文或完整个人会话。失败原件与成功补验分开；新证据进入[真实接入记录](../experiments/memory-base-live/README.md)，再更新任务板。

以上三项未取得证据前，整个产品目标继续未完成。没有设备或无法取得正常登录/宿主会话时明确保留缺口，不能通过增加同机模拟次数消除它们。
