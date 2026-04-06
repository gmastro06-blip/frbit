"""Centralized path resolver for config files and data directories.

In development mode, uses descriptive names (combat_config.json, routes/, etc.).
In frozen mode (PyInstaller .exe), resolves to obfuscated short names
(cc.json, r/, etc.) to avoid file-scanning fingerprints.
"""
from __future__ import annotations

import sys
from pathlib import Path

_FROZEN: bool = getattr(sys, "frozen", False)

if _FROZEN:
    _BASE: Path = Path(getattr(sys, "_MEIPASS", ""))
else:
    _BASE = Path(__file__).resolve().parent.parent

# ── Config files ─────────────────────────────────────────────────────────────
COMBAT_CONFIG       = _BASE / ("cc.json" if _FROZEN else "combat_config.json")
COMBAT_CONFIG_DRUID = _BASE / ("cc_d.json" if _FROZEN else "combat_config_druid.json")
COMBAT_CONFIG_PALA  = _BASE / ("cc_p.json" if _FROZEN else "combat_config_paladin.json")
COMBAT_CONFIG_SORC  = _BASE / ("cc_s.json" if _FROZEN else "combat_config_sorcerer.json")
DETECTOR_CONFIG     = _BASE / ("dc.json" if _FROZEN else "detector_config.json")
HPMP_CONFIG         = _BASE / ("hm.json" if _FROZEN else "hpmp_config.json")
MINIMAP_CONFIG      = _BASE / ("mc.json" if _FROZEN else "minimap_config.json")
TRADE_CONFIG        = _BASE / ("tc.json" if _FROZEN else "trade_config.json")

# ── Directories ──────────────────────────────────────────────────────────────
ROUTES_DIR    = _BASE / ("r" if _FROZEN else "routes")
TEMPLATES_DIR = _BASE / "cache" / ("t" if _FROZEN else "templates")
MAPS_DIR      = _BASE / ("m" if _FROZEN else "maps")
DATA_DIR      = _BASE / "data"
