# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for waypoint-navigator.

Produces a single .exe with all DLLs bundled inside — hides the Python
runtime, cv2, mss, interception, etc. from naive process/DLL scanners.

Usage:
    pip install pyinstaller
    pyinstaller build.spec

Output: dist/navigator.exe  (single file, ~80-120 MB)
"""

import os
import sys
from pathlib import Path

block_cipher = None

# Paths
HERE = Path(os.path.abspath(SPECPATH))

a = Analysis(
    [str(HERE / 'main.py')],
    pathex=[str(HERE)],
    binaries=[],
    datas=[
        # Data dirs → obfuscated names to avoid file-scanning fingerprints
        (str(HERE / 'routes'), 'r'),
        (str(HERE / 'cache' / 'markers.json'), 'cache'),
        (str(HERE / 'cache' / 'templates'), str(Path('cache') / 't')),
        (str(HERE / 'maps'), 'm'),
        # Config files → short generic names
        (str(HERE / 'combat_config.json'), 'cc.json'),
        (str(HERE / 'combat_config_druid.json'), 'cc_d.json'),
        (str(HERE / 'combat_config_paladin.json'), 'cc_p.json'),
        (str(HERE / 'combat_config_sorcerer.json'), 'cc_s.json'),
        (str(HERE / 'detector_config.json'), 'dc.json'),
        (str(HERE / 'hpmp_config.json'), 'hm.json'),
        (str(HERE / 'minimap_config.json'), 'mc.json'),
        (str(HERE / 'trade_config.json'), 'tc.json'),
    ],
    hiddenimports=[
        'src',
        'src.session',
        'src.navigator',
        'src.pathfinder',
        'src.healer',
        'src.combat_manager',
        'src.looter',
        'src.input_controller',
        'src.minimap_radar',
        'src.death_handler',
        'src.reconnect_handler',
        'src.anti_kick',
        'src.humanizer',
        'src.frame_capture',
        'src.frame_cache',
        'src.hpmp_detector',
        'src.dashboard_server',
        'src.depot_manager',
        'src.depot_orchestrator',
        'src.condition_monitor',
        'src.stuck_detector',
        'src.break_scheduler',
        'src.event_bus',
        'src.alert_system',
        'src.telemetry',
        'src.mouse_bezier',
        'src.ui_detection',
        'src.character_detector',
        'src.soak_monitor',
        'src.pvp_detection',
        'src.action_verifier',
        'src.calibrator',
        'src.map_loader',
        'src.adaptive_roi',
        'src.models',
        'src.visualizer',
        'mss',
        'cv2',
        'numpy',
        'websockets',
        'psutil',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude dev/test dependencies that shouldn't be in production
        'pytest', 'mypy', 'matplotlib', 'tkinter',
        # Exclude EasyOCR/PyTorch (huge, optional — use only if needed)
        'easyocr', 'torch', 'torchvision',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    # Generic name — doesn't scream "bot"
    name='navigator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # Compress with UPX if available
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,        # Keep console for logging; set False for silent
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Icon (optional — use a random .ico if you have one)
    # icon='icon.ico',
)
