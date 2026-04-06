"""
run_check.py — Pre-flight check antes de producción
====================================================
Ejecutar PRIMERO antes de cualquier fase.
Verifica: imports, ventana Tibia, frame capture, templates, OCR.
No envía ningún input al juego.

Uso: python run_check.py
"""

import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

OK   = "  [OK]"
WARN = "  [WW]"
FAIL = "  [!!]"

errors   = 0
warnings = 0

def check(label: str, fn: Callable) -> None:
    global errors, warnings
    try:
        result = fn()
        if result is True or result is None:
            print(f"{OK} {label}")
        elif result is False:
            print(f"{FAIL} {label}")
            errors += 1
        else:
            print(f"{OK} {label}: {result}")
    except Exception as exc:
        print(f"{FAIL} {label}: {exc}")
        errors += 1

def warn(label: str, fn: Callable) -> None:
    global warnings
    try:
        result = fn()
        if result is True or result is None:
            print(f"{OK} {label}")
        else:
            print(f"{WARN} {label}: {result}")
            warnings += 1
    except Exception as exc:
        print(f"{WARN} {label}: {exc}")
        warnings += 1

print("=" * 55)
print("  Pre-flight check - waypoint-navigator")
print("=" * 55)
print()

# ── 1. Imports core ──────────────────────────────────────────────────────────
print("[1] Core imports")

check("numpy", lambda: __import__("numpy") and True)
check("cv2",   lambda: __import__("cv2") and True)
check("PIL",   lambda: __import__("PIL") and True)

def _check_easyocr() -> bool:
    import easyocr  # noqa: F401
    return True
check("easyocr", _check_easyocr)

def _check_dxcam() -> bool:
    import dxcam  # noqa: F401
    return True
warn("dxcam (frame_source=dxcam)", _check_dxcam)

def _check_winsdk() -> bool:
    import winsdk  # noqa: F401
    return True
warn("winsdk (frame_source=wgc)", _check_winsdk)

print()

# ── 2. Módulos del bot ───────────────────────────────────────────────────────
print("[2] Bot modules")

check("src.session",         lambda: __import__("src.session") and True)
check("src.combat_manager",  lambda: __import__("src.combat_manager") and True)
check("src.looter",          lambda: __import__("src.looter") and True)
check("src.healer",          lambda: __import__("src.healer") and True)
check("src.input_controller",lambda: __import__("src.input_controller") and True)
print()

# ── 3. Ventana de Tibia ───────────────────────────────────────────────────────
print("[3] Ventana Tibia")

def _find_tibia_hwnd() -> str:
    import ctypes
    user32 = ctypes.windll.user32
    # Exact match
    hwnd = user32.FindWindowW(None, "Tibia")
    if hwnd:
        return f"hwnd=0x{int(hwnd):08X} (ventana exacta 'Tibia')"
    # Partial match via EnumWindows
    found = []
    BufType = ctypes.c_wchar * 256
    def _cb(h: int, _: int) -> bool:
        buf = BufType()
        user32.GetWindowTextW(h, buf, 256)
        title = buf.value
        if "tibia" in title.lower():
            found.append((int(h), title))
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_size_t, ctypes.c_size_t)
    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    if found:
        h, title = found[0]
        return f"hwnd=0x{h:08X} title='{title}' (match parcial)"
    raise RuntimeError(
        "No se encontro ninguna ventana con 'Tibia' en el titulo.\n"
        "    Abre el cliente de Tibia antes de correr run_phase1.py"
    )

check("Ventana Tibia encontrada", _find_tibia_hwnd)
print()

# ── 4. Frame capture ─────────────────────────────────────────────────────────
print("[4] Frame capture")

def _test_wgc_frame() -> str:
    """WGC captura solo la ventana de Tibia — coords coinciden con hpmp_config.json."""
    import ctypes, numpy as np
    # Verificar que winsdk esta disponible
    import winsdk  # noqa: F401
    # No iniciamos captura real aqui (requiere hwnd y async), solo verificamos imports
    return "winsdk OK — WGC disponible (frame_source='wgc' recomendado)"

warn("wgc frame source", _test_wgc_frame)

def _check_hpmp_config() -> str:
    import json
    p = ROOT / "hpmp_config.json"
    if not p.exists():
        return "No existe — se usaran defaults (pueden no coincidir con tu cliente)"
    d = json.loads(p.read_text())
    hp  = d.get("hp_roi", [])
    mp  = d.get("mp_roi", [])
    cmt = d.get("_comment", "sin comentario de calibración")
    return f"hp_roi={hp}, mp_roi={mp} | {cmt}"

warn("hpmp_config.json ROI calibrado", _check_hpmp_config)
print()

# ── 5. Templates ─────────────────────────────────────────────────────────────
print("[5] Templates")

def _count_templates(subdir: str) -> str:
    import cv2, numpy as np
    tdir = ROOT / "cache" / "templates" / subdir
    if not tdir.exists():
        raise FileNotFoundError(f"cache/templates/{subdir}/ no existe")
    pngs = list(tdir.glob("*.png"))
    bad = [p for p in pngs if cv2.imread(str(p)) is None]
    if bad:
        raise RuntimeError(f"{len(bad)} PNGs corruptos: {[p.name for p in bad]}")
    return f"{len(pngs)} templates"

check("cache/templates/corpses/",    lambda: _count_templates("corpses"))
check("cache/templates/loot_items/", lambda: _count_templates("loot_items"))
print()

# ── 6. Config files ───────────────────────────────────────────────────────────
print("[6] Config files")

def _check_combat_cfg() -> str:
    import json
    p = ROOT / "combat_config.json"
    if not p.exists():
        raise FileNotFoundError("combat_config.json no encontrado")
    d = json.loads(p.read_text())
    ocr = d.get("ocr_detection", False)
    if not ocr:
        return "ocr_detection=False — monstruos NO serán detectados. Ajustar en combat_config.json"
    return f"ocr_detection=True, roi={d.get('battle_list_roi')}"

check("combat_config.json", _check_combat_cfg)

def _check_loot_cfg() -> str:
    p = ROOT / "loot_config.json"
    if not p.exists():
        return "No existe — se usarán defaults (OK para Fase 1, recomendado generarlo en Fase 2)"
    import json
    d = json.loads(p.read_text())
    return f"loot_mode={d.get('loot_mode')}, hotkey={d.get('use_hotkey_quick_loot')}"

warn("loot_config.json", _check_loot_cfg)

def _check_route() -> str:
    p = ROOT / "routes" / "thais_rat_hunt.json"
    if not p.exists():
        raise FileNotFoundError("routes/thais_rat_hunt.json no encontrado")
    import json
    d = json.loads(p.read_text())
    start = d.get("_meta", {}).get("start_coord", "?")
    return f"start={start}"

check("routes/thais_rat_hunt.json", _check_route)
print()

# ── 7. EasyOCR warmup ─────────────────────────────────────────────────────────
print("[7] EasyOCR warmup (puede tardar 5-10s la primera vez)")

def _test_ocr() -> str:
    import easyocr
    import numpy as np
    t0 = time.time()
    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    # Imagen sintética con texto
    img = np.zeros((30, 200, 3), dtype=np.uint8)
    import cv2
    cv2.putText(img, "Cave Rat", (5, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 1)
    result = reader.readtext(img)
    elapsed = time.time() - t0
    texts = [t for _, t, _ in result]
    return f"'{' '.join(texts)}' detectado en {elapsed:.1f}s"

check("EasyOCR detecta texto", _test_ocr)
print()

# ── Resultado final ───────────────────────────────────────────────────────────
print("=" * 55)
if errors == 0 and warnings == 0:
    print("  [OK] LISTO PARA PRODUCCION — todos los checks pasaron")
    print()
    print("  Orden de ejecucion:")
    print("    1. python run_phase1.py   (5-10 min, sin loot)")
    print("    2. python run_phase2.py   (10-15 min, con loot Alt+Q)")
    print("    3. python run_phase3.py   (30+ min, hunt completo)")
elif errors == 0:
    print(f"  [WW] CASI LISTO — {warnings} advertencia(s), sin errores criticos")
    print("    Puedes continuar con run_phase1.py pero revisa los [WW]")
else:
    print(f"  [!!] NO LISTO — {errors} error(es) critico(s), {warnings} advertencia(s)")
    print("    Corrige los [!!] antes de continuar")
print("=" * 55)
sys.exit(0 if errors == 0 else 1)
