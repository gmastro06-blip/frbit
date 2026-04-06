# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**waypoint-navigator** — A BattlEye-safe Tibia bot that uses OBS screen-capture and hardware-level input only. No memory reads, no packet injection. The bot sees the screen like a human and acts like one.

Working directory for most tasks: `waypoint-navigator/`

## Commands

```bash
# Install
cd waypoint-navigator
pip install -r requirements.txt
# With optional extras (ocr, capture, interception, etc.)
pip install -e ".[ocr,capture,interception,dev]"

# Run tests
python -m pytest tests/ -q
python -m pytest tests/test_navigator.py -v          # single file
python -m pytest tests/test_navigator.py::TestClass::test_method -v  # single test
python -m pytest tests/ -m smoke -q                  # smoke subset only
python -m pytest tests/ -m "not slow and not obs" -q # skip slow/live tests

# Coverage (informational during dev; release gate requires --cov-fail-under=70)
python -m pytest tests/ --cov=src --cov-report=html

# Type checking & linting
python -m mypy src/ --ignore-missing-imports
python -m ruff check src/

# Build Windows executable
python -m PyInstaller build.spec

# CLI entry points
python main.py --help
python main.py navigate --sx 32369 --sy 32241 --ex 32343 --ey 32211 --floor 7
python main.py navigate-name "thais depot" "thais temple"
python main.py search-waypoints thais          # fuzzy waypoint search
python main.py floor-stats 7                   # walkability stats for a floor
python main.py show-floor 7                    # render floor map
python main.py calibrate
python main.py track --dest-name "temple"

# Tiered field tests (require live game)
python tools/run_nivel0_tests.py   # unit (no hardware)
python tools/run_nivel1_tests.py   # capture & vision
# ... up to tools/run_nivel7_tests.py (full integration)
```

## Architecture

The bot is structured in layers that build on each other:

### Coordinate System
All positions use absolute Tibia world coordinates (`Coordinate` in `models.py`). Floor 7 = ground level. Pixels are only computed at render time via `Coordinate.to_pixel()`. Never store pixel coordinates as canonical positions.

### Frame Pipeline
`frame_capture.py` + `frame_sources.py` → OBS Windowed Projector via HWND + BitBlt → `FrameCache` (circular buffer) → all vision modules consume frames from here. OBS is required because Tibia blocks direct screen capture. `frame_watchdog.py` alerts on stalls; `frame_quality.py` filters corrupt frames.

Capture backend fallback order: **OBS WebSocket → DXCam → WGC → MSS**.

### Vision Modules (all read frames, no game memory)
- `minimap_radar.py` + `minimap_radar_utils.py` — template-matches the minimap → player XYZ. Primary position source.
- `minimap_calibrator.py` — calibrates minimap ROI (auto-detects tiles_wide).
- `position_resolver.py` — fuses OCR coords + minimap → canonical position.
- `hpmp_detector.py` — reads HP/MP bars via pixel color analysis + EasyOCR.
- `combat_manager.py` + `combat_manager_helpers.py` + `combat_manager_loop.py` — detects battle list, manages attack targeting.
- `condition_monitor.py` — reads status icons (poison, burning, etc.).
- `character_detector.py` — detects player character on screen.
- `ui_detection.py` — detects generic UI elements.
- `storage_detector.py` — detects depot/storage container UI state.
- `image_processing.py` — shared image processing utilities.
- `adaptive_roi.py` — dynamically adjusts regions of interest.

### Pathfinding
`map_loader.py` downloads floor PNGs from tibiamaps.github.io → `pathfinder.py` runs 8-directional A* on the walkability grid → `navigator.py` exposes the high-level API (single-floor + multi-floor via `transitions.py`). Routes are JSON files in `routes/`. `obstacle_analyzer.py` analyzes blocking obstacles; `stuck_detector.py` detects and recovers from stuck states (nudge → escape → abort chain). `route_validator.py` validates route JSON against the v4 schema.

#### `src/navigation/` — Route recording tools
- `route_recorder.py`, `waypoint_recorder.py`, `waypoint_logger.py` — record and log routes interactively.

### Input Control
`input_controller.py` uses Windows `SendInput` via ctypes. `input_backends.py` abstracts multiple backends. `client_actions.py` wraps high-level game actions (open backpack, click NPC, etc.). `humanizer.py` adds jittered delays and fatigue accumulation. `mouse_bezier.py` generates natural curves. Alternative backends: `human_input_system/` (HIS), `pico2/` (Raspberry Pi Pico 2 USB HID firmware), `arduino/tibia_hid/` (Arduino HID firmware).

Input backend fallback order: **Interception (kernel driver) → PostMessage → scancode (SendInput)**.

### Main Orchestrator: `session.py`
`BotSession` is split across multiple files for maintainability:
- `session.py` — core class and entry point
- `session_startup.py` — initialization sequence
- `session_threads.py` — daemon thread management
- `session_position.py` — position tracking logic
- `session_capture.py` — frame capture integration
- `session_monitoring.py` — health monitoring
- `session_safety.py` — safety checks (GM, PvP, death)
- `session_route_execution.py` — route step execution
- `session_script.py` — script engine integration
- `session_subsystems.py` — subsystem wiring
- `session_runtime.py` — main loop
- `session_watchdog.py` — watchdog integration
- `session_stats.py` — session statistics
- `session_stop.py` — shutdown sequence
- `session_persistence.py` — state save/restore
- `session_optional.py` — optional feature toggles
- `session_integrated.py` — integrated session variant

Background daemon threads: `healer.py` / `healer_runtime.py` (`AutoHealer` — HP/MP threshold → hotkey), `looter.py` / `looter_runtime.py` (`Looter` — corpse detection → right-click).

`EventBus` (pub/sub, synchronous) in `event_bus.py` decouples major events (e1=kill, e3=death, e4=heal, e15=GM, etc.)

### Script Engine: `script_executor.py`
Split across multiple files:
- `script_executor.py` — core executor
- `script_executor_walk.py` — movement instructions
- `script_executor_interaction.py` — object/NPC interaction
- `script_executor_trade.py` — NPC buy/sell instructions
- `script_executor_runtime.py` — runtime state machine
- `script_executor_state.py` — state persistence

Parses `.in` script files via `script_parser.py` + `script_parser_parsing.py`. Instruction types: `node`, `stand`, `label`, `call`, `wait`, `goto`, `if`, `use_hotkey`, `use_item`, `ladder`, `rope`, `action`. Scripts live in `routes/`.

### Depot & Trade System
- `depot_manager.py` + `depot_manager_runtime.py` — depot cycle (deposit loot, withdraw supplies).
- `trade_manager.py` — NPC buy/sell automation with template matching.
- `depot_orchestrator.py` — ties `InventoryManager` + `DepotManager` + `TradeManager` into a unified resupply workflow: poll needs → navigate → deposit → bank → buy → return to hunt.
- `inventory_manager.py` — tracks inventory capacity and supply levels.
- `storage_navigator.py` — navigates within depot/storage containers.
- `storage_state.py` — tracks open/closed state of storage containers.

### Multi-Session & Telemetry
- `multi_session.py` — run N BotSessions in parallel, each targeting a different Tibia window.
- `telemetry.py` — per-session stats (steps walked, kills, depot cycles, etc.) saved as JSON snapshots.
- `soak_monitor.py` — long-run soak test monitoring.
- `dashboard_server.py` + `dashboard.html` — web dashboard for live stats.

### Spawn & Hunting Management
- `spawn_manager.py` — multi-spawn routing; detects occupied hunting spots and falls back to alternatives.

### Config System
Each subsystem has its own JSON config file at the project root. ROI calibration for monitor 2 at 1920×1080. Use `python main.py calibrate` to recalibrate.

| File | Purpose |
|---|---|
| `hpmp_config.json` | HP/MP thresholds and hotkeys |
| `heal_config.json` | Healer settings |
| `combat_config.json` | Combat targeting (base) |
| `combat_config_druid.json` | Combat config for Druid |
| `combat_config_paladin.json` | Combat config for Paladin |
| `combat_config_sorcerer.json` | Combat config for Sorcerer |
| `minimap_config.json` | Minimap ROI and template paths |
| `condition_config.json` | Status condition thresholds |
| `detector_config.json` | Generic detector settings |
| `loot_config.json` | Loot filter list |
| `depot_config.json` | Depot cycle settings |
| `trade_config.json` | NPC trade item list |
| `chat_config.json` | Chat responder rules |

### Recovery & Anti-Detection
- `break_scheduler.py` — configurable auto-breaks
- `anti_kick.py` — prevents inactivity kick
- `stuck_detector.py` — stuck detection with escalating recovery (nudge → escape → abort)
- `death_handler.py` — death → respawn + re-equip flow
- `reconnect_handler.py` — network loss → reconnect
- `gm_detector.py`, `pvp_detector.py` — threat detection
- `chat_responder.py` — auto-respond to PMs
- `alert_system.py` — configurable alerts (sound, log, event)
- `action_verifier.py` — verifies that actions had the expected effect

### Infrastructure
- `protocols.py` — shared Protocol/ABC definitions
- `config_paths.py` — centralized config file path constants
- `preflight.py` — pre-start sanity checks (OBS running, window found, configs valid)
- `game_data.py` — static Tibia game data (item IDs, creature names, etc.)
- `visualizer.py`, `path_visualizer.py` — debug visualization overlays
- `walkability_overlay.py` — renders walkability grid overlay
- `monitor_gui.py` — GUI monitor panel
- `calibrator.py` — ROI calibration wizard

### Tools (`tools/`)
90+ utility scripts for calibration, diagnostics, and testing:
- **Calibration:** `calibrate_manual.py`, `calibrate_storage_tabs.py`, `calibrate_viewport.py`, `cli_roi_capture.py`, `certify_rois.py`
- **Diagnostics:** `diagnose_pipeline.py`, `debug_walker.py`, `debug_capture.py`, `preflight_check.py`
- **Route tooling:** `route_validator.py` (validates route JSON v4)
- **Template management:** `capture_templates.py`, `download_templates.py`, `validate_templates.py`, `generate_synthetic_templates.py`
- **Tiered field tests:** `run_nivel0_tests.py` … `run_nivel7_tests.py`
- **Monitoring:** `monitor_dashboard_smoke.py`, `soak_monitor.py`

## Hunting Spot Setups

- `routes/wasp_thais/` — Thais wasp cave hunt configs (`setup_ek.json`, `setup_ek_nopvp.json`, `setup_rp.json`, `waypoints.in`, route JSONs including live variant)
- `buy_blessing/` — Blessing purchase automation (`setup.json`, `setup_ek.json`, class variants)

## Key Design Rules

- **No memory reads, no packet injection** — vision-only by design (BattlEye safety)
- **OBS required** for capture — Tibia blocks direct BitBlt from other processes
- **Hardware input only** — SendInput or Pico2/Arduino HID, never pyautogui
- **All configs are JSON** — validated per-module, never hardcoded thresholds
- **Typed codebase** — mypy strict mode, all public APIs have type hints
- **Threading model** — main thread navigates, daemon threads heal/loot
- **Test offline by default** — 112 test files use mock frames/coords, no live game required for unit tests. `tests/conftest.py` provides synthetic BGR frame fixtures (`blank_frame`, `hp75_mp50_frame`, etc.) so vision tests run without OBS. Tests needing live capture are marked `@pytest.mark.obs`.
- **Python 3.12+** — required (pyproject.toml enforces this)
- **Split files** — large modules are split (session_*.py, script_executor_*.py) to keep files manageable; treat each group as one logical unit

## Route JSON Format

Routes in `routes/` follow v4 format. Key fields: `waypoints` (list of named Coordinates), `instructions` (script nodes), `walkable_overrides`, `blocked_regions`. Full spec and `.in` script instruction reference is in `routes/README.md`.

## Review System (separate tool)

`review_system/` is an independent code review tool with its own CLI:
```bash
python review_system/cli.py review waypoint-navigator/ --depth deep
```
