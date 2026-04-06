# ═══════════════════════════════════════════════════════════════════
# Live Test Launchers — Quick PowerShell commands for T1-T5
# ═══════════════════════════════════════════════════════════════════
#
# Prerequisites:
#   - Tibia client open, logged in, character at Thais Temple (32369,32241,7)
#   - OBS Projector window "Proyector" visible (PrintWindow capture)
#   - Pico 2 on COM4
#   - Hotkeys: F1=heal, F2=mana, F3=emergency, F7-F10=attack
#
# Usage:
#   .\run_live_tests.ps1 t1       # Navigation
#   .\run_live_tests.ps1 t2       # Combat + heal + loot
#   .\run_live_tests.ps1 t3       # Death recovery
#   .\run_live_tests.ps1 t4       # Reconnect
#   .\run_live_tests.ps1 t5       # AFK soak 30 min
#   .\run_live_tests.ps1 pre      # Preflight only
# ═══════════════════════════════════════════════════════════════════

param(
    [Parameter(Position=0)]
    [ValidateSet("pre", "t1", "t2", "t3", "t4", "t5", "all")]
    [string]$Test = "pre"
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot

$py   = "c:\Users\gmast\Documents\frbit\.venv\Scripts\python.exe"
$route = "routes/thais_rat_hunt.json"

# Common base arguments
$base = @(
    "main.py", "run",
    "--route", $route,
    "--window", "Tibia",
    "--pico", "--pico-port", "COM4",
    "--position-source", "minimap",
    "--start-pos", "32369,32241,7",
    "--frame-source", "printwindow",
    "--frame-window", "Proyector",
    "--start-delay", "5"
)

$heal = @(
    "--heal", "70",
    "--emergency-pct", "30",
    "--mana-pct", "30",
    "--heal-vk", "0x70",
    "--emergency-vk", "0x72",
    "--mana-vk", "0x71"
)

function Show-Banner($title) {
    Write-Host "`n$("=" * 65)" -ForegroundColor Cyan
    Write-Host "  $title" -ForegroundColor White
    Write-Host "$("=" * 65)`n" -ForegroundColor Cyan
}

switch ($Test) {
    "pre" {
        Show-Banner "PREFLIGHT CHECK"
        & $py tools/preflight_check.py --route $route
    }

    "t1" {
        Show-Banner "T1 — NAVIGATION (no combat)"
        Write-Host "  Walk the rat hunt route for ~5 min. No combat, just movement." -ForegroundColor Yellow
        Write-Host "  Ctrl+C to stop.`n"
        & $py @base @heal --loop
    }

    "t2" {
        Show-Banner "T2 — COMBAT + HEAL + LOOT"
        Write-Host "  Full hunt loop: walk, fight rats, heal, loot." -ForegroundColor Yellow
        Write-Host "  Ctrl+C to stop.`n"
        & $py @base @heal --loop --combat --class knight --loot
    }

    "t3" {
        Show-Banner "T3 — DEATH RECOVERY"
        Write-Host "  After bot starts, KILL your character manually." -ForegroundColor Red
        Write-Host "  Bot should: detect death -> respawn -> re-equip -> resume." -ForegroundColor Yellow
        Write-Host "  Ctrl+C to stop.`n"
        & $py @base @heal --loop --combat --class knight `
            --re-equip "0x75,0x76" --max-deaths 2
    }

    "t4" {
        Show-Banner "T4 — DISCONNECT / RECONNECT"
        Write-Host "  After bot starts, DISCONNECT the network briefly." -ForegroundColor Red
        Write-Host "  Bot should: detect disconnect -> wait -> reconnect -> resume." -ForegroundColor Yellow
        Write-Host "  Ctrl+C to stop.`n"
        & $py @base @heal --loop --combat --class knight
    }

    "t5" {
        Show-Banner "T5 — SOAK TEST (30+ min AFK)"
        Write-Host "  Full autonomous: combat, heal, loot, breaks, anti-kick, GM detect." -ForegroundColor Yellow
        Write-Host "  Runs for 30+ min. Dashboard at http://localhost:8080" -ForegroundColor Green
        Write-Host "  Ctrl+C to stop.`n"
        & $py @base @heal --loop --combat --class knight --loot `
            --gm-detector --dashboard --anti-kick-idle 300 `
            --re-equip "0x75,0x76"
    }

    "all" {
        Show-Banner "RUNNING ALL TESTS (T1-T5)"
        foreach ($t in @("t1","t2","t3","t4","t5")) {
            & $PSCommandPath $t
            Write-Host "`nPress Enter for next test..." -ForegroundColor Gray
            Read-Host
        }
    }
}

Pop-Location
