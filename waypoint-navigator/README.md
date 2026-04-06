# WaypointNavigator 🗺️

A Python waypoint navigation system for the [Tibia](https://www.tibia.com/) MMORPG,
powered by map data from [tibiamaps/tibia-map-data](https://github.com/tibiamaps/tibia-map-data).
**BattlEye-safe** — operates exclusively via OBS screen-capture; never reads game memory.

## Features

| Module | What it does |
|---|---|
| **A\* Pathfinding** | Shortest walkable path on any of the 16 Tibia floors |
| **Multi-floor Routing** | Dijkstra across floor transitions (stairs, ladders, ropes) |
| **Named Waypoints** | Search locations by name from the official `markers.json` |
| **Script Runner** | `.in` scripts with `goto`, `if hp/mp`, `wait`, `use_hotkey`, `use_item` |
| **Auto-Healer** | Background thread fires hotkeys when HP/MP drops below threshold |
| **Looter** | Template-match corpses → right-click open → collect whitelist items |
| **Depot Manager** | Automate depot cycle: deposit stack → bank → close |
| **Input Controller** | Interception, scancode, PostMessage and hardware-backed input options |
| **Minimap Radar** | Template-match OBS minimap → continuous XYZ without memory read |
| **Character Detector** | Legacy OCR compatibility shim; minimap-based positioning is the primary source |
| **Map Visualizer** | Render routes and markers on floor PNGs with matplotlib |
| **BotSession** | High-level orchestrator wiring all modules together |

---

## Tibia Map Coordinate System

| Axis | Range | Description |
|------|-------|-------------|
| X | 31 744 – 34 048 | West → East |
| Y | 30 976 – 32 768 | North → South |
| Z | 0 – 15 | Floor (0 = sky, **7 = ground**, 15 = deep underground) |

`px = x − 31744`  `py = y − 30976`

---

## Installation

```bash
cd waypoint-navigator
pip install -r requirements.txt
```

---

## Quick Start

### BotSession orchestrator

```python
from src.session import BotSession, SessionConfig

cfg = SessionConfig(
    route_file="routes/thais_depot_to_temple.json",
    heal_hp_pct=65,
    emergency_hotkey_vk=0x72,   # F3
    mana_hotkey_vk=0x71,         # F2
    loop_route=True,
    start_delay=3.0,
)
session = BotSession(cfg)
session.start()
input("Press Enter to stop …")
session.stop()
```

### Multi-floor navigation

```python
from src.navigator import WaypointNavigator
from src.models import Coordinate

nav = WaypointNavigator()
routes = nav.navigate_multifloor(
    start=Coordinate(32369, 32241, 7),
    end=Coordinate(32341, 32230, 8),
)
for r in routes:
    print(r.summary())
```

### Script Runner

```bash
python examples/auto_walker.py --script routes/example_cavebot.in --start-delay 5
```

Supported instructions: `node`, `label`, `goto`, `if hp/mp [< <= > >=] N goto LABEL`,
`use_hotkey VK`, `use_item ITEM_NAME`, `wait SECS`, `ladder`, `rope`, `action`.

### Auto-Healer

```python
from src.healer import AutoHealer, HealConfig
from src.input_controller import InputController

ctrl   = InputController("Tibia")
ctrl.find_target()
healer = AutoHealer(HealConfig(heal_hotkey_vk=0x70), ctrl=ctrl)
healer.start()
healer.stop()
```

### Depot Manager

```python
from src.depot_manager import DepotManager, DepotConfig
from src.input_controller import InputController

ctrl = InputController("Tibia"); ctrl.find_target()
mgr  = DepotManager(DepotConfig(), ctrl=ctrl)
mgr.set_frame_getter(lambda: obs_frame)
mgr.run_depot_cycle()
```

---

## Project Structure

```
waypoint-navigator/
├── main.py
├── requirements.txt
├── cache/
│   ├── markers.json               ← official Tibia waypoints (auto-downloaded)
│   ├── transitions.json           ← floor-change points (stairs/ladders/ropes)
│   └── templates/
├── routes/                        ← JSON waypoint routes + .in scripts
├── src/
│   ├── models.py                  ← Coordinate, Waypoint, Route, FloorTransition
│   ├── map_loader.py              ← Downloads & caches floor PNGs
│   ├── pathfinder.py              ← A* on walkability arrays
│   ├── navigator.py               ← WaypointNavigator (single + multi-floor)
│   ├── transitions.py             ← TransitionRegistry
│   ├── input_controller.py        ← Keyboard/mouse to Tibia window
│   ├── minimap_radar.py           ← Template-match minimap → XYZ
│   ├── character_detector.py      ← legacy OCR compatibility shim
│   ├── hpmp_detector.py           ← HP/MP bar reader
│   ├── healer.py                  ← AutoHealer background thread
│   ├── combat_manager.py          ← Battle-list monster detection + attack
│   ├── condition_monitor.py       ← Status icon detection
│   ├── looter.py                  ← Corpse detection + item collection
│   ├── depot_manager.py           ← Depot cycle automation
│   ├── script_parser.py           ← .in script parser + ScriptExecutor
│   ├── calibrator.py              ← Semi-auto ROI calibration
│   ├── visualizer.py              ← matplotlib map renderer
│   ├── session.py                 ← BotSession high-level orchestrator
│   └── __init__.py
├── examples/
│   └── auto_walker.py             ← Auto-walk + script runner + --depot flag
└── tests/                         ← 5k+ offline tests across navigation, session and tooling
```

---

## Running Tests

```bash
python -m pytest tests/ -q
# Large offline suite; targeted runs may need --cov-fail-under=0 when not running the full set
```

---

## Configuration Files

| File | Module |
|---|---|
| `minimap_config.json` | `MinimapRadar` |
| `detector_config.json` | `CharacterDetector` |
| `heal_config.json` | `AutoHealer` |
| `combat_config.json` | `CombatManager` |
| `loot_config.json` | `Looter` |
| `depot_config.json` | `DepotManager` |
| `session_config.json` | `BotSession` |

```bash
# Interactive ROI calibration (requires OBS running)
python src/calibrator.py --mode all --source obs-ws
```

---

## Data Sources

Map data is downloaded automatically on first use and cached in `cache/`:

- Floor PNGs: `https://tibiamaps.github.io/tibia-map-data/floor-{NN}-{map|path}.png`
- Markers: `https://raw.githubusercontent.com/tibiamaps/tibia-map-data/master/data/markers.json`

---

## License

MIT — see [tibiamaps/tibia-map-data](https://github.com/tibiamaps/tibia-map-data/blob/master/LICENSE-MIT.txt).
Tibia is made and copyrighted by [CipSoft GmbH](https://www.tibia.com/).
