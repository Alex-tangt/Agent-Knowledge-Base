# #95 固定发布范围只读审计预登记

固定 base/tree `4dadc407d39b26855a96079ce8561e8983dc4401`；固定 upstream `4270f13f418c5069316ba819cec11bb5910dcefc`（origin/master），差集121个提交。执行分支codex/95-publication-audit；只写本目录，当前主树后续docs/CI变化不纳入本次结论。禁止push、改历史、产品、实际runtime、用户文件或ignored资产，不安装/恢复/调用AOCI。

扫描两个集合分别报告：固定base完整tree的全部blob；固定upstream..base所涉未发布unique blobs，包括后来删除的AOCI文件。git cat-file输出只进入扫描进程内存，不恢复历史文件、不执行历史程序。全部结果仅object hash、相对文件名、计数、category/criterion/coverage；不输出匹配值、正文、标题或私有URL片段。

已知凭据：正式Windows vault与授权R4/R5/#94隔离home命名空间、实际Bubble与memory_agent的.env credential字段、经相关owned-process身份核对后的环境。凭据读取与比对在同一formal Python3.12进程；只读复用已有Root venv psutil作为process-env适配，不安装依赖，-B不写bytecode。来源枚举、数量、访问失败/旧轮换值不可得明确报告，绝不承诺发现所有未知secret。

真实内容：从固定tree已存在的receipt URI读取同一正式memory_base产品的已授权capture正文/追溯metadata，仅保留内存比较。标题单独视作可能有意公开摘要；带参数URL区分公共video定位参数和可能私有参数，不任意删。原始缓存/无关用户文件/外部网络内容不读；不可得与未覆盖的来源报告gap。

另扫明显GitHub/OpenAI/DeepSeek token形态、credential赋值和可疑带密钥URL形态，合成fixture/占位和真实值分开。发现已知secret或实际私有内容即输出安全定位给Lead，继续只读完成覆盖，不复制原文或重写共享历史。二进制做literal-byte匹配，不OCR、不声称检查全部未知编码/图片内容。

地图只核固定tree README/AGENTS/CONTEXT路径与memory_base实际文件、15 payload归一化文本==45296a58fe2559e0b92f4af851c018b56148865a；扫描本轮新增Markdown本地链接（GitHub/issues/source外链不当本地）。对R5/native/恢复/Bubble既有证据和未验ADB/登录/DSH-opencode状态做一致性定位，历史文字不替Lead修改。

不重复833单测、harness/SKIP、R5安装/native、恢复、73/13 Bubble或模型。扫描返回live handle、等自然完成，不以观察timeout作为terminal。报告须列漏项和判据，不宣布整体产品goal完成；PR地图与正式发布由Lead负责，本目录只交安全报告/脚本。

首轮 `scan-4dadc40.json` 原样保留。Lead源码复核指出首通用assignment漏裸credential名/quoted JSON key，generic URL形态尚未实现；因此该轮只支持已知来源literal匹配与已实现的token形态结果。追加通用key捕获后过滤credential字段、URL-userinfo/secret-query候选，并以8个合成正例及placeholder/code-expression反例校验。随后 `--generic-only` 单独生成补充报告，不再读vault/env/进程环境或HTTP正文；固定tree/range不变。候选仅category/行号/对象/path，人工安全分类不复制值。地图解析追加install双后缀简写，fence保留换行确保定位准确。
