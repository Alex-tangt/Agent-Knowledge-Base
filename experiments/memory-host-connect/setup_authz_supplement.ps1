# setup_authz_supplement.ps1 -- M6 supplement: a SECOND isolated DeepTutor home for the authz run.
#
# The dev-profile home (hosts\deeptutor-home) is left byte-identical so the already-recorded M6
# evidence stays reproducible; this script only creates hosts\deeptutor-home-authz.
#
# Credential handling: mcp.json carries a DeepTutor *secret reference*, not a key --
#   "X-API-Key": "${secret:openviking-mvp/api_key_ro}"
# The value lives in DeepTutor's own per-owner secret store (data/system/user-secrets/_shared/
# private/mcp/openviking-mvp.json, chmod 700), written by host_identity.py. No key ever lands in
# mcp.json, in this script, in git, or in evidence.
#
# ASCII-only on purpose (repo .ps1 files are BOM-less; PS 5.1 reads them as ANSI).

[CmdletBinding()]
param(
    [string]$ServiceUrl = 'http://127.0.0.1:8782/mcp',
    [string]$Account    = 'default',
    [string]$User       = 'host-ro',
    [string]$SecretField = 'api_key_ro'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\lib_iso.ps1"

# NOTE: never name a local $home here -- $HOME is a read-only automatic variable and PowerShell
# variable names are case-insensitive, so `$home = ...` throws VariableNotWritable (measured).
$dtHome = Join-Path $script:HostsRoot 'deeptutor-home-authz'
$settings = Join-Path $dtHome 'data\user\settings'
$mcpPath = Join-Path $settings 'mcp.json'

Write-Host "== M6 supplement: authz-profile DeepTutor host =="
Write-Host "   home        : $dtHome"
Write-Host "   service URL : $ServiceUrl"
Write-Host "   credential  : `$`{secret:openviking-mvp/$SecretField`}  (reference only; value in DeepTutor secret store)"

foreach ($d in @($settings, (Join-Path $dtHome 'data\user\.runtime'), (Join-Path $dtHome 'data\user\workspace'),
                 (Join-Path $dtHome 'data\user\logs'), (Join-Path $dtHome 'data\system'))) {
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

$readTools  = @('search', 'find', 'read', 'list', 'tree', 'grep', 'glob', 'health')
$writeTools = @('write')

$authzMcp = [ordered]@{
    servers = [ordered]@{
        'openviking-mvp' = [ordered]@{
            type           = 'streamableHttp'
            url            = $ServiceUrl
            headers        = [ordered]@{
                'X-OpenViking-Account' = $Account
                'X-OpenViking-User'    = $User
                'X-API-Key'            = "`${secret:openviking-mvp/$SecretField}"
            }
            tool_timeout   = 30
            enabled_tools  = @($readTools + $writeTools)
            disabled_tools = @('forget', 'add_skill', 'add_resource')
            enabled        = $true
        }
    }
}

$full = [System.IO.Path]::GetFullPath($mcpPath)
$root = [System.IO.Path]::GetFullPath($script:IsoRoot)
if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "refusing to write outside the isolation root: $full"
}
Write-JsonNoBom -Path $full -Object $authzMcp
Write-Host "[write] $full"
Write-Host "   identity in config: account=$Account user=$User (switch role by re-running with -SecretField api_key_rw -User host-rw)"
