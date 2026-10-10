# host_deeptutor_agent.ps1 -- host #2 (DeepTutor) real agent turn (optional, stronger evidence).
#
# `deeptutor run chat "<prompt>"` is DeepTutor's own single-turn capability entry point. Here it
# runs under an isolated DEEPTUTOR_HOME with a local Ollama model so the *model* decides to call
# the MCP tools (we do not call them on its behalf). Raw output goes to evidence\deeptutor\.
#
# Measured fact: DeepTutor lists local bindings (ollama / vllm / lm_studio / llama_cpp) in
# _NATIVE_TOOL_BLOCKED_BINDINGS (runtime\agentic\client.py:52), so a local model does not get
# native function-calling and falls back to the text tool protocol. A 3B model is likely flaky;
# try qwen3:8b. If model-driven calls stay unreliable, host_deeptutor.py (the host's own MCP
# client path) is the primary evidence and this must be recorded as "LLM-driven call unverified".
#
# ASCII-only on purpose (repo .ps1 files are BOM-less; PS 5.1 reads them as ANSI).
#
# Usage: powershell -File host_deeptutor_agent.ps1 [-Model qwen3:8b] [-Capability chat]

[CmdletBinding()]
param(
    [string]$Model      = 'qwen3:8b',
    [string]$Capability = 'chat',
    [string]$Prompt     = $null,
    [int]$TimeoutSec    = 600
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\lib_iso.ps1"

if (-not $Prompt) {
    $Prompt = 'Use the openviking-mvp MCP tools: call write for uri ' +
              '"viking://resources/mvp-host-connect/deeptutor-agent.md" with content ' +
              '"marker=M6-DEEPTUTOR-AGENT", then call read on that uri, then call search with ' +
              'query "M6-DEEPTUTOR-AGENT". Report the raw tool results.'
}

$outDir = Join-Path $script:EvidenceDir 'deeptutor'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$outFile = Join-Path $outDir 'agent-turn.json'
$errFile = Join-Path $outDir 'agent-turn.log'

$deeptutorExe = Join-Path (Split-Path -Parent $script:DeeptutorPython) 'Scripts\deeptutor.exe'
if (-not (Test-Path -LiteralPath $deeptutorExe)) {
    $cmd = Get-Command deeptutor -ErrorAction SilentlyContinue
    if ($cmd) { $deeptutorExe = $cmd.Source } else { throw 'deeptutor executable not found' }
}

$savedHome = $env:DEEPTUTOR_HOME
$env:DEEPTUTOR_HOME = $script:DeeptutorHome
$env:NO_PROXY = (Get-SanitizedNoProxy $env:NO_PROXY)   # drop bracketed IPv6, mvp-protocol pitfall #2

Write-Host "== host #2 DeepTutor real agent turn =="
Write-Host "   deeptutor : $deeptutorExe"
Write-Host "   HOME      : $env:DEEPTUTOR_HOME"
Write-Host "   capability: $Capability / model: $Model (Ollama, not cloud)"

try {
    $job = Start-Job -ScriptBlock {
        param($exe, $capability, $prompt, $model)
        & $exe run $capability $prompt --format json --language zh
    } -ArgumentList $deeptutorExe, $Capability, $Prompt, $Model

    if (Wait-Job $job -Timeout $TimeoutSec) {
        $text = Receive-Job $job | Out-String
        Write-TextNoBom -Path $outFile -Text $text
        if ($errFile) { Write-TextNoBom -Path $errFile -Text "see agent-turn.json" }
        Write-Host "[evidence] $outFile"
    } else {
        Stop-Job $job
        Write-Warning "timed out after ${TimeoutSec}s -- record as 'LLM-driven turn incomplete' and use host_deeptutor.py"
    }
    Remove-Job $job -Force
} finally {
    $env:DEEPTUTOR_HOME = $savedHome
}

Write-Host ""
Write-Host "verify isolation: powershell -File verify_isolation.ps1"
