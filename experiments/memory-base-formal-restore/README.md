# #94 正式快照空数据根恢复：完成

恢复对象是固定正式冷备份 `formal-20261012T014904`，manifest SHA-256 `27dc14f772e61a351be62bf3c5dfb24397c27cec8894612065139a8677541c65`。原件独立逐size/hash验证67文件、39目录、7,229,794字节；实际restore到新的 `.mb94/a` 空data后、start前全部逐字节与目录匹配。没有以旧synthetic #85替代本票。

| 要求 | 实际证据 |
|---|---|
| 真CLI restore | 3.7942秒、退出0；67文件及目录pre-start一致，sources原字节保留；原快照无access.json，其absence保留 |
| 第一次恢复启动 | 命令只 `start --home a`，没有timeout覆盖；默认180秒/组件，实际总182.2176秒、退出0、三服务ready。总时间可大于单组件预算 |
| 身份重新发放 | 原pending=true，启动后false；目标7个凭据与正式对应值不同（只记录Boolean），native gateway持久key hash旋转、用户/role roster保留 |
| 三条真实原文 | article/video/lesson经HTTP、实际MCP reader get/revision和离线物理文件hash一致；来源/task metadata与snapshot逐字节一致，只归档metadata hash，正文/URL不进入报告 |
| 检索与来源 | 三条capture search/list均成功，capture总数4；当前真实项目README和部署文档hash与实际source一致，MCP project search成功 |
| 权限 | 先用真实AddRequest验证合法合成fixture；reader add403、总数未增；匿名401、scope403、stale revision409；native目标gateway key读其他账户admin/users域403 |
| 实际MCP | SDK1.30.0，四个封闭工具（add/get/list/search），三原文get、reader list/project search成功 |
| 清理与原件 | target home manager stop成功；独立PID/birth/command无自有存活，58180/58181/58182关闭、records={}；正式3health200，只读；snapshot原件hash/inventory保持 |
| 安全交付 | 27个实际凭据、8个真实正文/URL/标题pattern扫描原始report/log；0匹配。没有提交snapshot数据、真实正文、私有来源URL或token |

三原文收据：

- article `4b24c835877cfaa5666c2ba322431a663aab3fdd17c3196ac99274e94fcf591d` → `a773d1d44eb19f2b3e061417b37aabfc728a1878bf50f0918eb994e1746b8095`。
- video `0ada0a7776fcf4e646f0b3b54d69972334f01b04a7da6488c4044fa6f46dbd7b` → `5f6ba7e2e05b205484c0034111404a363151d35d95516871c61fd6d727eeadc7`。
- lesson `5a3c5791d6c61e7a5771ca7006d5d0498134c7076dcad2e3312177af4f47ce9c` → `167b784b8f81f90927b6b3d656c0d3df8cec10f92d1601fb4c1901271f0ca6e0`。

全过程的粗边和边界保持可见：

1. 独立snapshot预检第一次因WindowsPath大小写排序与manifest相对字符串排序不一致退出1；67文件内容映射当时已一致，尚未创建target。只修了方法排序，原记录保留。
2. Lead原指定共享f runtime install显式wheel，会被pip无条件重装。install退出0，但R5非cache全字节守卫FAIL。产品payload、wheel metadata、direct_url及53个R5原报告/日志原件保持；console launcher重建（ZIP内新时间）和installed RECORD有副作用。原before mapping未永久落盘，精确旧RECORD hash/逐行delta不可恢复，**不能声称R5全部旧字节保持**。依Lead决定继续已安装空a，后续不再install，当前完整133文件基线含launcher/RECORD/directurl永久落盘并通过digest/size校验，此后保持。
3. 首restore及第一次start确实成功；随后reader fixture缺必填title导致422，live阶段FAIL。原FAIL/result/start/stop原件保留。合法fixture实际model_validate通过后，单独正常起停a完成剩余只读探针，第二次start68.0502秒、live39.0056秒、stop18.3553秒。这次不是第一次启动，不重装、不重恢复，不覆盖首FAIL。
4. 采用R5 f自己的既有完整runtime，产品仍冻结 `45296a58fe2559e0b92f4af851c018b56148865a`、wheel hash `baa5877bc55a834f7abf16c833d9e869c0768624efdd63de9ad1ef8b2768bfde`；不是第三套新依赖安装证据。f Lark缓存10727保持，Volc SDK缓存0→472来自第一次native，后续472保持；旧R5应用数据、部署、marker、报告、wheel均保持。
5. native权限证明是既有账户域边界403；快照没有自定义access或restricted文件ACL对象，不声称恢复了不存在的限制规则。本票不证明真实登录、第二实体设备、POSIX或其他版本恢复。正式instance没有启停/写入；无付费LLM或新真实capture。

资产原件全部保留于 `C:\Users\Tan\.mb94`，恢复目标为其 `a`。安全原件副本与raw SHA-256清单见 `evidence-manifest.json`；安装与方法FAIL、真实restore/start、最终只读PASS分别记录。完整真实data仅在原backup和隔离target保留，不加入Git。
