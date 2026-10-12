# #94：固定正式快照恢复到新的空数据根

基于 Lead `0c73019`；实际产品仍为 #86 已验证的冻结 `45296a58fe2559e0b92f4af851c018b56148865a` wheel，SHA-256 `baa5877bc55a834f7abf16c833d9e869c0768624efdd63de9ad1ef8b2768bfde`。

正式快照仅只读：`D:/python_work/work2026-4/memory-base-backups/formal-20261012T014904`，manifest hash `27dc14f772e61a351be62bf3c5dfb24397c27cec8894612065139a8677541c65`，67文件。先独立逐文件size/hash、目录清单验证，不能只用被测backup validator。正式实例不启停、不写任何配置/数据/凭据。

仅新建 `C:/Users/Tan/.mb94/a`（整个 .mb94 根必须此前不存在）。使用已有 R5 f own-site runtime 的真实 CLI install，显式同一wheel/model，不算第三套新依赖安装；运行前后校验 R5 产品文件/RECORD/directurl、原报告、d/f数据/部署/marker字节不变，声明 f runtime 自身新增bytecode缓存。既有约束已满足，若需要不同产品/依赖字节先停止报告。

仅未启动目标配置修改隔离端口fixture，建立空data且无sources/access/restore状态。真实 `restore --home a --backup <固定快照>` 后、start前独立核全部67文件和目录、sources原字节、access不存在语义保留、identity_reissue_pending为true。

真实首次 `start --home a` 不传timeout（默认180秒/组件），三服务ready，目标身份重新发放且pending解除，全部本home凭据与正式对应凭据不同，只记录Boolean。至少三条既有article/video/lesson原文get、revision/hash、snapshot来源metadata一致；list/search、当前真实项目README/docs路径实时hash、reader写403和scope/revision拒绝；实际MCP SDK1.30四个封闭工具及原文get/list。

快照无自定义access.json/独立restricted文件ACL，不能声称恢复了不存在的限制对象。native权限探针准确验证目标gateway管理员key不能读取别的账户admin/users域（403）；不利用可被忽略的identity头假装换账户，不用正式key向目标请求。所有网络探针只读，唯一add尝试是reader合成拒绝请求，必须403且不新增capture。

最后仅目标home manager stop，PID/birth/command/端口/records清理；原快照、R5原件字节再次独立核不变，正式三服务health只读。失败原始log/exit保留，不增timeout/改产品补跑。报告仅URI/hash/Boolean/状态，不提交真实正文、私有source URL、token或snapshot内容。全部目标原件保持在 .mb94，安全证据写本目录；无付费LLM/AOCI。
