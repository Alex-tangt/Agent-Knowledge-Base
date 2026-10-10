# run_supplement_authz.ps1 -- M6 supplement, end to end, in ONE shell.
#
# Why one shell: the authz profile needs an api_key-mode root key, and this line keeps it in the
# PROCESS ENVIRONMENT only -- never in a file, never in git, never in evidence. The same shell
# therefore has to generate it, start the server, provision identities/ACL/secrets, and run the two
# host-role checks. Re-running the script restarts the instance with a fresh root key and
# re-provisions everything (the registered identities live in the instance workspace, so their user
# keys survive; this script re-fetches them via the admin API).
#
# Proves, host-side (DeepTutor's own MCP client path):
#   * read-only identity  -> can READ the granted restricted subtree, WRITE inside it is denied
#   * writable identity   -> READ + WRITE inside the same subtree both work
#
# ASCII-only on purpose (repo .ps1 files are BOM-less; PS 5.1 reads them as ANSI).

[CmdletBinding()]
param(
    [string]$EvidenceDir = $null
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\lib_iso.ps1"
if (-not $EvidenceDir) { $EvidenceDir = $script:EvidenceDir }
$supDir = Join-Path $EvidenceDir 'supplement-authz'
New-Item -ItemType Directory -Force -Path $supDir | Out-Null

$py = Join-Path 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts' 'python.exe'
$dtpy = $script:DeeptutorPython
$authzHome = Join-Path $script:HostsRoot 'deeptutor-home-authz'
$scope = 'viking://resources/m6-scope'

# ---- root key: process env only ---------------------------------------------
$bytes = New-Object byte[] 16
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$env:OPENVIKING_MVP_HOST_ROOT_KEY = 'host-' + (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
Write-Host "== M6 supplement: authz profile + host-side read-only proof =="
Write-Host "   root key: process env only ($($env:OPENVIKING_MVP_HOST_ROOT_KEY.Substring(0,10))...; not written anywhere)"

# ---- 1. instance in authz profile -------------------------------------------
& $py host_server.py stop 2>&1 | Select-Object -Last 1
& $py host_server.py start --profile authz 2>&1 | Select-Object -Last 1
$health = Invoke-RestMethod http://127.0.0.1:8782/health
Write-Host "   /health -> auth_mode=$($health.auth_mode)"

# ---- 2. provision identities + restricted subtree + secret store -------------
& $dtpy host_identity.py --home $authzHome --json-out (Join-Path $supDir 'identity.json') > (Join-Path $supDir 'identity.log') 2>&1
Write-Host "   identity provisioning exit=$LASTEXITCODE"
Get-Content (Join-Path $supDir 'identity.log') | Select-String -Pattern '^\[(PASS|FAIL)\]' | ForEach-Object { "   $($_.Line)" }

# ---- 3. host-side check, role = read-only -----------------------------------
Write-Host "-- role=read-only (host-ro) --"
& powershell -File (Join-Path $PSScriptRoot 'setup_authz_supplement.ps1') -User 'host-ro' -SecretField 'api_key_ro' | Out-Null
& $dtpy host_deeptutor.py --home $authzHome `
    --authn-mode 'api_key (account ACL enabled; identity host-ro role=user)' `
    --read-uri "$scope/seed.md" `
    --write-uri "$scope/ro-attempt.md" `
    --expect-write-denied --skip-peer-read `
    --marker 'M6-SUPPLEMENT-RO' `
    --json-out (Join-Path $supDir 'host-ro-readonly.json') > (Join-Path $supDir 'host-ro.log') 2>&1
Write-Host "   host-ro exit=$LASTEXITCODE"
Get-Content (Join-Path $supDir 'host-ro.log') | Select-String -Pattern '^\[(PASS|FAIL)\]' | ForEach-Object { "   $($_.Line)" }

# ---- 4. host-side check, role = writable ------------------------------------
Write-Host "-- role=writable (host-rw) --"
& powershell -File (Join-Path $PSScriptRoot 'setup_authz_supplement.ps1') -User 'host-rw' -SecretField 'api_key_rw' | Out-Null
& $dtpy host_deeptutor.py --home $authzHome `
    --authn-mode 'api_key (account ACL enabled; identity host-rw role=admin)' `
    --read-uri "$scope/seed.md" `
    --write-uri "$scope/rw-written.md" `
    --skip-peer-read `
    --marker 'M6-SUPPLEMENT-RW' `
    --json-out (Join-Path $supDir 'host-rw-writable.json') > (Join-Path $supDir 'host-rw.log') 2>&1
Write-Host "   host-rw exit=$LASTEXITCODE"
Get-Content (Join-Path $supDir 'host-rw.log') | Select-String -Pattern '^\[(PASS|FAIL)\]' | ForEach-Object { "   $($_.Line)" }

# ---- 5. did the denied write leave anything on disk? ------------------------
$roFile = Join-Path $script:IsoRoot 'data\viking\default\resources\m6-scope\ro-attempt.md'
$rwFile = Join-Path $script:IsoRoot 'data\viking\default\resources\m6-scope\rw-written.md'
Write-Host "-- disk check --"
Write-Host "   ro-attempt.md exists (must be False): $(Test-Path -LiteralPath $roFile)"
Write-Host "   rw-written.md exists (must be True) : $(Test-Path -LiteralPath $rwFile)"
Write-Host "done. evidence: $supDir"
