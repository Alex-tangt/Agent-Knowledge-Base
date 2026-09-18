# memory-agent 一键部署（#52 / ADR-0028）——薄壳。
#
# 只做三件事：定位仓库根 → 选一个带 venv 的系统 Python → 交给统一的
# `python -m memory_agent.deploy install`（真正实现：建 venv / 装依赖 / 建索引 /
# 写 opencode 注册 / 落 skill / 起 daemon / 冒烟，全部幂等）。
#
# 用法：
#   pwsh install.ps1                 # 一键部署
#   pwsh install.ps1 --dry-run       # 只预览，不落盘
#   pwsh install.ps1 --no-index      # 跳过建索引
#   pwsh install.ps1 --with-tests    # 额外装 pytest
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CmdArgs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Py = if ($env:MEMORY_INSTALL_PYTHON) { $env:MEMORY_INSTALL_PYTHON } else { "python" }
if (-not (Get-Command $Py -ErrorAction SilentlyContinue)) {
    Write-Error "找不到 $Py（可设 MEMORY_INSTALL_PYTHON 指定解释器）"
    exit 2
}

& $Py -c "import venv" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "$Py 缺少 venv 模块，请安装 Python 的 venv/ensurepip 支持"
    exit 2
}

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root;$env:PYTHONPATH" } else { $Root }
& $Py -m memory_agent.deploy install @CmdArgs
exit $LASTEXITCODE
