#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Enqueue', 'Worker', 'Run', 'Status')]
    [string]$Action = 'Status',
    [string]$RepoRoot = '',
    [string]$Profile = '',
    [string]$CodexPath = '',
    [string]$AociPath = '',
    [ValidateRange(1, 86400)] [int]$IdleSeconds = 120
)
# Owner disabled AOCI on 2026-10-12. This tombstone never queues or launches work.
Write-Output 'AOCI is disabled by owner decision; no work was queued or started.'
if ($Action -eq 'Install') { exit 1 }
exit 0
