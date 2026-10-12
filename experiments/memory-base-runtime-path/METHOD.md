# #97 解释器入口修复预登记

基点 `09b8ed8213c7c02166fcde7ec0cb8a38458354c8`，分支codex/97-runtime-path。写域仅memory_base/deploy.py、对应单测与本目录。原CI Ubuntu失败由Lead保留；既有Windows CI观察作业不重启，不改地图/CI，不push。

先用已安装WSL Ubuntu现有Python创建两个全新 `--without-pip --symlinks` 微型venv，实际运行各入口与产品_install_state返回入口，记录共享resolved基础exe但prefix/purelib不同的红信号。探针不安装工具/依赖/模型，不启动服务，fixture只在新建临时根内，报告不得覆盖。Python3.10结果只证明symlink机制，不宣称3.12 wheel验收。

已有直接证据的三个可证伪假设：1）最终python符号链接被resolve导致丢失venv入口，改为保留该最后一段应恢复prefix/site；2）仅比较底层exe会把不同venv误识为同一安装环境，同底层不同入口应仍独立选择/安装/验证；3）只是测试fixture返回resolve错误，若产品本身也错误则真实_install_state探针仍红，不能仅修改测试期望。先保留真实红证据，最小修复后同命令绿色对照。

只对解释器入口规范化父目录并保留最终python名字/符号链接；module/site/model/data/home的真实路径及安全边界不变。新增跨venv同底层exe回归，既有真实lark fixture own-root/拒继承/不导入/保留有效缓存继续核验，既有3.12真实wheel自动与fresh两模式复跑；它们已有明确依赖/准备/vault fixture边界，不冒称整个依赖栈安装或native冷启动。

Windows激活主树既有venv，用其绝对pytest和PYTHONPATH本工作树。正式home、R4/R5/#94、Bubble的runtime/data/vault/服务均不改，不访问实际凭据；无安装、模型、付费调用、AOCI或新工具。解释器入口改动需Lead冻结全量测试与PR Ubuntu3.12 CI独立确认；不延长180/600预算、不放宽进程归属与清理规则。
