# 草稿交付的干净 CI 回归

2026-10-12。草稿 [PR #96](https://github.com/Alex-tangt/Agent-Knowledge-Base/pull/96) 将既有 Windows/Ubuntu 两腿对齐正式 Python 3.12。这里只记录远端源码回归，不将 Ubuntu 单测视为 POSIX 部署验收，不重复独立安装、模型或实际快照恢复。

## 首次失败与定位

固定 head `7b0b2953c2d018df7004eff378d7164ea8064a00`，[run 38266677403](https://github.com/Alex-tangt/Agent-Knowledge-Base/actions/runs/38266677403)。两个干净 runner 都完成依赖/可编辑包/BM25 预热，随后 `python -m pytest tests/unit -q` 在收集 `test_memory_base_gateway.py` 时退出2：`ModuleNotFoundError: No module named 'fastapi'`。harness 与接入冒烟没有执行，不能记作通过或脚本自身的 SKIP。

原始失败步骤日志分别保存在 [Ubuntu](first-ubuntu-collection-failure.log) 与 [Windows](first-windows-collection-failure.log)，状态与具体步骤在 [first-run.json](first-run.json)。没有用户语料或个人运行环境，GitHub 自身凭据由 runner 屏蔽。

CI 只安装旧 `memory_agent/deploy-requirements.txt`，其中明确排除 FastAPI；新网关单测直接导入 FastAPI。本机根 venv 有 FastAPI 0.135.3，所以原有本地833闸门未发现干净 runner 的缺项。这是测试环境依赖遗漏，不是 Ubuntu 服务启动失败。

## 修复与复验

只在原有 CI 依赖步骤增加 `fastapi==0.143.0`，与 `memory_base/constraints.txt` 的正式版本一致。保留两平台、既有测试/harness/冒烟与只读 GitHub 权限；不安装已排除的 legal_web，不装 OpenViking/native 模型栈，不跳过网关测试，不修改应用源码或正式环境。

修复后实际远端两腿结果仍待取得。原失败保留；不能用本机已有 FastAPI 的导入成功代替干净 runner 复验。复验入口仍为现有 CI 的三个命令。
