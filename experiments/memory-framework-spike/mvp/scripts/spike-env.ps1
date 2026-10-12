# Spike environment: isolated OpenViking instance for experiments/memory-framework-spike/mvp.
# Dot-source from PowerShell: . .\spike-env.ps1
# All state lives outside the repo, under %USERPROFILE%\.openviking-mvp-spike.

$env:SPIKE_ROOT = (Join-Path $env:USERPROFILE '.openviking-mvp-spike')
$env:SPIKE_VENV = 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts'
$env:SPIKE_PORT = '8781'
$env:SPIKE_BASE_URL = 'http://127.0.0.1:8781'
$env:SPIKE_WORKSPACE = (Join-Path $env:SPIKE_ROOT 'data')
$env:SPIKE_LOGS = (Join-Path $env:SPIKE_ROOT 'logs')
$env:SPIKE_PY = (Join-Path $env:SPIKE_VENV 'python.exe')
$env:SPIKE_SERVER = (Join-Path $env:SPIKE_VENV 'openviking-server.exe')
$env:SPIKE_OV = (Join-Path $env:SPIKE_VENV 'ov.exe')
$env:OPENVIKING_CONFIG_FILE = (Join-Path $env:SPIKE_ROOT 'ov.conf')
$env:OPENVIKING_CLI_CONFIG_FILE = (Join-Path $env:SPIKE_ROOT 'ovcli.conf')

# Known host pitfall #1: NO_PROXY contains a bracketed IPv6 literal ([::1]); the built-in
# HTTP clients (litellm/httpx) then fail with "Invalid port: ':1]'". Rewrite in-process.
# Known host pitfall #2: the host exports BOTH `NO_PROXY` and `no_proxy`. PowerShell's
# Start-Process builds a case-insensitive env dictionary and dies with
# "已添加项。字典中的关键字:NO_PROXY" — so drop both spellings, then set exactly one.
Remove-Item Env:NO_PROXY, Env:no_proxy -ErrorAction SilentlyContinue
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
# This instance must not talk to any cloud model provider.
Remove-Item Env:HTTP_PROXY, Env:HTTPS_PROXY, Env:http_proxy, Env:https_proxy -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path $env:SPIKE_ROOT, $env:SPIKE_WORKSPACE, $env:SPIKE_LOGS | Out-Null
