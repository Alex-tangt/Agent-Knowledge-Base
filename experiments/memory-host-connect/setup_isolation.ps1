# setup_isolation.ps1 -- build isolated configs for both hosts (opencode + DeepTutor).
#
# Idempotent: re-running only rewrites the isolated copies. It never touches a real config;
# the script prints the real config hashes before/after and FAILS if any of them changed.
# ASCII-only on purpose (repo .ps1 files are BOM-less; PS 5.1 reads them as ANSI).
#
# Usage:
#   powershell -File setup_isolation.ps1
#   powershell -File setup_isolation.ps1 -ServiceUrl http://127.0.0.1:8782/mcp
#
# This script only writes <IsoRoot>\hosts\**. It does not start the service and does not
# call any tool -- the connect test runs after task-1 unblocks (instance on 8782).

[CmdletBinding()]
param(
    [string]$ServiceUrl = $null,
    [string]$OpencodeUser   = 'opencode-agent',
    [string]$DeeptutorUser  = 'deeptutor-agent',
    [string]$Account        = 'mvp-host',
    [string]$OllamaModel    = 'qwen2.5:3b-instruct',
    [string]$OllamaBaseUrl  = 'http://127.0.0.1:11434/v1'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\lib_iso.ps1"

if (-not $ServiceUrl) { $ServiceUrl = $script:DefaultServiceUrl }

Write-Host "== M6 host isolation: generate isolated configs =="
Write-Host "   iso root   : $script:IsoRoot"
Write-Host "   service URL: $ServiceUrl"
Write-Host "   local model: $OllamaModel @ $OllamaBaseUrl (Ollama, not cloud)"

New-IsoDirectories
$before = Get-RealConfigBaseline
Write-JsonNoBom -Path (Join-Path $script:EvidenceDir 'real-config-baseline.json') -Object $before

# ---------------------------------------------------------------- opencode side
# Isolated HOME: opencode reads $XDG_CONFIG_HOME/opencode (fallback $HOME/.config/opencode).
# Measured (opencode 1.18.35): with USERPROFILE/HOME/XDG_CONFIG_HOME set it reads only the copy.
$opencodeConfig = [ordered]@{
    '$schema' = 'https://opencode.ai/config.json'
    mcp = [ordered]@{
        'openviking-mvp' = [ordered]@{
            type    = 'remote'
            url     = $ServiceUrl
            enabled = $true
            headers = [ordered]@{
                'X-OpenViking-Account' = $Account
                'X-OpenViking-User'    = $OpencodeUser
            }
        }
    }
    provider = [ordered]@{
        ollama = [ordered]@{
            npm     = '@ai-sdk/openai-compatible'
            name    = 'Ollama (local)'
            options = [ordered]@{ baseURL = $OllamaBaseUrl }
            models  = [ordered]@{
                $OllamaModel = [ordered]@{ name = "$OllamaModel (local)" }
            }
        }
    }
    # Disable opencode's built-in file/shell tools for this run. Measured 2026-10-10: a 3B local
    # model asked "use the MCP server" instead invented a local file at
    # <project>\path\to\file.md with the built-in edit tool and never called the MCP server.
    # Removing the wrong affordance is what makes the agent-driven tool call reproducible.
    tools = [ordered]@{
        bash = $false; edit = $false; write = $false; read = $false
        grep = $false; glob = $false; list = $false; webfetch = $false
        task = $false; todowrite = $false; todoread = $false; patch = $false
    }
}
$target = Assert-UnderIso $script:OpencodeCfg
Write-JsonNoBom -Path $target -Object $opencodeConfig
Write-Host "[write] opencode isolated config : $target"

# --------------------------------------------------------------- DeepTutor side
# Deployment-level MCP config: <DEEPTUTOR_HOME>/data/user/settings/mcp.json
# Shape = deeptutor.services.mcp.config.MCPConfig ({"servers": {name: MCPServerConfig}}).
# The allowlist is explicit (read + one permitted write) instead of "*", which also
# exercises scope control at the config layer.
$readTools  = @('search', 'find', 'read', 'list', 'tree', 'grep', 'glob', 'health')
$writeTools = @('write')   # content write only; no forget / add_skill (destructive/governance)
# NOTE: never name locals after lib_iso.ps1 script-scope vars (PowerShell is case-insensitive
# and this file is dot-sourced into the same scope) -- e.g. $deeptutorMcp would overwrite
# $script:DeeptutorMcp (the path) with an ordered dictionary.
$dtMcpConfig = [ordered]@{
    servers = [ordered]@{
        'openviking-mvp' = [ordered]@{
            type           = 'streamableHttp'
            url            = $ServiceUrl
            headers        = [ordered]@{
                'X-OpenViking-Account' = $Account
                'X-OpenViking-User'    = $DeeptutorUser
            }
            tool_timeout   = 30
            enabled_tools  = @($readTools + $writeTools)
            disabled_tools = @('forget', 'add_skill', 'add_resource')
            enabled        = $true
        }
    }
}
$target = Assert-UnderIso $script:DeeptutorMcp
Write-JsonNoBom -Path $target -Object $dtMcpConfig
Write-Host "[write] DeepTutor mcp.json        : $target"

# DeepTutor model catalog: local Ollama only. We deliberately do NOT copy the user's real
# model_catalog.json -- that file holds a plaintext cloud API key, and copying it would put
# a secret into the experiment tree (violates "secrets never on disk / never echoed").
$dtCatalog = [ordered]@{
    version     = 1
    connections = @()
    services    = [ordered]@{
        llm = [ordered]@{
            active_profile_id = 'llm-profile-ollama'
            active_model_id   = 'llm-model-ollama'
            profiles          = @([ordered]@{
                id            = 'llm-profile-ollama'
                name          = 'Local Ollama (isolated M6)'
                binding       = 'ollama'
                base_url      = $OllamaBaseUrl
                api_key       = ''
                api_version   = ''
                extra_headers = [ordered]@{}
                models        = @([ordered]@{ id = 'llm-model-ollama'; name = $OllamaModel; model = $OllamaModel })
                api_format    = 'auto'
                wire_api      = 'auto'
            })
        }
        task      = [ordered]@{ active_profile_id = $null; active_model_id = $null; profiles = @() }
        embedding = [ordered]@{ active_profile_id = $null; active_model_id = $null; profiles = @() }
        search    = [ordered]@{ active_profile_id = $null; profiles = @() }
        tts       = [ordered]@{ active_profile_id = $null; active_model_id = $null; profiles = @() }
        stt       = [ordered]@{ active_profile_id = $null; active_model_id = $null; profiles = @() }
        imagegen  = [ordered]@{ active_profile_id = $null; active_model_id = $null; profiles = @() }
        videogen  = [ordered]@{ active_profile_id = $null; active_model_id = $null; profiles = @() }
    }
}
$target = Assert-UnderIso $script:DeeptutorCatalog
Write-JsonNoBom -Path $target -Object $dtCatalog
Write-Host "[write] DeepTutor model catalog   : $target (binding=ollama, no key)"

# DeepTutor creates data/** on run; pre-create so "writes stay inside the isolation root"
# stays checkable.
foreach ($d in @('data\user\.runtime', 'data\user\workspace', 'data\user\logs', 'data\system')) {
    $p = Assert-UnderIso (Join-Path $script:DeeptutorHome $d)
    if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
}

# ------------------------------------------------------------------ post-check
$after = Get-RealConfigBaseline
$changed = @()
foreach ($k in $before.files.Keys) {
    if ($before.files[$k].sha256 -ne $after.files[$k].sha256) { $changed += $k }
}
Write-Host ""
Write-Host "-- real-config zero-write check --"
Write-Host ("   opencode.json     : {0}" -f $(if ($before.files.opencode_json.sha256) { $before.files.opencode_json.sha256 } else { '(absent)' }))
Write-Host ("   DeepTutor mcp.json: {0}" -f $(if ($before.files.deeptutor_mcp_json.sha256) { $before.files.deeptutor_mcp_json.sha256 } else { '(absent)' }))
if ($changed.Count -gt 0) {
    Write-Error "real config changed while generating the isolated copies: $($changed -join ', ') -- stop and investigate"
} else {
    Write-Host "   result: PASS (real configs unchanged)"
}
Write-Host ""
Write-Host "Next (after task-1 unblocks and 8782 is ready): host_opencode.ps1 / host_deeptutor.py"
