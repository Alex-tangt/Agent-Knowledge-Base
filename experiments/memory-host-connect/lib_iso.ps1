# lib_iso.ps1 -- isolation paths and helpers for the M6 host-connect line.
#
# Hard constraints (task-2):
#   * write only under %USERPROFILE%\.openviking-mvp-host\hosts\ ;
#   * never write the user's real opencode registration (~/.config/opencode/opencode.json);
#   * never write the user's real DeepTutor config (<DEEPTUTOR_HOME>/data/user/settings/*);
#   * configs/JSON must be BOM-free (PowerShell 5.1 `-Encoding UTF8` adds a BOM).
#   * THIS FILE IS ASCII-ONLY on purpose: repo .ps1 files carry no BOM and
#     Windows PowerShell 5.1 reads BOM-less scripts as ANSI, which corrupts non-ASCII.
#
# Usage:  . "$PSScriptRoot\lib_iso.ps1"

Set-StrictMode -Version Latest

# ---- repo / isolation roots / host dirs --------------------------------------
$script:WkHostRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:IsoRoot      = Join-Path $env:USERPROFILE '.openviking-mvp-host'
$script:HostsRoot    = Join-Path $script:IsoRoot 'hosts'
$script:OpencodeHome = Join-Path $script:HostsRoot 'opencode-home'
$script:OpencodeCfg  = Join-Path $script:OpencodeHome '.config\opencode\opencode.json'
$script:OpencodeProject = Join-Path $script:HostsRoot 'opencode-project'
$script:DeeptutorHome = Join-Path $script:HostsRoot 'deeptutor-home'
$script:DeeptutorSettings = Join-Path $script:DeeptutorHome 'data\user\settings'
$script:DeeptutorMcp = Join-Path $script:DeeptutorSettings 'mcp.json'
$script:DeeptutorCatalog = Join-Path $script:DeeptutorSettings 'model_catalog.json'
$script:EvidenceDir  = Join-Path $PSScriptRoot 'evidence'

# ---- real (read-only) configs; discovery notes in README section 1 -----------
$script:RealOpencodeConfig = Join-Path $env:USERPROFILE '.config\opencode\opencode.json'
$script:RealDeeptutorHome  = if ($env:DEEPTUTOR_REAL_HOME) { $env:DEEPTUTOR_REAL_HOME } else { 'D:\Study\my-deeptutor' }
$script:RealDeeptutorMcp   = Join-Path $script:RealDeeptutorHome 'data\user\settings\mcp.json'
$script:RealDeeptutorCatalog = Join-Path $script:RealDeeptutorHome 'data\user\settings\model_catalog.json'

# ---- host-side facts (verified 2026-10-10, see README section 1) -------------
$script:DeeptutorPython = 'D:\Users\Tan\AppData\Local\Programs\Python\Python312\python.exe'
$script:DeeptutorPackageVersion = '1.6.8'
$script:DefaultServiceUrl = 'http://127.0.0.1:8782/mcp'

function Write-TextNoBom {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][AllowEmptyString()][string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Write-JsonNoBom {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Object, [int]$Depth = 24)
    Write-TextNoBom -Path $Path -Text ($Object | ConvertTo-Json -Depth $Depth)
}

function Get-FileSha256 {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-UnderIso {
    # Pre-write guard: the target must live inside the isolation root.
    param([Parameter(Mandatory)][string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetFullPath($script:IsoRoot)
    if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "refusing to write outside the isolation root: $full (allowed root: $root)"
    }
    return $full
}

function New-IsoDirectories {
    foreach ($d in @($script:IsoRoot, $script:HostsRoot, $script:OpencodeHome,
                     (Join-Path $script:OpencodeHome '.config\opencode'),
                     $script:OpencodeProject, $script:DeeptutorSettings, $script:EvidenceDir)) {
        if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    }
}

function Get-SanitizedNoProxy {
    # A host NO_PROXY containing bracketed IPv6 (e.g. [::1]) makes the bundled clients
    # fail with "Invalid port"; mvp-protocol lists this as startup pitfall #2.
    param([string]$Value = $env:NO_PROXY)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    $parts = $Value -split ',' | ForEach-Object {
        $_.Trim().TrimStart('[').TrimEnd(']')
    } | Where-Object { $_ -ne '' }
    return ($parts -join ',')
}

function Get-RealConfigBaseline {
    return [ordered]@{
        captured_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        files = [ordered]@{
            opencode_json          = [ordered]@{ path = $script:RealOpencodeConfig;   sha256 = (Get-FileSha256 $script:RealOpencodeConfig) }
            deeptutor_mcp_json     = [ordered]@{ path = $script:RealDeeptutorMcp;     sha256 = (Get-FileSha256 $script:RealDeeptutorMcp) }
            deeptutor_catalog_json = [ordered]@{ path = $script:RealDeeptutorCatalog; sha256 = (Get-FileSha256 $script:RealDeeptutorCatalog) }
        }
    }
}
