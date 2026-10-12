# R4 启动导入诊断（不改变原验收判据）

原 R4 `start --timeout 300` 的 upstream 未健康，最终停止自有进程；Lead 用真实 upstream command、15s faulthandler 再现 import 栈位于 `openviking.parse.accessors.feishu_accessor` → `lark_oapi` generated APIs。这是已运行的窄反馈环；本轮只诊断，不实现修复或延长服务 timeout。

四个按优先级排列的可证伪假设：

1. 新目标缺少预编译 bytecode 是主要成本。预测：formal 已有缓存导入快，但同 formal 解释器强制只读源码导入明显慢；从未启动的 fresh 与缓存缺少的 default 表现相近。
2. generated SDK eagerly import 大量无关 API 的规模本身主导。预测：`lark_oapi` 单独 import 也会加载大量模块或在相同 generated API 栈达诊断时间上限；无需启动服务、配置或模型即可再现。
3. 未固定的新增 transitive 依赖差异主导。预测：distribution 差异应涉及慢栈包；若关键包 version 与 RECORD 中全部 Python source hashes 相同，则该包版本差异假设被否定。
4. upstream 配置、网络或模型加载才是必要触发。预测：单独导入 lark/Feishu accessor 应快速，只有实际 wrapper 慢；纯导入失败则否定其作为必要触发。

Lead 已只读核验 formal/target 的 lark-oapi 1.7.3、volcengine-python-sdk 5.0.51、fastapi 0.143.0、pydantic 2.14.0、openviking 0.4.23 相同；formal RECORD 有完整 SDK pyc，新目标 RECORD 无 pyc。必须进一步区分 RECORD 与实际文件，且只用 lexical 路径和 os.walk，不对十万文件逐个 Path.resolve。

**方法环境因素**：`run_recheck.clean_env()` 设置了 `PYTHONDONTWRITEBYTECODE=1`，包括 R4 native child。安装器本身还使用 `pip install --no-compile`。因此 R4 native 原 FAIL 包含禁止自动 pyc 写入的验收环境，不能不加限定地等同于未设置该变量的普通部署。诊断保持此 flag 与 `-B`，formal 只读，先保留证据；不据后续结果重标原 FAIL。

`diagnose_import.py` 只在 fresh 安装 gate 已封存 PASS 后运行。三环境 actual `.py/.pyc` census、distribution versions 和 source RECORD digest 在独立新日志目录保留；25s faulthandler 限定纯 import child，使用 `-X importtime` 捕获分项成本，不启动任何服务。formal force-source 使用不存在的本票 pycache_prefix 并 `-B`，不写 formal 或目标 bytecode。每项变量差异和未完成导入独立报告；目标数据与 vault 不读取。

原始 startup FAIL/cleanup 及 acceptance results 保持不变。结果、支持与否定的假设待实际运行追加；诊断上限不是产品 timeout 的替代值。
