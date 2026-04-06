# run_release_prod_full.ps1
# Full production release gate — runs repo status check then module audit.
# Usage: .\run_release_prod_full.ps1
# Exit:  0 = OPERATIONAL_REAL   1 = NOT_READY

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT   = $PSScriptRoot
$PYTHON = "$ROOT\..\..\.venv\Scripts\python.exe"

if (-not (Test-Path $PYTHON)) {
    # Fallback: use python on PATH
    $PYTHON = "python"
}

function Run-Gate {
    param([string]$Label, [string]$Script)
    Write-Host ""
    Write-Host "=" * 60
    Write-Host "GATE: $Label"
    Write-Host "=" * 60
    & $PYTHON $Script
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[FAIL] Gate '$Label' returned $LASTEXITCODE" -ForegroundColor Red
        return $false
    }
    return $true
}

$ok1 = Run-Gate "repo_status"   "$ROOT\audit_repo_status.py"
$ok2 = Run-Gate "prod_full"     "$ROOT\audit_prod_full.py"

Write-Host ""
Write-Host "=" * 60
if ($ok1 -and $ok2) {
    Write-Host "FINAL_DECISION: OPERATIONAL_REAL" -ForegroundColor Green
    exit 0
} else {
    Write-Host "FINAL_DECISION: NOT_READY" -ForegroundColor Red
    exit 1
}
