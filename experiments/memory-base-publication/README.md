# #95 固定发布范围只读审计

范围为 `4dadc407d39b26855a96079ce8561e8983dc4401` tree，以及 `4270f13f418c5069316ba819cec11bb5910dcefc..4dadc40` 的121个未发布提交。后续 Lead 文档/CI修订不属于本报告。未发现当前已知实际凭据、三份原文全文/长片段、所读私有URL的literal泄漏；通用token与URL候选检测无命中。宽赋值扫描保留6处原未决定位，Lead独立只读补验已判为非secret语法/fixture/环境引用；这不构成所有未知secret或整体产品目标完成的证明。

## 冻结、执行与覆盖

先提交方法 `621d3c2`，再提交脚本 `36f5b0d`；首扫描正式Python3.12 `-B`、PID37896、exec10665，38.17秒自然退出0。首报告 [scan-4dadc40.json](scan-4dadc40.json) 原样保留。Lead复核发现首assignment裸名/quoted-key及generic URL缺口，追加方法/脚本 `cb5d1ab`；8个合成正例与2个placeholder/code-expression反例PASS，再仅扫Git对象，PID2144、exec70982，12.64秒自然退出0，补报告 [generic-4dadc40.json](generic-4dadc40.json)。补扫没有再次读取vault、env或HTTP原文。

| 集合 | 文件/unique blobs | 被扫unique blob字节 |
|---|---:|---:|
| 固定tree | 989文件 / 927 blobs | 20,181,200 |
| 固定未发布history | 630 blobs | 6,490,039 |
| 合并后实际逐对象读取 | 1,131 blobs | 两集合重叠，字节不相加 |

Git原始blob经cat-file进入进程内存，没有恢复历史文件。未发布history包含3个AOCI命名blob，均被覆盖；固定tree无AOCI命名文件。未安装、恢复、调用或执行AOCI。原始对象定位见 [evidence-review.json](evidence-review.json)。无扫描活跃handle遗留，无产品/服务生命周期操作。

## 敏感来源与边界

同一正式Python进程比较47个去重的当前实际凭据。来源计数包括：正式vault7、R4默认7/fresh6、R5默认7/fresh6、#94目标7；Bubble env3、memory_agent env1、审计进程credential环境2；经过PID/birth/完整命令归属核对的正式3组件及Bubble2进程环境。来源计数可重叠，不等于去重数；完整来源/数量在首报告。隔离homes没有当前owned服务，因此没有声称读到已退出进程环境。

R4/R5 fresh各缺1个vault条目；未获取该缺失值，不声称覆盖轮换/删除过的历史凭据。三条显式白名单receipt经正式gateway reader只读get，revision及原文SHA256全部匹配，metadata 3/3读取；3标题只作摘要审阅，1个带非公共定位参数的sourceURL在内存核对。没有search、embedding、总结生成或任何外部LLM调用，也没有读取无关用户文件或全量个人语料。

已知literal比较包含UTF-8/UTF-16LE、CRLF/LF及JSON转义；二进制仅做字面字节匹配。没有OCR、压缩包展开、base64/任意编码证明。未知secret无法穷尽。原文只覆盖上述3份已授权receipt，未覆盖整个人语料；标题命中不能自动等同泄漏。

## 候选分类

首报告18个blob定位由11个标题摘要blob与7个赋值候选blob构成。标题定位只在既有 `experiments/memory-base-live/README.md` 的历史/当前版本及 `bubble.json`，报告不复制标题；它们属于已公开验收摘要的owner审阅面，正文和私有URL没有命中。

补扫描为131个blob、411个赋值位置。原候选一律保留，分类脚本和逐位置criterion见 [review_candidates.py](review_candidates.py)、[candidate-review.json](candidate-review.json)：87数值/布尔/null、151代码引用/注解/比较、58指标或库存、20环境变量名、16空占位/语法、42包库存/非credential字段、5环境扩展、1计算表达式、6文字/Markdown分隔、19处有明确断言或本地fake/temp上下文的fixture，以及6处未决。每项记录bare/quoted key与bare/quoted value形式；仅文件在tests目录不构成fixture豁免。分类没有写出任何值或代码上下文。

| 未决blob前缀 | 相对文件 | 行 |
|---|---|---:|
| 1b730e7 | experiments/memory-base-bubble-bridge/bubble-four-tools.patch | 407 |
| 53b4ce8 | experiments/memory-base-note-intent/artifacts/server/tests/memory-base.test.ts | 437 |
| 758f808 | memory_agent/eval/nav_63_results.md | 53、241 |
| 8d1118a | experiments/memory-base-package/cold-final-2026-10-12/probe_installed.py；同blob另见experiments/memory-base-package/probe_installed.py | 133 |
| d26cad4 | experiments/agent-loop-subagent/README.md | 31 |

这些位置仅发定位给Lead独立核对，原6项owner-review报告保持不变。Lead在2026-10-12只读内存复核反馈：前两是node嵌套脚本读取process.env/stdout，没有内层硬编码credential；两nav位置是锚定字段正则匹配/替换及空替换；probe位置是明确fixture且旁边为store路径表达式；最后为严格大写环境名引用。全部可归非secret语法/fixture/占位，Lead另保存独立复核记录，无需重复人审批。generic URL解析的90处错误都是含插值/嵌入代码的片段，定位与判据在 [url-parse-review.json](url-parse-review.json)；不把模板片段当已验证真实URL。通用GitHub/OpenAI/DeepSeek形态、可解析URL-userinfo与secret-query候选均0；placeholder启发式不是对所有未知凭据安全性的保证。

## 文件地图和既有证据

15个产品payload逐一归一化LF后全部等于 `45296a58fe2559e0b92f4af851c018b56148865a`；额外pyproject也一致，因此实际memory_base全部16个tracked文件均一致。README/AGENTS/CONTEXT中解析到的显式反引号memory_base路径和两安装入口均存在；首报告AGENTS的 `install-memory-base.ps1/.sh` 是两后缀缩写解析假阳性，补报告已分别核对两个文件。67个新增/变化Markdown的205个本地链接存在，133个外链排除；只检查文件/目录存在，不验证外链、标题anchor或平台可携带性。

固定tree既有R5 default/fresh安装及native、正式恢复、Bubble正式tool/pi证据的overall均PASS；没有重跑833单测、安装、R5/native、恢复、73/13或模型。`docs/memory-base-deployment.md:90` 的历史“尚未恢复”与102/104行当前#94成功状态并列，需要明确历史标签；此定位已发Lead，后续文档修订不在固定审计内。真实ADB设备/第二设备、真实注销登录与DSH/opencode真实对话仍是未验项，taskboard第78行没有宣称整体目标完成。更早冷启动/平台/恢复失败记录仍保留，不能用当前PASS覆盖历史FAIL或扩大短home/Windows验收范围。

## 复跑

激活主树既有venv后，以正式runtime Python执行 `audit.py --self-check`。完整比较用 `audit.py --repo <本工作树> --report <本目录新的报告名>`；只补通用对象扫描用 `--generic-only`，它不读取vault/env/原文。报告目标必须是本目录中不存在的新文件。`review_candidates.py` 针对已冻结补报告生成分类，现存报告时拒绝覆盖。只读操作不需要安装任何依赖。

完整源码 [audit.py](audit.py)，预登记 [METHOD.md](METHOD.md)。所有提交仅包含本目录安全脚本/报告；产品、正式runtime、Bubble实际文件、ignored旧store和共享历史没有改动。此审计交Lead作为draft PR输入，不发布、不push，也不宣布整体goal完成。
