# 草稿交付的干净 CI 回归

2026-10-12。草稿 [PR #96](https://github.com/Alex-tangt/Agent-Knowledge-Base/pull/96) 将既有 Windows/Ubuntu 两腿对齐正式 Python 3.12。这里只记录远端源码回归，不将 Ubuntu 单测视为 POSIX 部署验收，不重复独立安装、模型或实际快照恢复。

## 首次失败与定位

固定 head `7b0b2953c2d018df7004eff378d7164ea8064a00`，[run 38266677403](https://github.com/Alex-tangt/Agent-Knowledge-Base/actions/runs/38266677403)。两个干净 runner 都完成依赖/可编辑包/BM25 预热，随后 `python -m pytest tests/unit -q` 在收集 `test_memory_base_gateway.py` 时退出2：`ModuleNotFoundError: No module named 'fastapi'`。harness 与接入冒烟没有执行，不能记作通过或脚本自身的 SKIP。

原始失败步骤日志分别保存在 [Ubuntu](first-ubuntu-collection-failure.log) 与 [Windows](first-windows-collection-failure.log)，状态与具体步骤在 [first-run.json](first-run.json)。没有用户语料或个人运行环境，GitHub 自身凭据由 runner 屏蔽。

CI 只安装旧 `memory_agent/deploy-requirements.txt`，其中明确排除 FastAPI；新网关单测直接导入 FastAPI。本机根 venv 有 FastAPI 0.135.3，所以原有本地833闸门未发现干净 runner 的缺项。这是测试环境依赖遗漏，不是 Ubuntu 服务启动失败。

## 修复与复验

只在原有 CI 依赖步骤增加 `fastapi==0.143.0`，与 `memory_base/constraints.txt` 的正式版本一致。保留两平台、既有测试/harness/冒烟与只读 GitHub 权限；不安装已排除的 legal_web，不装 OpenViking/native 模型栈，不跳过网关测试，不修改应用源码或正式环境。

修复后实际远端两腿结果仍待取得。原失败保留；不能用本机已有 FastAPI 的导入成功代替干净 runner 复验。复验入口仍为现有 CI 的三个命令。

## 第二轮实际结果与入口缺陷

固定 head `09b8ed8213c7c02166fcde7ec0cb8a38458354c8`，[run 38267977956](https://github.com/Alex-tangt/Agent-Knowledge-Base/actions/runs/38267977956) 已结束。Windows 为 **835 passed /3 skipped /1 warning，74.32秒**，harness 两场景3+7全部预期匹配，实际opencode脚本因无4096服务器 **SKIP**。这是干净 Windows 源码回归，不是新的独立native运行证明。完整三个闸门的步骤输出见 [Windows 日志](second-windows-gate.log)。

Ubuntu 为 **3 failed /831 passed /4 skipped /1 warning，26.33秒**，不再缺 FastAPI。三项失败来自 `test_memory_base_deploy.py` 的真实缓存准备与两种真实wheel环境隔离：`.resolve()` 把 POSIX venv 的 `bin/python` 转成同一系统解释器，导致实际prefix/purelib丢失；测试helper也曾返回解引用后的系统入口。原失败全文在 [Ubuntu 日志](second-ubuntu-gate.log)，步骤状态和本机原件哈希在 [second-run.json](second-run.json)。harness/冒烟在该腿未执行，不计为通过或脚本SKIP。

新 [#97](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/97) 保留解释器入口的venv身份，仍严格验证目标own-site、继承provider拒绝和生命周期归属；不通过跳过POSIX测试消除失败。小机制探针可用现有WSL Python3.10，但不能据此声称正式3.12 wheel或完整POSIX部署。源码修复后另冻结完整闸门，旧45296a5独立native证据的版本范围保持。

该修复已合入`4498aea`。Lead在最终`4fc5aa9`复跑所有既有单测，837 passed /5 skipped /1既有warning，harness10/10，opencode脚本无服务器而SKIP；包括两个真实wheel fixture模式。源码摘要与执行者最终冻结一致，[最终本机闸门](../memory-base-live/gates-runtime-path-2026-10-12.json)已保存。最终远端Ubuntu3.12/Windows结果另行取得，不以WSL3.10或本机通过替代。
