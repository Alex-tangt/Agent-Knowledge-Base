# collect_cost.ps1 -- M6 cost accounting (deps / storage / resident / model calls / maintenance).
#
# Safe to run at any time: whatever exists is measured, whatever is missing is reported as
# "not measured". Output goes to evidence\cost\cost.json plus a console summary.
#
# RSS caveat (AGENTS.md / perf_70): on this host, querying memory of a *detached* process from
# outside returns bogus values (a process holding 300 MB reports ~4.4 MB through
# psutil/tasklist/WorkingSet64). The working_set figures below are a LOWER-BOUND observation;
# the true value must come from the process self-reporting.
#
# ASCII-only on purpose (repo .ps1 files are BOM-less; PS 5.1 reads them as ANSI).
#
# Usage: powershell -File collect_cost.ps1 [-ServicePorts 8782]

[CmdletBinding()]
param(
    [int[]]$ServicePorts = @(8782),
    [string]$EvidenceDir = $null
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\lib_iso.ps1"
if (-not $EvidenceDir) { $EvidenceDir = $script:EvidenceDir }
$costDir = Join-Path $EvidenceDir 'cost'
New-Item -ItemType Directory -Force -Path $costDir | Out-Null

function Get-DirSizeMB {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return 0 }
    return [math]::Round($sum / 1MB, 1)
}

function Get-ProcsOnPort {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { return @() }
    return @($conns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        $p = Get-Process -Id $_ -ErrorAction SilentlyContinue
        if ($p) { $p }
    })
}

function Get-VersionOf {
    param([string]$Exe, [string[]]$ExeArgs = @('--version'))
    if (-not (Test-Path -LiteralPath $Exe)) { return '' }
    return ((& $Exe @ExeArgs 2>&1 | Out-String).Trim())
}

$venvOv = 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov'
$spOurs = Join-Path $venvOv 'Lib\site-packages'

$perPort = @()
foreach ($p in $ServicePorts) {
    foreach ($proc in (Get-ProcsOnPort $p)) {
        $perPort += [ordered]@{
            port           = $p
            pid            = $proc.Id
            name           = $proc.ProcessName
            working_set_mb = [math]::Round($proc.WorkingSet64 / 1MB, 1)
            private_mb     = [math]::Round($proc.PrivateMemorySize64 / 1MB, 1)
            start_time     = $proc.StartTime.ToString('o')
        }
    }
}

$report = [ordered]@{
    captured_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    rss_caveat      = 'working_set values are cross-process lower-bound observations and may be wrong; trust process self-report'
    dependencies    = [ordered]@{
        openviking_python_package = '0.4.23'
        openviking_ov_binary      = Get-VersionOf (Join-Path $venvOv 'Scripts\ov.exe')
        venv_ov_size_mb           = Get-DirSizeMB $venvOv
        site_packages_top_dirs    = @(Get-ChildItem -LiteralPath $spOurs -Directory -ErrorAction SilentlyContinue).Count
        deeptutor_version         = $script:DeeptutorPackageVersion
        deeptutor_python          = $script:DeeptutorPython
        opencode_version          = Get-VersionOf (Join-Path $env:APPDATA 'npm\node_modules\opencode-ai\bin\opencode.exe')
        mcp_sdk_in_venv_ov        = Get-VersionOf (Join-Path $venvOv 'Scripts\python.exe') @('-c', "import importlib.metadata as m; print(m.version('mcp'))")
        ollama_models             = @(& ollama list 2>&1 | Select-Object -Skip 1 | ForEach-Object { ($_ -split '\s+')[0..2] -join ' ' } | Where-Object { $_ })
    }
    storage = [ordered]@{
        iso_root_mb     = Get-DirSizeMB $script:IsoRoot
        host_copies_mb  = Get-DirSizeMB $script:HostsRoot
        service_data_mb = Get-DirSizeMB (Join-Path $script:IsoRoot 'data')
        index_like_dirs = @(Get-ChildItem -LiteralPath $script:IsoRoot -Recurse -Directory -ErrorAction SilentlyContinue |
                            Where-Object { $_.Name -match 'qdrant|store|index' } |
                            Select-Object -First 10 -ExpandProperty FullName)
    }
    resident = [ordered]@{
        note       = 'lower-bound observation; cross-process RSS of detached processes is unreliable'
        per_port   = $perPort
        opencode   = @(Get-Process opencode -ErrorAction SilentlyContinue | ForEach-Object {
                          [ordered]@{ pid = $_.Id; working_set_mb = [math]::Round($_.WorkingSet64 / 1MB, 1) } })
        # Loose filter: any system-Python 3.12 process, not necessarily DeepTutor. Recorded as a
        # ceiling on what a DeepTutor turn might hold; confirm with a process self-report if needed.
        python312_any = @(Get-Process -Name 'python' -ErrorAction SilentlyContinue |
                          Where-Object { $_.Path -like '*Python312*' } |
                          ForEach-Object { [ordered]@{ pid = $_.Id; name = $_.ProcessName; working_set_mb = [math]::Round($_.WorkingSet64 / 1MB, 1) } })
    }
    model_calls = [ordered]@{
        note              = 'both hosts use local Ollama in this experiment; cloud calls = 0, cost = 0 (see README section 5)'
        cloud_calls       = 0
        cloud_cost_cny    = 0
        local_evidence    = @(
            @(
                (Join-Path $script:EvidenceDir 'opencode\agent-turn.jsonl'),
                (Join-Path $script:EvidenceDir 'deeptutor\agent-turn.json')
            ) | Where-Object { Test-Path -LiteralPath $_ }
        )
    }
    maintenance_steps = @(
        'onboarding = write one isolated copy config (setup_isolation.ps1); restart the host to pick it up',
        'no per-host index: both hosts talk to one instance and one workspace',
        'service down shows up host-side as a connection failure (opencode mcp list = failed; DeepTutor probe_server ok=false)',
        'host upgrades only touch the isolated copy; syncing the real registration is the owner decision (this line never touches it)'
    )
}

Write-JsonNoBom -Path (Join-Path $costDir 'cost.json') -Object $report
Write-Host "== M6 cost accounting (lower bound) =="
$report | ConvertTo-Json -Depth 8 | Write-Host
Write-Host "[evidence] $(Join-Path $costDir 'cost.json')"
