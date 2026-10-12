# verify_isolation.ps1 -- independent check: real configs untouched, isolated copies
# point only at this line's instance (8782), never at 8781 / 8783 / 8765.
#
# ASCII-only on purpose (repo .ps1 files are BOM-less; PS 5.1 reads them as ANSI).
#
# Usage: powershell -File verify_isolation.ps1 [-ServicePort 8782]

[CmdletBinding()]
param(
    [int]$ServicePort = 8782,
    [int[]]$ForbiddenPorts = @(8781, 8783, 8765)
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\lib_iso.ps1"

$fail = @()

# 1) real configs unchanged (compared against the baseline captured by setup_isolation.ps1)
$baselinePath = Join-Path $script:EvidenceDir 'real-config-baseline.json'
if (-not (Test-Path -LiteralPath $baselinePath)) {
    $fail += "missing baseline: $baselinePath (run setup_isolation.ps1 first)"
} else {
    $baseline = Get-Content -LiteralPath $baselinePath -Raw | ConvertFrom-Json
    $now = Get-RealConfigBaseline
    foreach ($k in @('opencode_json', 'deeptutor_mcp_json', 'deeptutor_catalog_json')) {
        $b = $baseline.files.$k.sha256
        $n = $now.files.$k.sha256
        $status = 'FAIL'
        if ($b -eq $n) { $status = 'PASS' }
        $shown = '(absent)'
        if ($n) { $shown = $n }
        Write-Host ("   {0,-24} {1}  {2}" -f $k, $status, $shown)
        if ($b -ne $n) { $fail += "real config changed: $k (baseline $b -> now $n)" }
    }
}

# 2) isolated copies exist and carry no forbidden port
$badUrls = @()
if (-not (Test-Path -LiteralPath $script:OpencodeCfg))  { $fail += "missing isolated opencode config: $($script:OpencodeCfg)" }
if (-not (Test-Path -LiteralPath $script:DeeptutorMcp)) { $fail += "missing isolated DeepTutor mcp.json: $($script:DeeptutorMcp)" }

Get-ChildItem -LiteralPath $script:HostsRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in @('.json', '.yaml', '.yml', '.conf') } |
    ForEach-Object {
        $text = Get-Content -LiteralPath $_.FullName -Raw
        foreach ($p in $ForbiddenPorts) {
            if ($text -match (":$p\b")) {
                $badUrls += ("{0} references forbidden port {1}" -f $_.FullName, $p)
            }
        }
    }
if ($badUrls.Count -gt 0) { $fail += $badUrls } else { Write-Host ("   isolated copies ports   : PASS (no {0})" -f ($ForbiddenPorts -join '/')) }

# 3) the isolated service URL must target this line's port
$wantUrl = "http://127.0.0.1:$ServicePort/mcp"
foreach ($f in @($script:OpencodeCfg, $script:DeeptutorMcp)) {
    if (Test-Path -LiteralPath $f) {
        $text = Get-Content -LiteralPath $f -Raw
        if ($text -notmatch [regex]::Escape($wantUrl)) {
            $fail += "$f does not point at $wantUrl"
        }
    }
}

Write-Host ""
if ($fail.Count -gt 0) {
    Write-Host "== verify_isolation: FAIL =="
    $fail | ForEach-Object { Write-Host "  - $_" }
    exit 1
}
Write-Host "== verify_isolation: PASS =="
exit 0
