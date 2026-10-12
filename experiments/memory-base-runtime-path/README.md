# #97 venv解释器入口修复

基点 `09b8ed8213c7c02166fcde7ec0cb8a38458354c8`。实现冻结 `8d832817a0f59223ddf12036b7b397b780715f2d`：仅对解释器入口规范化父目录并保留最终名字/符号链接，统一用于_install_state、_package_input、_installed_source和准备子进程身份比较。两个venv即使共享同一个系统exe，也不能据此视为同一安装环境。模块、site、模型、数据、home和owned_component原安全判据保持。

## 实际红/绿

[预登记](METHOD.md) 与 [最小探针](probe_symlink.py) 先提交 `1d9cbfe`，再跑现有WSL Ubuntu-22.04 Python3.10.12。两个实际 `--without-pip --symlinks` venv均指向 `/usr/bin/python3.10`，但各自prefix/site不同。

| 条件 | 结果 |
|---|---|
| 原实现 | selected都成为系统exe，prefix变/usr、purelib变系统dist-packages，RED |
| 修复后同探针 | selected保留各自bin/python，prefix/purelib均与实际venv一致，GREEN |

[原红报告](symlink-red.json)（PID349）与[绿报告](symlink-green.json)（PID365）保留，均自然退出0表示符合命令的预期red/green判据，没有把旧失败改SKIP。新建微型fixture保留在报告指定/tmp根，未删除任何旧资产。该结果只证明POSIX符号链接机制，不是Python3.12 wheel或Linux完整依赖/native验收。

最小绿命令：

```powershell
wsl.exe -d Ubuntu-22.04 --exec python3 -B /mnt/c/Users/Tan/.codex/worktrees/memory-bootstrap-fix/Agent-Knowledge-Base/experiments/memory-base-runtime-path/probe_symlink.py --repo /mnt/c/Users/Tan/.codex/worktrees/memory-bootstrap-fix/Agent-Knowledge-Base --report /mnt/c/Users/Tan/.codex/worktrees/memory-bootstrap-fix/Agent-Knowledge-Base/experiments/memory-base-runtime-path/new-green.json --expected green
```

报告必须是本目录中不存在的新文件。原红运行使用相同参数结构和`--expected red`。

## Windows3.12验证及顺序

激活主树已有venv，显式PYTHONPATH为此工作树，并使用主树绝对Python3.12.14：

```powershell
. D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\Activate.ps1
$env:PYTHONPATH='C:\Users\Tan\.codex\worktrees\memory-bootstrap-fix\Agent-Knowledge-Base'
& D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe -B -m pytest tests/unit/test_memory_base_deploy.py -q
```

完整deploy模块：**68 passed，88.44s**，exec54713自然退出0，原输出 [windows-deploy-tests.log](windows-deploy-tests.log)。包括真实target lark fixture的own-root、拒绝继承provider、不导入SDK、有效缓存不强制重编、准备失败不泄漏源码等既有检查，以及实际venv explicit/previous/fresh入口回归和不同venv的wheel选择/返回解释器身份拒绝。

该68项执行之后，最后一个纯mock的`test_target_import_verification_rejects_launchers_package_and_clears_pythonpath`中sys入口预期也改成保留final-link，然后在最终测试文件上单独复跑：**1 passed /67 deselected，0.96s**。因此68项日志不是最终测试文件全量运行；产品文件在两个检查之间没有再改，最终完整主树与Ubuntu3.12仍由Lead/PR CI冻结复跑，不能用旧source hash代替最终。

新增回归第一次Windows局部运行为3 PASS /1 FAIL：失败是我新增PEP610 origin fixture漏了必需SHA256字段。已补wheel实际摘要，未放宽产品provenance检查；此方法错误在 [results.json](results.json) 保留，不能作为真实路径bug的红证据。

既有真实wheel的 **automatic-default / explicit-fresh两模式均通过**，wheel构建、两个目标的产品包pip安装、模块与console入口实际执行。完整逐命令证据：[自动模式](wheel-automatic-default.json)、[fresh模式](wheel-explicit-fresh.json)。它们保留已有fixture边界：借现有build/pip依赖，upstream依赖安装/版本、SDK准备与vault阶段由测试fixture处理；不宣称全新依赖栈安装、SDK冷启动或native服务。本轮没有启动任何产品服务、下载模型、调用LLM/付费接口或修改正式/R4/R5/#94/Bubble runtime/data/vault。

原Ubuntu3failed/831passed/4skipped与Windows835passed/3skipped CI记录由Lead保存在memory-base-ci目录，本分支未覆盖或重启旧CI。R5旧证据仍只支持45296a5；本源码变更之后需最终head的Ubuntu/Windows3.12闸门，不宣称整体goal完成。

## 交付范围

产品及对应单测在实现commit；本目录保存预登记、probe与安全结果，不改地图/CI、安装策略、180/600预算或进程清理归属。未push/merge。所有工具运行已自然结束，无live handle遗留；最终工作树状态另随commit交付。
