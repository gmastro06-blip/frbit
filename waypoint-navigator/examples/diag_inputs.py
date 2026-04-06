"""
diag_inputs.py — Diagnóstico completo de inputs hacia la ventana de Tibia.

Ejecutar con Tibia abierto:
    python examples/diag_inputs.py

Pruebas realizadas:
  1. Listar ventanas visibles que contengan "Tibia"
  2. Conectar al handle (hwnd)
  3. Forzar foco (focus_now)
  4. Send PostMessage WM_KEYDOWN/WM_KEYUP (tecla configurable)
  5. Send SendInput KEYEVENTF_SCANCODE (tecla configurable)
  6. Comparar retorno Win32 de PostMessageW (True/False, GetLastError)
  7. Verificar que la ventana siga válida tras cada envío

Al final imprime un resumen PASS/FAIL por cada etapa.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
import os

# Forzar stdout en UTF-8 para evitar UnicodeEncodeError en consolas cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.input_controller import (
    InputController,
    Key,
    list_windows,
    find_window,
    WM_KEYDOWN,
    WM_KEYUP,
    user32,
)

# ── Config ────────────────────────────────────────────────────────────────────
TARGET_TITLE = "Tibia"          # fragmento del título de ventana
TEST_VK      = Key.ARROW_RIGHT  # tecla de prueba (flecha derecha — 1 paso)
TEST_DELAY   = 0.08             # segundos entre KEYDOWN y KEYUP
PAUSE_SECS   = 2.0              # pausa antes de enviar (para enfocar a mano si hace falta)

SEP = "─" * 60

# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(msg: str) -> None:
    print(f"  [✓] {msg}")

def _fail(msg: str) -> None:
    print(f"  [✗] {msg}", file=sys.stderr)

def _info(msg: str) -> None:
    print(f"  [i] {msg}")


def _make_lparam(vk: int, is_keyup: int) -> int:
    """Reproduce exactamente el lParam que usa InputController."""
    import ctypes
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    repeat    = 1
    scancode  = scan & 0xFF
    extended  = 1 if vk in (0x25, 0x26, 0x27, 0x28) else 0
    context   = 0
    prev_state = 1 if is_keyup else 0
    transition = is_keyup
    return (
        (repeat & 0xFFFF)
        | (scancode << 16)
        | (extended << 24)
        | (context << 29)
        | (prev_state << 30)
        | (transition << 31)
    )


results: dict[str, bool] = {}

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  DIAGNÓSTICO DE INPUTS — WaypointNavigator")
print(SEP)

# ── 1. Listar ventanas ────────────────────────────────────────────────────────
print("\n[1] Buscando ventanas visibles…")
all_wins = list_windows(visible_only=True)
tibia_wins = [w for w in all_wins if TARGET_TITLE.lower() in w.title.lower()]

if tibia_wins:
    _ok(f"Encontradas {len(tibia_wins)} ventana(s) con '{TARGET_TITLE}':")
    for w in tibia_wins:
        _info(f"  hwnd={w.hwnd}  pid={w.pid}  título={w.title!r}")
    results["1_find_windows"] = True
else:
    _fail(f"No se encontró ninguna ventana con '{TARGET_TITLE}'")
    _info("Abre Tibia y vuelve a ejecutar este script.")
    all_titles = [w.title for w in all_wins[:20]]
    _info(f"Primeras 20 ventanas visibles: {all_titles}")
    results["1_find_windows"] = False

# ── 2. Conectar InputController ───────────────────────────────────────────────
print("\n[2] Conectando InputController…")
ctrl = InputController(
    target_title=TARGET_TITLE,
    input_method="postmessage",
    key_delay=TEST_DELAY,
)
found = ctrl.find_target()

if ctrl.is_connected():
    title_str = found.title if found else "unknown"
    _ok(f"Conectado: hwnd={ctrl.hwnd}  título={title_str!r}")
    results["2_connect"] = True
else:
    _fail("is_connected() devolvió False — no se puede continuar.")
    results["2_connect"] = False

if not results.get("2_connect"):
    print("\n" + SEP)
    print("  RESUMEN: no hay ventana de destino — abortar.")
    print(SEP)
    sys.exit(1)

hwnd = ctrl.hwnd

# ── 3. Verificar que la ventana es válida (IsWindow) ─────────────────────────
print("\n[3] Verificando validez del hwnd…")
is_win = bool(user32.IsWindow(hwnd))
is_vis = bool(user32.IsWindowVisible(hwnd))
is_min = bool(user32.IsIconic(hwnd))
_info(f"IsWindow={is_win}  IsWindowVisible={is_vis}  IsIconic(minimizada)={is_min}")
if is_win and is_vis:
    _ok("Ventana válida y visible.")
    results["3_hwnd_valid"] = True
elif is_win and is_min:
    _info("Ventana minimizada — se intentará restaurar con focus_now().")
    results["3_hwnd_valid"] = True
else:
    _fail("El hwnd ya no es válido o la ventana no es visible.")
    results["3_hwnd_valid"] = False

# ── 4. Forzar foco ────────────────────────────────────────────────────────────
print(f"\n[4] Forzando foco sobre Tibia (focus_now)…")
fg_before = user32.GetForegroundWindow()
_info(f"Foreground antes: hwnd={fg_before}")

ok_focus = ctrl.focus_now()
fg_after = user32.GetForegroundWindow()
_info(f"Foreground después: hwnd={fg_after}  ==hwnd: {fg_after == hwnd}")

if ok_focus and fg_after == hwnd:
    _ok("focus_now() exitoso — Tibia tiene el foco.")
    results["4_focus"] = True
elif ok_focus:
    _info("focus_now() devolvió True pero GetForegroundWindow difiere "
          "(normal si hay UAC/fullscreen).")
    results["4_focus"] = True   # consideramos OK si la API no dio error
else:
    _fail("focus_now() devolvió False.")
    results["4_focus"] = False

# ── 5. PostMessage — WM_KEYDOWN/WM_KEYUP ─────────────────────────────────────
print(f"\n[5] PostMessage WM_KEYDOWN/WM_KEYUP — tecla: {Key(TEST_VK).name} (VK={hex(TEST_VK)})…")
_info(f"Pausa de {PAUSE_SECS}s para que puedas ver el personaje en pantalla…")
time.sleep(PAUSE_SECS)

lp_down = _make_lparam(TEST_VK, 0)
lp_up   = _make_lparam(TEST_VK, 1)

ctypes.windll.kernel32.SetLastError(0)
ret_down = user32.PostMessageW(hwnd, WM_KEYDOWN, TEST_VK, lp_down)
err_down = ctypes.windll.kernel32.GetLastError()
time.sleep(TEST_DELAY)
ctypes.windll.kernel32.SetLastError(0)
ret_up   = user32.PostMessageW(hwnd, WM_KEYUP,   TEST_VK, lp_up)
err_up   = ctypes.windll.kernel32.GetLastError()

_info(f"PostMessageW KEYDOWN → ret={ret_down}  GetLastError={err_down}")
_info(f"PostMessageW KEYUP   → ret={ret_up}   GetLastError={err_up}")

if ret_down and ret_up and err_down == 0 and err_up == 0:
    _ok("PostMessage entregado sin errores Win32.")
    _info("Si el personaje NO se movió: Tibia ignora PostMessage (UIPI/anticheat).")
    results["5_postmessage"] = True
else:
    _fail(f"PostMessage falló. ret_down={ret_down} err_down={err_down} "
          f"ret_up={ret_up} err_up={err_up}")
    results["5_postmessage"] = False

time.sleep(0.5)

# ── 6. press_key vía InputController.press_key ────────────────────────────────
print(f"\n[6] InputController.press_key (postmessage)…")
ok_pk = ctrl.press_key(TEST_VK, delay=TEST_DELAY)
_info(f"press_key() → {ok_pk}")
if ok_pk:
    _ok("press_key() ejecutado sin excepción.")
    results["6_press_key"] = True
else:
    _fail("press_key() devolvió False (no conectado o error interno).")
    results["6_press_key"] = False

time.sleep(0.5)

# ── 7. SendInput KEYEVENTF_SCANCODE (requiere foco) ───────────────────────────
print(f"\n[7] SendInput KEYEVENTF_SCANCODE — requiere foco…")

# volver a enfocar antes del SendInput
ctrl.focus_now()
time.sleep(0.1)

scan = ctypes.windll.user32.MapVirtualKeyW(int(TEST_VK), 0)

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP    = 0x0002

# IMPORTANTE: incluir MOUSEINPUT en la union fuerza sizeof(INPUT)=40 en 64-bit.
# Sin él, sizeof=32 y SendInput devuelve 0 con GetLastError=87.
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          wt.LONG),
        ("dy",          wt.LONG),
        ("mouseData",   wt.DWORD),
        ("dwFlags",     wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         wt.WORD),
        ("wScan",       wt.WORD),
        ("dwFlags",     wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("_u",)
    _fields_    = [("type", wt.DWORD), ("_u", _INPUT_UNION)]

input_size = ctypes.sizeof(INPUT)
_info(f"VK={hex(TEST_VK)}  scan={hex(scan)}  sizeof(INPUT)={input_size} (esperado 40 en 64-bit)")
if input_size != 40:
    _fail(f"sizeof(INPUT)={input_size} != 40 — struct mal definido.")

def _send(flags: int) -> int:
    inp = INPUT(type=1, ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags,
                                      time=0, dwExtraInfo=0))
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

sent_down = _send(KEYEVENTF_SCANCODE)
time.sleep(TEST_DELAY)
sent_up   = _send(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
err_si    = ctypes.windll.kernel32.GetLastError()

_info(f"SendInput KEYDOWN → {sent_down}  KEYUP → {sent_up}  GetLastError={err_si}")

if sent_down == 1 and sent_up == 1 and err_si == 0:
    _ok("SendInput KEYEVENTF_SCANCODE entregado sin errores.")
    _info("Si el personaje NO se movió: la ventana no tenía foco real "
          "(p.ej. cliente Tibia en modo protegido/admin).")
    results["7_sendinput"] = True
else:
    _fail(f"SendInput falló. sent_down={sent_down} sent_up={sent_up} err={err_si}")
    results["7_sendinput"] = False

time.sleep(0.5)

# ── 8. Verificar hwnd aún válido ──────────────────────────────────────────────
print("\n[8] Verificando hwnd tras las pruebas…")
still_valid = bool(user32.IsWindow(hwnd))
if still_valid:
    _ok(f"hwnd={hwnd} sigue válido.")
    results["8_hwnd_after"] = True
else:
    _fail(f"hwnd={hwnd} ya no es válido — la ventana se cerró durante la prueba.")
    results["8_hwnd_after"] = False

# ── resumen ───────────────────────────────────────────────────────────────────
print("\n" + SEP)
print("  RESUMEN")
print(SEP)
all_pass = True
labels = {
    "1_find_windows" : "Encontrar ventana Tibia",
    "2_connect"      : "Conectar InputController",
    "3_hwnd_valid"   : "hwnd válido y visible",
    "4_focus"        : "focus_now()",
    "5_postmessage"  : "PostMessageW sin error Win32",
    "6_press_key"    : "press_key() sin excepción",
    "7_sendinput"    : "SendInput KEYEVENTF_SCANCODE",
    "8_hwnd_after"   : "hwnd válido al final",
}
for key, label in labels.items():
    passed = results.get(key, False)
    icon = "✓ PASS" if passed else "✗ FAIL"
    print(f"  {icon}  {label}")
    if not passed:
        all_pass = False

print(SEP)
if all_pass:
    print("  TODAS LAS PRUEBAS PASARON")
    print()
    print("  Si el personaje no se movió a pesar de PASS en postmessage/sendinput,")
    print("  el cliente Tibia está bloqueando inputs externos (CipSoft protection).")
    print("  Posibles soluciones:")
    print("    • Ejecuta tanto Tibia como este script como Administrador")
    print("    • Usa el modo 'scancode' con la ventana en foreground real")
    print("    • El cliente moderno de Tibia (Flash/Desktop) filtra PostMessage")
else:
    print("  ALGUNAS PRUEBAS FALLARON — revisar mensajes [✗] arriba.")
print(SEP)
