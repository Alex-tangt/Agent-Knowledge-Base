# #86 独立安装 R2：安装通过，标准条件 native 首次启动 FAIL

冻结实现为 **9252d5281ab69ad418c76e1672af4dc6fbdff9e5**（#89 修复），原预注册 **5fbeea837b504c8802ddb739e17ec47092da1fea**，见 [PREREGISTRATION.md](PREREGISTRATION.md)。实验写域仅本目录，外部 fixture 明确授权为 `C:\Users\Tan\.mb86\r4`。未修改实现、正式 runtime/服务/数据/配置、注册表或宿主；未恢复或调用 AOCI。

**结论仍为 FAIL，不能关闭全部独立部署验收。** 两个全新目标均完成真实 pinned 依赖和产品包 pip 安装；但普通环境的首次 native 启动在原 300s 上游窗口仍未健康，gateway 未启动，读写/MCP native 行为未验证。安装器 wheel/default/fresh 修复有独立成功证据，完整产品可用性尚未通过。

## 判据结果

| 判据 | 结果 | 实际依据 |
|---|---|---|
| 1 wheel inventory / METADATA / entrypoint / RECORD / source | PASS | 20 个 wheel 条目；15 个包资产与 export bytes 相同；16 个 tracked 文本含 pyproject 的 Git blob/archive raw bytes 有 CRLF 差异，规范化相同 |
| 2 独立 launcher 的真实 wheel 安装 | PASS | `pip --no-deps --no-index --ignore-installed`；本包 origin 为 launcher site，dependencies 通过明确 `.pth` 只读复用 formal |
| 3 default 自动沿用 PEP610 artifact | PASS | 不存在的 d/runtime 真实创建；完整依赖/包 pip；source 与所有 distribution/module origin 位于目标 site |
| 4 adopt→显式 wheel/fresh 切换 | PASS | 不存在的 f/runtime 真实创建；完整依赖/包 pip；home/model/ports/Unicode+CRLF marker 保留 |
| 5 pins / 全新目标隔离 / pip check | PASS | d/f 各 175 distributions、173 active constraints 全匹配；无 formal provider `.pth`、无 system-site-packages；两目标 `pip check` 均退出 0 |
| 6 仓库外两 CWD 的模块与 help | PASS | launcher/default 在 TEMP 和 System32 导入全部 12 模块，console/module/MCP help 均实际通过 |
| 7 正式 runtime 只读 pip check | PASS | `No broken requirements found.`，没有安装、卸载或改正式 runtime |
| 合成 HTTP + 实际 SDK1.30 stdio 协议 | PASS（合成） | 四工具/schema/get/list 的协议探针；不据此声称 native 语义通过 |
| 原 R4 native 启动 | FAIL（方法限定） | 验收额外 `PYTHONDONTWRITEBYTECODE=1`，embed 健康、upstream 未就绪；原报告保持原样 |
| 标准条件冷 native 启动 | FAIL | 去掉额外变量、无预编译、f 从未启动、SDK pyc=0；同 wheel/原300s，upstream 未就绪，out/err 空 |
| native append / reader get+hash+list+search / 401+403 / native stdio | NOT RUN | readiness 失败，未写 note、未初始化 MCP、未发生检索或 LLM/总结生成 |
| 标准条件自有进程归属清理 | PASS | 保存 stop 前 embed/upstream PID/birth/完整 home-bound command；stop 退出 0、records={}、三个 fixture 端口无残留 |

launcher **复用** formal dependencies，不能称为全新依赖栈；两个目标 d/f 则是实际新 venv 与真实 pip，未复制 provider/cache 文件代替安装。已有 pip 下载缓存可以参与真实 pip。

## 原生失败与诊断边界

标准条件预注册方法提交为 **f68c7c46257cf8663f83672c594e0ee4c5468e15**。f 的 data 顶层只有 fresh 保留 marker，上游工作区未初始化；有限诊断均 `-B`，没有预热其 bytecode。标准条件 manager start 退出 1、314.52s（包含 embedding 启动及上游原300s），整个 probe 331.88s；stop 退出 0、12.29s。embedding 健康，upstream/gateway 未健康。原 R4 native/results/results-before-fresh 三份报告的字节哈希均未变，marker 也保留。

[normal_native_result.json](normal_native_result.json) 保存独立标准条件结果，[normal_cleanup_evidence.json](normal_cleanup_evidence.json) 保存精确进程归属与清理证据。原 R4 失败在 start 返回前，缺少其当次 partial-record 副本；后加脚本不能补称旧证据完整。标准条件这次确实保存了 stop 前 partial records。

[IMPORT_DIAGNOSIS.md](IMPORT_DIAGNOSIS.md) 预先比较四个可证伪假设。三环境全部已安装版本差异为空、五个关键包源码 RECORD digest 相同；formal 有完整 SDK pyc，新目标没有。25s importtime/faulthandler 探针定位到大量 generated API 导入，全部未完整导入；其中两个诊断 child 的 3221225477 退出状态仅属于带限时 instrumentation 的 probe，不能宣称普通产品 crash。没有测出完整 import 时间、精确缓存收益或最终根因；未改产品 `--no-compile`、未增加 timeout、未加 stub/fork、未删除依赖或自动重试。

## artifact 与保留资产

实际 wheel：`C:\Users\Tan\.mb86\r4\wheels\agent_memory_base-0.1.0-py3-none-any.whl`。

SHA256：`e7e2dfba47ccc65e47e064f9fadde7b8e9e2d0a6496424122c83f96029b23d03`。冻结 archive SHA256：`9cc879c8a40ebcc6b12aef83cac59d7eb36a7966891ed40f319c77f9d21f0708`。

所有失败分别保留，不抹去或用后续安装成功覆盖：

- `store/run-20261012T000202Z`：脚本过度清空既有 loopback 代理，官方 llama wheel 下载超时；[initial_environment_probe.json](initial_environment_probe.json)。
- `store/run-20261012T000903Z`：真实 pip 在 pywin32 完整路径长260处 WinError3；[platform_install_failure.json](platform_install_failure.json)。
- `store/3`：真实 pip 越过 pywin32，但更长 cloud SDK 文件路径失败；[short_path_platform_failure.json](short_path_platform_failure.json)。
- `C:\Users\Tan\.mb86\r4`：wheel/export、launcher l、default d、fresh f、原 FAIL/continuation 和全部原始日志保留；[results.json](results.json) 仍 overall FAIL，[native_start_failure.json](native_start_failure.json) 为原失败副本。
- `r4/diag-20261012T010718Z` 与 `r4/diag-census-20261012T011127Z`：有限纯 import、首版及补充 census；[import_diagnosis.json](import_diagnosis.json)、[complete_import_census.json](complete_import_census.json)。首版 volc 单目录 census 的零计数不采信，补充已覆盖全部多顶层目录。
- `r4/normal-native`：标准条件独立 result/native/manager 日志及 stop 前 process records；原 R4 报告不覆盖。

Windows `LongPathsEnabled=0` 下深 home 实际安装失败；外部短 home 事前全部175份 RECORD路径上界197、无>=250条目，真实 pip 成功。不能因此宣称任意深 home 支持。POSIX、真实第三方宿主会话、登录启动与其它地图均不在本轮验收范围。

## 已执行命令

从 managed worktree 激活主树 venv 后，按已提交方法依次执行（现有 fixture 会被脚本拒绝覆盖，以下为历史命令记录）：

```powershell
. 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\Activate.ps1'
python experiments\memory-base-package\recheck-2026-10-12\run_recheck.py --asset-root C:\Users\Tan\.mb86\r4
python experiments\memory-base-package\recheck-2026-10-12\continue_fresh.py
python experiments\memory-base-package\recheck-2026-10-12\diagnose_import.py
python experiments\memory-base-package\recheck-2026-10-12\diagnose_import.py --census-only
python experiments\memory-base-package\recheck-2026-10-12\normal_native.py
```

原始 pip/服务日志留在保留资产中，安全摘要和脚本仅提交本目录。凭据只读取本票 d/f namespace，值不输出；证据审计见 [evidence_audit.json](evidence_audit.json)。停止仅用本票 home manager 的 PID/birth/完整 command 归属，不按进程名清理，不删除任何 store。
