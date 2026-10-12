# #86 R5：全新 wheel 安装与默认首次运行

冻结产品：`45296a58fe2559e0b92f4af851c018b56148865a`。预注册：`096a051103efed7b85092445988ed8c0af163456`。本轮验收 PASS；没有改产品、正式运行、旧失败证据或服务超时。

wheel SHA-256：`baa5877bc55a834f7abf16c833d9e869c0768624efdd63de9ad1ef8b2768bfde`。资产原件保留于 `C:\Users\Tan\.mb86\r5`（source、wheel、独立 launcher、d/f runtimes、合成数据、完整日志）。源码由冻结 git archive 导出；逐 blob 声明 CRLF 归一化边界，wheel 与导出文件、两个安装产品文件均逐字节一致，RECORD、metadata、PEP610 本地来源和依赖约束已校验。

| 验证范围 | 实际证据 |
|---|---|
| 默认新建 runtime（隔离 `--home d`） | 真实 pip 全栈安装与自动 SDK 准备，175 个 own-site distributions、173 条有效约束，无 formal provider/system-site/stub；pip check 成功 |
| 安装期间实际自动准备 | 默认与 fresh 各自新 runtime 的准备子进程 PID/birth/命令被持续观察，Lark 10727 源文件/10727 缓存；首次启动前 d/data 与进程记录均不存在。没有手动编译或预热 |
| 默认首次 native | 只调用 `memory-base start --home d`，未传 timeout；安装包默认 180 秒/组件，实际总启动 168.1306 秒、退出0、三服务 ready |
| 原生行为 | 12 条检查：Unicode/CRLF 原文/hash/revision、同 request_id 重试幂等、401、reader 写403、scope403、stale revision409、search/list、实际 MCP SDK 四个封闭工具及 reader get/list、清理 |
| 显式 fresh | f 先真正采用 d 的 own-site runtime，随后 `--wheel ... --fresh-runtime` 安装第二完整新栈；175 distributions/173 constraints，home/model/ports/合成 marker 保留，pip check 成功 |
| 仓库外 cwd | launcher/d 的两个外部 cwd 模块/import/console/module/MCP help 已原流程验证；f 追加两个外部 cwd 的8条只读检查成功 |
| 清理 | 默认 stop 18.4724 秒、退出0；再次独立检查记录 PID/birth/command 无自有进程存活，62925/62926/62927 关闭，processes.json={}；f 从未启动 native |
| 独立原件/安全审计 | 物理 capture 文件 CRLF 字节 hash 一致；两个完整产品文件再次逐字节匹配 wheel；新 home 的13个实际凭据按 UTF-8/UTF-16 扫描归档证据，未发现明文 |

实际 append URI：`viking://resources/captures/b689ca3d68176b13a21d9bd17e44bf1bac3a6b345b2a1ab6ab6456b6f102bba6/content.md`。原文字节 SHA-256：`0c5aae91f3e9e347c564fac699d63e93e8cd3bf4332bde8edcf8b96cfaa17b50`。只写合成测试条目，不调用 LLM 或生成付费总结。

边界和方法纠正：

- Windows/Python 3.12、短隔离根、机器已有公共 pip cache/无凭据 loopback proxy和本地 GGUF；不是全新机器或模型下载验收。机器 LongPathsEnabled 仍为0，不声称深 Windows 路径问题消失。
- 初始 launcher 借正式环境的只读 provider（-B 的 Python 调用避免隐式缓存写入）；launcher 不算新依赖栈。真正 d/f 使用自己的全栈 site-packages，并未借正式 SDK 缓存。准备过程观察到的基础 Python binary 是 Windows venv 的标准子进程；实际 sys.prefix/分布根仍属目标 runtime。
- 原 `default_pre_native.sdk_counts.volcenginesdk` 指向不存在的单一目录，0/0 是计数方法盲点，不证明其他 SDK 没有缓存。原报告保持原样；独立审计实际 `volcenginesdk*` 的138个目录/26461源文件，d native之后472缓存、f0缓存。该后验计数不回填首次启动前状态。
- 自动准备的观察窗口约300秒（d）/291秒（f），来自轮询时刻，不当作编译函数内部精确耗时。原始时间串携带明确时区偏移。
- 四工具 synthetic HTTP/MCP 协议与真正 OpenViking native 证据分别记录；不把 stub 当 native。f不重复 native 全流程，未验证真实登录触发、第二实体设备或 POSIX 凭据持久化。
- 原 R1–R4/normal-native 的失败与 #91 的实验300秒结果保留；R5不覆盖它们。本轮结果来自一个原始执行（session31068已经终止），使用额度间隔后只做离线审计和未覆盖的 f 只读 import/help，没有重启安装/native。

`results.json`、`native.json`、完整日志和 `final-audit.json` 是安全原件副本；`evidence-manifest.json` 给出每个归档文件与资产原件的 raw byte hash。可复跑方法：先选择另一个明确授权且不存在的新短资产根并更新预注册，不能覆盖 r5。
