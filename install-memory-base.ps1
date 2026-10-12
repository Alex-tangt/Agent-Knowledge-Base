<#!
Install the current OpenViking-backed personal memory base.
Example: powershell -File install-memory-base.ps1 -Python C:\Python312\python.exe
Remaining arguments are forwarded to the same memory-base install command.
#>
param(
    [string]$Python = "python",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$InstallArguments
)

$ErrorActionPreference = "Stop"
& $Python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Personal memory base requires Python 3.12. Pass -Python <python.exe>."
}
Push-Location $PSScriptRoot
try {
    & $Python -m memory_base install @InstallArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Personal memory base installation failed (exit $LASTEXITCODE)."
    }
} finally {
    Pop-Location
}
