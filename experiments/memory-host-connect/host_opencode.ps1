# host_opencode.ps1 -- host #1 (opencode) evidence capture.
#
# Two steps, all under an isolated HOME, raw output saved to evidence\:
#   A. `opencode mcp list`            -> proves opencode itself connected to openviking-mvp
#   B. `opencode run --format json`   -> raw event stream of a real agent turn (tool calls + results)
#
# Isolation (measured on opencode 1.18.35): USERPROFILE / HOME / XDG_CONFIG_HOME all point at
# <IsoRoot>\hosts\opencode-home, so it reads only the isolated .config\opencode\opencode.json.
# The user's real registration is never read for this run and never written.
#
# ASCII-only on purpose (repo .ps1 files are BOM-less; PS 5.1 reads them as ANSI).
#
# Usage:
#   powershell -File host_opencode.ps1
#   powershell -File host_opencode.ps1 -SkipAgentTurn        # connection check only

[CmdletBinding()]
param(
    [string]$Model       = 'qwen2.5:3b-instruct',
    [string]$Prompt      = $null,
    [string]$EvidenceDir = $null,
    [switch]$SkipAgentTurn
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\lib_iso.ps1"
if (-not $EvidenceDir) { $EvidenceDir = $script:EvidenceDir }
if (-not (Test-Path -LiteralPath $EvidenceDir)) { New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null }

if (-not $Prompt) {
    # Short on purpose: a local CPU model generates slowly and a long multi-step instruction made a
    # 3B model conflate the arguments (measured 2026-10-10: it passed read's {"uris": ...} to write).
    # One tool call only, and an explicit stop -- otherwise the model re-calls write with the tool's
    # own output as the content and loops (measured 2026-10-10). The cross-host read is DeepTutor's.
    $Prompt = 'Call the MCP tool openviking-mvp_write exactly once, with these arguments: ' +
              '{"uri":"viking://resources/mvp-host-connect/opencode.md",' +
              '"content":"M6-OPENCODE marker written by the opencode agent",' +
              '"mode":"replace","wait":true}. ' +
              'After the tool returns, reply with the single word DONE. ' +
              'Never call any tool a second time.'
}

# ---- locate opencode ---------------------------------------------------------
$opencodeExe = $null
foreach ($cand in @(
        (Join-Path $env:APPDATA 'npm\node_modules\opencode-ai\bin\opencode.exe'),
        (Join-Path $env:APPDATA 'npm\opencode.cmd'),
        (Join-Path $env:APPDATA 'npm\opencode.exe'))) {
    if (Test-Path -LiteralPath $cand) { $opencodeExe = $cand; break }
}
if (-not $opencodeExe) {
    $cmd = Get-Command opencode -ErrorAction SilentlyContinue
    if ($cmd) { $opencodeExe = $cmd.Source }
}
if (-not $opencodeExe) { throw 'opencode executable not found' }

# ---- isolated env -----------------------------------------------------------
$childEnv = @{
    USERPROFILE = $script:OpencodeHome
    HOME        = $script:OpencodeHome
    XDG_CONFIG_HOME = (Join-Path $script:OpencodeHome '.config')
    NO_PROXY    = (Get-SanitizedNoProxy $env:NO_PROXY)
    OPENCODE_DISABLE_TELEMETRY = '1'
}
$saved = @{}
foreach ($k in $childEnv.Keys) {
    $saved[$k] = [Environment]::GetEnvironmentVariable($k)
    [Environment]::SetEnvironmentVariable($k, $childEnv[$k])
}

Write-Host "== host #1 opencode =="
Write-Host "   exe       : $opencodeExe"
Write-Host "   HOME      : $($script:OpencodeHome)"
Write-Host "   project   : $($script:OpencodeProject)"

# Evidence metadata: records WHO drives the tool calls and under which isolation/authn facts.
# Lead decision (2026-10-10): the two hosts differ in driver -- opencode = agent-driven
# (the local LLM decides to call MCP tools); DeepTutor = host MCP client path. The evidence
# must state that difference instead of implying both are the same.
$metaPath = Join-Path $EvidenceDir 'opencode\run-meta.json'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $metaPath) | Out-Null
$ErrorActionPreference = 'Continue'
$oca = & $opencodeExe --version 2>&1 | Out-String
$ErrorActionPreference = 'Stop'
$meta = [ordered]@{
    host             = 'opencode'
    driver           = 'agent-driven: the local LLM decides which MCP tools to call'
    opencode_version = $oca.Trim()
    isolated_home    = $script:OpencodeHome
    isolated_config  = $script:OpencodeCfg
    isolated_env     = [ordered]@{
        USERPROFILE     = $childEnv.USERPROFILE
        HOME            = $childEnv.HOME
        XDG_CONFIG_HOME = $childEnv.XDG_CONFIG_HOME
        NO_PROXY        = $childEnv.NO_PROXY
    }
    model            = "ollama/$Model"
    authn            = [ordered]@{
        mode               = 'unknown until the task-1 instance states it (localhost, no key assumed -- this is NOT proof of authorization)'
        identity_headers   = @('X-OpenViking-Account', 'X-OpenViking-User')
        credential_headers = @()   # fill in when task-1 reports the instance authn mode
    }
    in_scope_only    = 'real opencode registration and real DeepTutor config are read-only to this run; see verify_isolation.ps1'
}
Write-JsonNoBom -Path $metaPath -Object $meta
Write-Host "[evidence] $metaPath"

try {
    # Native exes write progress to stderr; with $ErrorActionPreference='Stop' PS 5.1 turns
    # merged native stderr (2>&1) into a terminating error. Relax it around native calls.
    $ErrorActionPreference = 'Continue'

    # ---- A. connection status ------------------------------------------------
    $mcpListOut = Join-Path $EvidenceDir 'opencode\mcp-list.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $mcpListOut) | Out-Null
    Write-Host ""
    Write-Host "-- A. opencode mcp list --"
    $a = & $opencodeExe mcp list 2>&1 | Out-String
    Write-Host $a
    Write-TextNoBom -Path $mcpListOut -Text $a
    Write-Host "[evidence] $mcpListOut"
    if ($a -match 'failed') { Write-Warning 'opencode reports a failed connection -- confirm the isolated instance on 8782 is up' }

    if ($SkipAgentTurn) { Write-Host "(SkipAgentTurn: real agent turn skipped)"; return }

    # ---- B. real agent turn --------------------------------------------------
    $runOut = Join-Path $EvidenceDir 'opencode\agent-turn.jsonl'
    $runLog = Join-Path $EvidenceDir 'opencode\agent-turn.log'
    Write-Host ""
    Write-Host "-- B. opencode run (local Ollama: $Model) --"
    $models = & ollama list 2>&1 | Out-String
    if ($models -notmatch [regex]::Escape($Model)) {
        Write-Warning "ollama has no model '$Model'. Installed:`n$models"
    }
    $runArgs = @('run', '-m', "ollama/$Model", '--format', 'json', '--auto',
                 '--dir', $script:OpencodeProject, $Prompt)
    & $opencodeExe @runArgs 2> $runLog | Out-File -FilePath $runOut -Encoding utf8
    Write-Host "[evidence] $runOut"
    Write-Host "[evidence] $runLog"
    $hits = @(Select-String -Path $runOut -Pattern 'openviking' -SimpleMatch -ErrorAction SilentlyContinue).Count
    Write-Host "[summary] lines mentioning openviking in the event stream: $hits"
} finally {
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
}

Write-Host ""
Write-Host "done. verify isolation: powershell -File verify_isolation.ps1"
