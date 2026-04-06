"""
InputController — envía teclado y clics de ratón a una ventana de destino.

Modos de input (input_method):
  'postmessage'    WM_KEYDOWN/WM_KEYUP via PostMessageW — background,
                   no requiere foco, detectable por anticheat.
  'scancode'       keybd_event con VK + scancode — requiere foreground.
                   BattlEye bloquea la flag LLKHF_INJECTED.
  'interception'   Interception kernel driver — envía keystrokes directo
                   al device sin flag INJECTED.  Requiere driver instalado
                   y reboot.  Invisible a BattlEye.

Funcionalidades:
  - Listado y búsqueda de ventanas por título
  - Envío de teclas via PostMessage o SendInput/scancode
  - Click izquierdo/derecho en coordenadas de cliente o absolutas
  - Traducción WASD/flechas → movimiento Tibia

Uso:
    from src.input_controller import InputController, Key

    ctrl = InputController("Tibia", input_method="scancode")
    ctrl.press_key(Key.ARROW_UP)
    ctrl.click(x=800, y=600)
"""

from __future__ import annotations

import logging
import random
import threading
import time
import ctypes
import ctypes.wintypes as wt
from enum import IntEnum
from typing import Any, Optional, List, Tuple

_log = logging.getLogger("wn.ic")

from .humanizer import humanize as _humanize
from .input_backends import (
    click_interception as runtime_click_interception,
    ensure_foreground as runtime_ensure_foreground,
    ensure_pico_foreground as runtime_ensure_pico_foreground,
    press_hybrid as runtime_press_hybrid,
    press_interception as runtime_press_interception,
    press_postmessage as runtime_press_postmessage,
    press_scancode as runtime_press_scancode,
    type_hardware as runtime_type_hardware,
    type_interception as runtime_type_interception,
)

# ─────────────────────────────────────────────────────────────────────────────
# Win32 API directa (no requiere pywin32 en tiempo de ejecución)
# ─────────────────────────────────────────────────────────────────────────────
user32  = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_KEYDOWN   = 0x0100
WM_KEYUP     = 0x0101
WM_CHAR      = 0x0102
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP   = 0x0205
WM_MOUSEMOVE   = 0x0200

def MAKELONG(lo: int, hi: int) -> int:
    return (hi << 16) | (lo & 0xFFFF)


# ─────────────────────────────────────────────────────────────────────────────
# Virtual-key codes
# ─────────────────────────────────────────────────────────────────────────────
class Key(IntEnum):
    # Movimiento
    ARROW_UP    = 0x26
    ARROW_DOWN  = 0x28
    ARROW_LEFT  = 0x25
    ARROW_RIGHT = 0x27
    # WASD
    W = 0x57
    A = 0x41
    S = 0x53
    D = 0x44
    # Acciones comunes
    SPACE    = 0x20
    ENTER    = 0x0D
    ESCAPE   = 0x1B
    SHIFT    = 0x10
    CTRL     = 0x11
    ALT      = 0x12
    TAB      = 0x09
    F1 = 0x70; F2 = 0x71; F3 = 0x72; F4 = 0x73
    F5 = 0x74; F6 = 0x75; F7 = 0x76; F8 = 0x77
    F9 = 0x78; F10 = 0x79; F11 = 0x7A; F12 = 0x7B
    # Numpad
    NUM0 = 0x60; NUM1 = 0x61; NUM2 = 0x62; NUM3 = 0x63
    NUM4 = 0x64; NUM5 = 0x65; NUM6 = 0x66; NUM7 = 0x67
    NUM8 = 0x68; NUM9 = 0x69
    # Navigation / extended keys
    PAGE_UP   = 0x21
    PAGE_DOWN = 0x22
    END       = 0x23
    HOME      = 0x24
    INSERT    = 0x2D
    DELETE    = 0x2E
    LWIN      = 0x5B
    RWIN      = 0x5C


# Tabla VK → scan code hardware (para KEYEVENTF_SCANCODE)
# Key enum values are IntEnum — they hash/compare identically to raw ints,
# so Key.F1 as a dict key is equivalent to 0x70.
_VK_TO_SCAN: dict[int, int] = {
    # Arrow keys
    Key.ARROW_LEFT:  0x4B,
    Key.ARROW_UP:    0x48,
    Key.ARROW_RIGHT: 0x4D,
    Key.ARROW_DOWN:  0x50,
    # Letters
    Key.A: 0x1E,
    Key.D: 0x20,
    Key.S: 0x1F,
    Key.W: 0x11,
    # Special
    Key.SPACE:  0x39,
    Key.ENTER:  0x1C,
    Key.ESCAPE: 0x01,
    Key.TAB:    0x0F,
    # Function keys F1-F12
    Key.F1:  0x3B,
    Key.F2:  0x3C,
    Key.F3:  0x3D,
    Key.F4:  0x3E,
    Key.F5:  0x3F,
    Key.F6:  0x40,
    Key.F7:  0x41,
    Key.F8:  0x42,
    Key.F9:  0x43,
    Key.F10: 0x44,
    Key.F11: 0x57,
    Key.F12: 0x58,
}
# VK → human-readable key name understood by the Arduino/Pico firmware
_VK_TO_HID: dict[int, str] = {
    Key.ENTER:       "ENTER",
    Key.ESCAPE:      "ESC",
    Key.TAB:         "TAB",
    Key.SPACE:       "SPACE",
    Key.SHIFT:       "SHIFT",
    Key.CTRL:        "CTRL",
    Key.ALT:         "ALT",
    Key.ARROW_UP:    "UP",
    Key.ARROW_DOWN:  "DOWN",
    Key.ARROW_LEFT:  "LEFT",
    Key.ARROW_RIGHT: "RIGHT",
    Key.PAGE_UP:     "PAGEUP",
    Key.PAGE_DOWN:   "PAGEDOWN",
    Key.HOME:        "HOME",
    Key.END:         "END",
    Key.INSERT:      "INSERT",
    Key.DELETE:      "DELETE",
    **{getattr(Key, f"F{i}"): f"F{i}" for i in range(1, 13)},  # F1-F12
    **{0x41 + i: chr(0x41 + i) for i in range(26)},  # A-Z (VK 0x41-0x5A)
    **{getattr(Key, f"NUM{i}"): str(i) for i in range(10)},  # NUM0-NUM9
}

def _vk_to_hid_name(vk: int) -> str:
    """Convert a VK code to the firmware key name, falling back to uppercase char."""
    name = _VK_TO_HID.get(vk)
    if name:
        return name
    # Printable ASCII fallback (e.g. VK for 'Q' is 0x51 = ord('Q'))
    if 0x20 <= vk <= 0x7E:
        return chr(vk).upper()
    return str(vk)  # last resort — may return ERR from firmware


def _vk_to_arduino_arg(vk: int) -> str:
    """Return the firmware key name expected by Arduino/Pico HID integrations."""
    return _vk_to_hid_name(vk)

# Arrow keys y otras teclas extendidas requieren KEYEVENTF_EXTENDEDKEY
_EXTENDED_VK: set[int] = {
    Key.ARROW_LEFT, Key.ARROW_UP, Key.ARROW_RIGHT, Key.ARROW_DOWN,
    Key.PAGE_UP, Key.PAGE_DOWN, Key.END, Key.HOME,
    Key.INSERT, Key.DELETE, Key.LWIN, Key.RWIN,
}


# Dirección → Key
WASD_KEYS = {
    "up":    [Key.ARROW_UP,    Key.W],
    "down":  [Key.ARROW_DOWN,  Key.S],
    "left":  [Key.ARROW_LEFT,  Key.A],
    "right": [Key.ARROW_RIGHT, Key.D],
}


# ─────────────────────────────────────────────────────────────────────────────
# Enumeración de ventanas
# ─────────────────────────────────────────────────────────────────────────────
class WindowInfo:
    def __init__(self, hwnd: int, title: str, pid: int):
        self.hwnd  = hwnd
        self.title = title
        self.pid   = pid

    def __repr__(self) -> str:
        return f"<Window hwnd={self.hwnd} pid={self.pid} title={self.title!r}>"


def list_windows(visible_only: bool = True) -> List[WindowInfo]:
    """Devuelve todas las ventanas abiertas."""
    results: List[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)  # type: ignore[untyped-decorator]
    def _enum_cb(hwnd: int, _: int) -> bool:
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value.strip()
        if title:
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            results.append(WindowInfo(hwnd, title, pid.value))
        return True

    user32.EnumWindows(_enum_cb, 0)
    return results


def find_window(title_fragment: str, visible_only: bool = True,
                exclude_hwnd: Optional[int] = None) -> Optional["WindowInfo"]:
    """
    Busca la primera ventana cuyo título coincida con `title_fragment`.
    Orden de prioridad:
      1. Coincidencia exacta (case-insensitive)
      2. Empieza por el fragmento
      3. Contiene el fragmento
    """
    frag = title_fragment.lower()
    windows = list_windows(visible_only)
    if exclude_hwnd:
        windows = [w for w in windows if w.hwnd != exclude_hwnd]

    # 1. Exacta
    for w in windows:
        if w.title.lower() == frag:
            return w
    # 2. Empieza por
    for w in windows:
        if w.title.lower().startswith(frag):
            return w
    # 3. Contiene
    for w in windows:
        if frag in w.title.lower():
            return w
    return None


# ─────────────────────────────────────────────────────────────────────────────
# InputController principal
# ─────────────────────────────────────────────────────────────────────────────
class InputController:
    """
    Envía inputs de teclado y ratón a una ventana de destino.

    Modos de input (input_method):
      'postmessage'    WM_KEYDOWN/WM_KEYUP via PostMessageW — background,
                       no requiere foco, pero detectable por anticheat.
      'scancode'       keybd_event con VK + scancode — requiere foreground.
                       BattlEye bloquea la flag LLKHF_INJECTED.
      'hybrid'         scancode + restauración inmediata de foco (<20ms).
      'interception'   Interception kernel driver — envía keystrokes directo
                       al device sin flag INJECTED.  Requiere driver instalado
                       y reboot.  Invisible a BattlEye.  RECOMENDADO.

    Parámetros:
        target_title:   fragmento del título de la ventana destino.
        key_delay:      segundos entre KEYDOWN y KEYUP (default 0.05).
        move_mode:      'arrow' | 'wasd'.
        input_method:   'interception' | 'postmessage' | 'scancode' | 'hybrid'.
        fg_delay:       pausa (s) tras SetForegroundWindow (scancode/hybrid).
    """

    def __init__(
        self,
        target_title: str = "Tibia",
        key_delay: float = 0.05,
        move_mode: str = "arrow",
        input_method: str = "interception",
        fg_delay: float = 0.04,
        jitter_pct: float = 0.0,
    ):
        self.target_title = target_title
        self.key_delay    = key_delay
        self.move_mode    = move_mode
        self.input_method = input_method
        self.fg_delay     = fg_delay
        self.jitter_pct   = jitter_pct   # 0.0 = sin jitter; 0.3 = ±30% aleatorio en cada delay
        self._hwnd: Optional[int] = None
        self._own_hwnd: Optional[int] = None
        self._log: List[str] = []
        self._max_log = 40
        self._input_lock = threading.Lock()  # T2: serialise all Win32 input calls
        self._interception_ctx: Any = None   # lazy-loaded Interception context
        self._interception_failed: bool = False  # latch: True after first driver failure
        self._emergency_stopped: bool = False  # kill switch: all input blocked
        self._consecutive_failures: int = 0  # track consecutive press_key failures
        self._lock_timeout: float = 5.0  # max seconds to wait for input lock
        self._arduino_hid: Any = None  # optional ArduinoHID or PicoHID for failover
        self._using_arduino_failover: bool = False  # True when running on HID failover

    # ── Ventana ──────────────────────────────────────────────────────────────
    @property
    def hwnd(self) -> Optional[int]:
        return self._hwnd

    def find_target(self) -> Optional[WindowInfo]:
        w = find_window(self.target_title, exclude_hwnd=self._own_hwnd)
        if w:
            self._hwnd = w.hwnd
            self._log_event(f"Ventana encontrada: {w.title!r} (hwnd={w.hwnd})")
        else:
            self._hwnd = None
            self._log_event(f"Ventana no encontrada: {self.target_title!r}")
        return w

    def is_connected(self) -> bool:
        if self._hwnd is None:
            return False
        return bool(user32.IsWindow(self._hwnd))

    def emergency_stop(self) -> None:
        """Kill switch: permanently block all input from this controller.

        All subsequent ``press_key`` / ``click`` calls return False immediately.
        Cannot be undone — create a new InputController after resolving the issue.
        """
        self._emergency_stopped = True
        # Release any held keys on the hardware HID device
        if self._arduino_hid is not None and hasattr(self._arduino_hid, "release_all_keys"):
            try:
                self._arduino_hid.release_all_keys()
            except Exception:
                pass
        self._log_event("[E] Input controller killed — all input blocked")
        _log.critical("[E] InputController.emergency_stop() called")

    @property
    def is_emergency_stopped(self) -> bool:
        return self._emergency_stopped

    def set_arduino_failover(self, arduino_hid: Any) -> None:
        """Register an ArduinoHIDController as failover for Interception.

        When the Interception driver fails at runtime, input will seamlessly
        switch to the Arduino HID device (real USB hardware) instead of
        crashing.  Arduino HID is undetectable by BattlEye since it sends
        genuine USB HID reports.
        """
        self._arduino_hid = arduino_hid
        self._log_event("[FO] Arduino HID registered as Interception fallback")

    def _send_arduino_key_press(self, vk: int, duration_ms: int, *, action: str) -> bool:
        if self._arduino_hid is None:
            return False
        key_name = _vk_to_arduino_arg(vk)
        if self._arduino_hid.send_key_press(key_name, duration_ms):
            return True
        _log.warning("[Pico] %s send_key_press(%s) failed", action, key_name)
        self._consecutive_failures += 1
        return False

    def _send_arduino_mouse_click(self, button: str, *, action: str) -> bool:
        if self._arduino_hid is None:
            return False
        if self._arduino_hid.send_mouse_click(button):
            return True
        _log.warning("[Pico] %s send_mouse_click(%s) failed", action, button)
        self._consecutive_failures += 1
        return False

    def _send_arduino_combo(self, vk1: int, vk2: int, duration_ms: int, *, action: str) -> bool:
        if self._arduino_hid is None:
            return False
        key1 = _vk_to_hid_name(vk1)
        key2 = _vk_to_hid_name(vk2)
        send_combo = getattr(self._arduino_hid, "send_combo", None)
        if callable(send_combo):
            if send_combo(key1, key2, duration_ms):
                return True
            _log.warning("[Pico] %s send_combo(%s+%s) failed", action, key1, key2)
            self._consecutive_failures += 1
            return False
        ok1 = self._send_arduino_key_press(vk1, 1, action=action)
        ok2 = self._send_arduino_key_press(vk2, 1, action=action)
        if ok1 and ok2:
            time.sleep(max(duration_ms, 1) / 1000)
            return True
        return False

    def _send_arduino_shift_click(self, x: int, y: int, *, action: str) -> bool:
        if self._arduino_hid is None:
            return False
        sx, sy = self._set_cursor_from_client(x, y)
        if not self._send_arduino_key_press(Key.SHIFT, 30, action=action):
            return False
        time.sleep(self._jitter(0.02))
        if not self._send_arduino_mouse_click("left", action=action):
            self._arduino_hid.send_key_release(_vk_to_hid_name(Key.SHIFT))
            return False
        if not self._arduino_hid.send_key_release(_vk_to_hid_name(Key.SHIFT)):
            _log.warning("[Pico] %s send_key_release(SHIFT) failed", action)
            self._consecutive_failures += 1
            return False
        self._log_event(f"SHIFT_CLICK ({x},{y}) scr=({sx},{sy}) [Arduino]")
        return True

    @property
    def using_arduino_failover(self) -> bool:
        """True when the controller has switched to Arduino HID after Interception failure."""
        return self._using_arduino_failover

    def focus_now(self) -> bool:
        """Fuerza el foco sobre la ventana destino de forma inmediata.

        A diferencia de :meth:`_ensure_foreground`, **no** espera 3 s para que
        el usuario haga clic: usa directamente Alt-key trick + SetForegroundWindow.

        Devuelve ``True`` si la ventana quedó en primer plano tras la llamada,
        ``False`` si no está conectado o no se pudo forzar el foco.

        **No-op** cuando ``input_method == "interception"`` (driver-level input
        bypasses window focus).  Pico2/Arduino HID goes through the OS input
        stack, so it still needs foreground focus.

        Thread-safe: serialised via ``_input_lock``.
        """
        if not self.is_connected():
            return False
        # Interception driver sends directly — focus not needed.
        # Pico2/Arduino HID goes through OS input stack — NEEDS foreground.
        if (
            not self._using_arduino_failover
            and self.input_method == "interception"
            and not self._interception_failed
        ):
            return True  # pretend success — focus not needed for driver-level input
        # Never steal focus — user must be able to switch windows / Ctrl+C freely.
        return bool(user32.GetForegroundWindow() == self._hwnd)

    # ── Jitter ──────────────────────────────────────────────────────────────
    def _jitter(self, base: float) -> float:
        """Apply humanized timing variation via the central humanizer module."""
        if self.jitter_pct <= 0:
            return base
        return _humanize(base, pct=self.jitter_pct)

    # ── Teclas ───────────────────────────────────────────────────────────────
    def press_key(self, vk: int, delay: Optional[float] = None) -> bool:
        """Envía KEYDOWN + KEYUP para la virtual-key `vk`.

        Thread-safe: serialised via ``_input_lock`` with timeout.
        Returns False if emergency-stopped, disconnected, or lock timeout.
        """
        if self._emergency_stopped:
            return False
        if not self.is_connected():
            self._log_event(f"[!] Sin hwnd — tecla {vk:#04x} descartada")
            self._consecutive_failures += 1
            return False
        d = self._jitter(delay if delay is not None else self.key_delay)
        acquired = self._input_lock.acquire(timeout=self._lock_timeout)
        if not acquired:
            self._log_event(f"[!] Lock timeout ({self._lock_timeout}s) — tecla {vk:#04x} descartada")
            self._consecutive_failures += 1
            return False
        try:
            if self._using_arduino_failover and self._arduino_hid is not None:
                self._ensure_pico_foreground()
                if not self._send_arduino_key_press(vk, int(d * 1000), action="press_key"):
                    return False
            elif self.input_method == "interception" and not self._interception_failed:
                if not self._press_with_interception_fallback(vk, d):
                    return False
            elif self.input_method == "hybrid":
                self._press_hybrid(vk, d)
            elif self.input_method == "scancode":
                self._press_scancode(vk, d)
            else:
                self._press_postmessage(vk, d)
        finally:
            self._input_lock.release()
        self._log_event(f"KEY  {Key(vk).name if vk in Key._value2member_map_ else hex(vk)}")
        self._consecutive_failures = 0
        return True

    def key_combo(self, modifier_vk: int, key_vk: int, delay: Optional[float] = None) -> bool:
        """Send modifier+key combo (e.g. Ctrl+L).

        Holds *modifier_vk* down, presses *key_vk*, then releases modifier.
        Thread-safe: serialised via ``_input_lock`` with timeout.
        """
        if self._emergency_stopped:
            return False
        if not self.is_connected():
            return False
        d = self._jitter(delay if delay is not None else self.key_delay)
        acquired = self._input_lock.acquire(timeout=self._lock_timeout)
        if not acquired:
            return False
        try:
            if self._using_arduino_failover and self._arduino_hid is not None:
                self._ensure_pico_foreground()
                dur_ms = int(d * 1000)
                if not self._send_arduino_combo(modifier_vk, key_vk, dur_ms, action="key_combo"):
                    return False
            else:
                self._combo_postmessage(modifier_vk, key_vk, d)
        finally:
            self._input_lock.release()
        self._consecutive_failures = 0
        return True

    def _combo_postmessage(self, mod_vk: int, key_vk: int, delay: float) -> None:
        """Hold modifier, press key, release both via PostMessageW."""
        mod_down = self._make_lparam(mod_vk, 0)
        mod_up = self._make_lparam(mod_vk, 1)
        key_down = self._make_lparam(key_vk, 0)
        key_up = self._make_lparam(key_vk, 1)
        user32.PostMessageW(self._hwnd, WM_KEYDOWN, mod_vk, mod_down)
        time.sleep(delay * 0.3)
        user32.PostMessageW(self._hwnd, WM_KEYDOWN, key_vk, key_down)
        time.sleep(delay)
        user32.PostMessageW(self._hwnd, WM_KEYUP, key_vk, key_up)
        time.sleep(delay * 0.2)
        user32.PostMessageW(self._hwnd, WM_KEYUP, mod_vk, mod_up)

    def _press_with_interception_fallback(self, vk: int, d: float) -> bool:
        try:
            self._press_interception(vk, d)
            return True
        except (RuntimeError, ImportError):
            self._interception_warn_fallback("press_key")
            if self._using_arduino_failover and self._arduino_hid is not None:
                self._ensure_pico_foreground()
                return self._send_arduino_key_press(vk, int(d * 1000), action="press_key")
            return False

    def _press_postmessage(self, vk: int, delay: float) -> None:
        runtime_press_postmessage(
            self,
            vk,
            delay,
            user32_module=user32,
            time_module=time,
            wm_keydown=WM_KEYDOWN,
            wm_keyup=WM_KEYUP,
        )

    def _ensure_foreground(self) -> bool:
        return runtime_ensure_foreground(self, user32_module=user32, time_module=time)

    # ------------------------------------------------------------------
    # Interception driver backend
    # ------------------------------------------------------------------

    def _get_interception(self) -> Any:
        """Lazy-init the Interception context.

        Raises ``RuntimeError`` if the driver is not installed or the PC
        has not been rebooted after installation.
        """
        if self._interception_failed:
            raise RuntimeError("Interception driver previously failed")
        if self._interception_ctx is None:
            try:
                from interception import Interception  # type: ignore[import-untyped]
                self._interception_ctx = Interception()
            except Exception as exc:
                self._interception_failed = True
                raise RuntimeError(
                    "Interception driver no disponible. "
                    "¿Instalaste el driver y reiniciaste el PC?"
                ) from exc
        return self._interception_ctx

    @property
    def interception_available(self) -> bool:
        """True when the Interception driver is loaded or can be loaded."""
        if self._interception_ctx is not None:
            return True
        if self._interception_failed:
            return False
        try:
            self._get_interception()
            return True
        except RuntimeError:
            return False

    def _interception_warn_fallback(self, action: str) -> None:
        """Handle Interception driver failure at runtime.

        If an Arduino HID controller is registered, seamlessly switch to
        it (real USB HID — undetectable).  Otherwise abort: falling back
        to PostMessage/SendInput is instantly detectable by BattlEye.
        """
        self._interception_failed = True

        # Attempt Arduino HID failover
        if self._arduino_hid is not None and hasattr(self._arduino_hid, "is_available"):
            if self._arduino_hid.is_available():
                self._using_arduino_failover = True
                _log.warning(
                    "[FO] Interception falló durante '%s' — "
                    "cambiando a Arduino HID (USB hardware real).",
                    action,
                )
                self._log_event(
                    f"[FO] Interception → Arduino HID (action={action})"
                )
                return  # don't crash — Arduino will handle subsequent calls

        msg = (
            f"Driver failed at runtime during '{action}'. "
            f"Cannot continue — verify driver installed + reboot."
        )
        _log.critical(msg)
        self._log_event(f"[E] DRIVER FAILED: {action}")
        raise RuntimeError(msg)

    def _press_interception(self, vk: int, delay: float) -> None:
        runtime_press_interception(
            self,
            vk,
            delay,
            ctypes_module=ctypes,
            time_module=time,
            extended_vk=_EXTENDED_VK,
        )

    def _ensure_pico_foreground(self) -> None:
        runtime_ensure_pico_foreground(self)

    def _set_cursor_from_client(self, x: int, y: int) -> tuple[int, int]:
        """Move the Windows cursor to screen coords derived from client (x, y).

        Brings the target window to the foreground first so the Pico2 HID
        click (which targets whatever window is under the cursor) actually
        lands on Tibia.  Uses SetCursorPos (instant, no sweep) so Pico2
        only needs to send a click — no MOUSE_MOVE required.
        Returns (screen_x, screen_y) for logging.
        """
        self._ensure_pico_foreground()
        pt = wt.POINT(x, y)
        user32.ClientToScreen(self._hwnd, ctypes.byref(pt))
        user32.SetCursorPos(pt.x, pt.y)
        return pt.x, pt.y

    def _click_interception(self, x: int, y: int, button: str = "left") -> None:
        runtime_click_interception(
            self,
            x,
            y,
            button,
            ctypes_module=ctypes,
            user32_module=user32,
            time_module=time,
            random_module=random,
            wt_module=wt,
        )

    # ------------------------------------------------------------------

    def _press_scancode(self, vk: int, delay: float) -> None:
        runtime_press_scancode(
            self,
            vk,
            delay,
            ctypes_module=ctypes,
            time_module=time,
            extended_vk=_EXTENDED_VK,
        )

    @staticmethod
    def _sendinput_scancode(scan: int, down: bool, extended: bool = False) -> None:
        """SendInput con KEYEVENTF_SCANCODE — indistinguible de teclado físico.

        El struct INPUT en 64-bit mide 40 bytes.  Para que ctypes calcule el
        tamaño correcto el union debe incluir MOUSEINPUT (el miembro más grande);
        omitirlo produce sizeof=32 → SendInput devuelve 0 con GetLastError=87.
        """
        KEYEVENTF_SCANCODE    = 0x0008
        KEYEVENTF_KEYUP       = 0x0002
        KEYEVENTF_EXTENDEDKEY = 0x0001

        # MOUSEINPUT es el miembro más grande de la union INPUT; su presencia
        # eleva sizeof(union) a 32 bytes → sizeof(INPUT) = 4+4+32 = 40 bytes.
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx",          wt.LONG),
                ("dy",          wt.LONG),
                ("mouseData",   wt.DWORD),
                ("dwFlags",     wt.DWORD),
                ("time",        wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR: 8 bytes en 64-bit
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

        flags = KEYEVENTF_SCANCODE
        if extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= KEYEVENTF_KEYUP
        extra = ctypes.windll.user32.GetMessageExtraInfo() or 0x80040000
        inp = INPUT(type=1, ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags,
                                          time=0, dwExtraInfo=extra))
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    @staticmethod
    def _keybd_event(vk: int, scan: int, down: bool, extended: bool = False) -> None:
        """keybd_event con VK + scancode — compatible con Qt (Tibia v13+).

        Qt QPA ignora SendInput con wVk=0 + KEYEVENTF_SCANCODE.
        keybd_event envía ambos (VK + scan) correctamente.

        dwExtraInfo se establece con GetMessageExtraInfo() para imitar un
        evento de hardware real — BattlEye verifica dwExtraInfo==0 en su
        WH_KEYBOARD_LL hook como señal de inyección.
        """
        KEYEVENTF_EXTENDEDKEY = 0x0001
        KEYEVENTF_KEYUP       = 0x0002
        flags = 0
        if extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= KEYEVENTF_KEYUP
        extra = ctypes.windll.user32.GetMessageExtraInfo() or 0x80040000
        ctypes.windll.user32.keybd_event(vk, scan, flags, extra)

    def _press_hybrid(self, vk: int, delay: float) -> None:
                runtime_press_hybrid(
                        self,
                        vk,
                        delay,
                        ctypes_module=ctypes,
                        time_module=time,
                        extended_vk=_EXTENDED_VK,
                )

    def hold_key(self, vk: int, duration: float = 0.3) -> bool:
        """Mantiene una tecla pulsada `duration` segundos.

        Thread-safe: serialised via ``_input_lock``.
        """
        if not self.is_connected():
            return False
        with self._input_lock:
            if self._using_arduino_failover and self._arduino_hid is not None:
                self._ensure_pico_foreground()
                if not self._send_arduino_key_press(vk, int(duration * 1000), action="hold_key"):
                    return False
            elif self.input_method == "interception" and not self._interception_failed:
                try:
                    self._press_interception(vk, duration)
                except (RuntimeError, ImportError):
                    self._interception_warn_fallback("hold_key")
                    if self._using_arduino_failover and self._arduino_hid is not None:
                        if not self._send_arduino_key_press(vk, int(duration * 1000), action="hold_key"):
                            return False
                    else:
                        return False
            elif self.input_method in ("scancode", "hybrid"):
                scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
                extended = vk in _EXTENDED_VK
                self._keybd_event(vk, scan, True,  extended)
                time.sleep(duration)
                self._keybd_event(vk, scan, False, extended)
            else:
                lparam_down = self._make_lparam(vk, 0)
                lparam_up   = self._make_lparam(vk, 1)
                user32.PostMessageW(self._hwnd, WM_KEYDOWN, vk, lparam_down)
                time.sleep(duration)
                user32.PostMessageW(self._hwnd, WM_KEYUP,   vk, lparam_up)
        self._log_event(f"HOLD {Key(vk).name if vk in Key._value2member_map_ else hex(vk)} {duration:.2f}s")
        return True

    def type_text(self, text: str) -> bool:
        """Envía texto carácter-a-carácter usando el mismo método que press_key.

        - interception → Interception driver (hardware real).
        - scancode/hybrid → keybd_event con VK + scancode (hardware-like).
        - postmessage → WM_CHAR via PostMessageW (fallback).

        Thread-safe: serialised via ``_input_lock``.
        """
        if not self.is_connected():
            return False
        with self._input_lock:
            if self._using_arduino_failover and self._arduino_hid is not None:
                self._ensure_pico_foreground()
                for ch in text:
                    vk_ch = ord(ch.upper()) if ch.isalpha() else ord(ch)
                    if not self._send_arduino_key_press(vk_ch, int(self._jitter(0.05) * 1000), action="type_text"):
                        return False
                    time.sleep(self._jitter(0.05))
            elif self.input_method == "interception" and not self._interception_failed:
                try:
                    self._type_interception(text)
                except (RuntimeError, ImportError):
                    self._interception_warn_fallback("type_text")
                    if self._using_arduino_failover and self._arduino_hid is not None:
                        self._ensure_pico_foreground()
                        for ch in text:
                            vk_ch = ord(ch.upper()) if ch.isalpha() else ord(ch)
                            if not self._send_arduino_key_press(vk_ch, int(self._jitter(0.05) * 1000), action="type_text"):
                                return False
                            time.sleep(self._jitter(0.05))
                    else:
                        return False
            elif self.input_method in ("scancode", "hybrid"):
                self._type_hardware(text)
            else:
                for ch in text:
                    user32.PostMessageW(self._hwnd, WM_CHAR, ord(ch), 0)
                    time.sleep(self._jitter(0.02))
        self._log_event(f"TEXT {text!r}")
        return True

    def _type_interception(self, text: str) -> None:
        runtime_type_interception(self, text, ctypes_module=ctypes, time_module=time)

    def _type_hardware(self, text: str) -> None:
        runtime_type_hardware(self, text, ctypes_module=ctypes, time_module=time)

    # ── Movimiento direccional ────────────────────────────────────────────────
    def move(self, direction: str, steps: int = 1, step_delay: float = 0.15) -> bool:
        """
        Mueve el personaje en `direction` ('up','down','left','right') N pasos.
        Usa arrow keys o WASD según `move_mode`.
        """
        direction = direction.lower()
        keys = WASD_KEYS.get(direction)
        if not keys:
            return False
        vk = keys[1] if self.move_mode == "wasd" else keys[0]
        ok = True
        for _ in range(steps):
            ok = ok and self.press_key(vk, delay=step_delay)
            time.sleep(step_delay * 0.3)
        return ok

    def move_to_tile(self, dx: int, dy: int, step_delay: float = 0.15) -> bool:
        """
        Navega dx tiles en X y dy tiles en Y.
        dx>0 = derecha, dy>0 = abajo (coordenadas Tibia).

        When **both** dx and dy are non-zero the method presses both arrow
        keys simultaneously so the character moves diagonally (1 tile per
        server tick) instead of zigzagging through two sequential steps.
        """
        ok = True
        idx = 1 if self.move_mode == "wasd" else 0

        # ── Diagonal steps (simultaneous keypresses) ─────────────────────
        diag = min(abs(dx), abs(dy))
        if diag > 0:
            dir_x = "right" if dx > 0 else "left"
            dir_y = "down"  if dy > 0 else "up"
            vk_x = WASD_KEYS[dir_x][idx]
            vk_y = WASD_KEYS[dir_y][idx]
            for _ in range(diag):
                ok = ok and self._press_two_keys(vk_x, vk_y, step_delay)
                time.sleep(step_delay * 0.3)

        # ── Remaining straight-line steps ────────────────────────────────
        rem_x = abs(dx) - diag
        rem_y = abs(dy) - diag
        if rem_x > 0:
            ok = ok and self.move("right" if dx > 0 else "left", rem_x, step_delay)
        if rem_y > 0:
            ok = ok and self.move("down" if dy > 0 else "up", rem_y, step_delay)
        return ok

    def _press_two_keys(self, vk1: int, vk2: int, delay: float = 0.15) -> bool:
        """Press two keys simultaneously (both down, wait, both up).

        Useful for diagonal movement where Tibia expects two arrow keys
        held at the same time.  Thread-safe via ``_input_lock``.
        """
        if not self.is_connected():
            return False
        d = self._jitter(delay)
        with self._input_lock:
            if self._using_arduino_failover and self._arduino_hid is not None:
                self._ensure_pico_foreground()
                if not self._send_arduino_combo(vk1, vk2, int(d * 1000), action="press_two_keys"):
                    return False
            elif self.input_method == "interception" and not self._interception_failed:
                try:
                    ctx = self._get_interception()
                    scan1 = ctypes.windll.user32.MapVirtualKeyW(vk1, 0)
                    scan2 = ctypes.windll.user32.MapVirtualKeyW(vk2, 0)
                    ext1 = vk1 in _EXTENDED_VK
                    ext2 = vk2 in _EXTENDED_VK
                    from interception.strokes import KeyStroke
                    from interception.constants import KeyFlag
                    f1_down = KeyFlag.KEY_DOWN | (KeyFlag.KEY_E0 if ext1 else 0)
                    f2_down = KeyFlag.KEY_DOWN | (KeyFlag.KEY_E0 if ext2 else 0)
                    f1_up   = KeyFlag.KEY_UP   | (KeyFlag.KEY_E0 if ext1 else 0)
                    f2_up   = KeyFlag.KEY_UP   | (KeyFlag.KEY_E0 if ext2 else 0)
                    ctx.send(ctx.keyboard, KeyStroke(scan1, f1_down))
                    ctx.send(ctx.keyboard, KeyStroke(scan2, f2_down))
                    time.sleep(d)
                    ctx.send(ctx.keyboard, KeyStroke(scan1, f1_up))
                    ctx.send(ctx.keyboard, KeyStroke(scan2, f2_up))
                except (RuntimeError, ImportError):
                    self._interception_warn_fallback("press_two_keys")
                    if self._using_arduino_failover and self._arduino_hid is not None:
                        self._ensure_pico_foreground()
                        if not self._send_arduino_combo(vk1, vk2, int(d * 1000), action="press_two_keys"):
                            return False
                    else:
                        return False
            elif self.input_method in ("scancode", "hybrid"):
                scan1 = ctypes.windll.user32.MapVirtualKeyW(vk1, 0)
                scan2 = ctypes.windll.user32.MapVirtualKeyW(vk2, 0)
                ext1 = vk1 in _EXTENDED_VK
                ext2 = vk2 in _EXTENDED_VK
                # Use keybd_event (VK+scan) instead of SendInput (wVk=0)
                # because Qt (Tibia v13+) ignores SendInput with wVk=0.
                self._keybd_event(vk1, scan1, True, ext1)
                self._keybd_event(vk2, scan2, True, ext2)
                time.sleep(d)
                self._keybd_event(vk1, scan1, False, ext1)
                self._keybd_event(vk2, scan2, False, ext2)
            else:
                lp1_down = self._make_lparam(vk1, 0)
                lp1_up   = self._make_lparam(vk1, 1)
                lp2_down = self._make_lparam(vk2, 0)
                lp2_up   = self._make_lparam(vk2, 1)
                user32.PostMessageW(self._hwnd, WM_KEYDOWN, vk1, lp1_down)
                user32.PostMessageW(self._hwnd, WM_KEYDOWN, vk2, lp2_down)
                time.sleep(d)
                user32.PostMessageW(self._hwnd, WM_KEYUP, vk1, lp1_up)
                user32.PostMessageW(self._hwnd, WM_KEYUP, vk2, lp2_up)
        self._consecutive_failures = 0
        self._log_event(
            f"KEY2 {Key(vk1).name if vk1 in Key._value2member_map_ else hex(vk1)}"
            f"+{Key(vk2).name if vk2 in Key._value2member_map_ else hex(vk2)}"
        )
        return True

    # ── Ratón ─────────────────────────────────────────────────────────────────
    def click(self, x: int, y: int, button: str = "left") -> bool:
        """
        Click en coordenadas de cliente de la ventana destino.
        button: 'left' | 'right'

        Thread-safe: serialised via ``_input_lock`` with timeout.
        """
        if self._emergency_stopped:
            return False
        if not self.is_connected():
            return False
        acquired = self._input_lock.acquire(timeout=self._lock_timeout)
        if not acquired:
            self._log_event(f"[!] Lock timeout ({self._lock_timeout}s) — click descartado")
            return False
        try:
            if self._using_arduino_failover and self._arduino_hid is not None:
                sx, sy = self._set_cursor_from_client(x, y)
                time.sleep(self._jitter(0.02))
                if not self._send_arduino_mouse_click(button, action="click"):
                    return False
                self._log_event(f"CLICK {button} ({x},{y}) scr=({sx},{sy}) [Arduino]")
                return True
            elif self.input_method == "interception" and not self._interception_failed:
                try:
                    self._click_interception(x, y, button)
                    self._log_event(f"CLICK {button} ({x},{y})")
                    return True
                except (RuntimeError, ImportError):
                    self._interception_warn_fallback("click")
                    if self._using_arduino_failover and self._arduino_hid is not None:
                        sx, sy = self._set_cursor_from_client(x, y)
                        time.sleep(self._jitter(0.02))
                        if not self._send_arduino_mouse_click(button, action="click"):
                            return False
                        self._log_event(f"CLICK {button} ({x},{y}) scr=({sx},{sy}) [Arduino]")
                        return True
                    return False
            else:
                lparam = MAKELONG(x, y)
                if button == "left":
                    user32.PostMessageW(self._hwnd, WM_LBUTTONDOWN, 0x0001, lparam)
                    time.sleep(self._jitter(0.05))
                    user32.PostMessageW(self._hwnd, WM_LBUTTONUP,   0x0000, lparam)
                else:
                    user32.PostMessageW(self._hwnd, WM_RBUTTONDOWN, 0x0002, lparam)
                    time.sleep(self._jitter(0.05))
                    user32.PostMessageW(self._hwnd, WM_RBUTTONUP,   0x0000, lparam)
        finally:
            self._input_lock.release()
        self._log_event(f"CLICK {button} ({x},{y})")
        return True

    def move_mouse(self, x: int, y: int) -> bool:
        """Send WM_MOUSEMOVE to (x, y) in client coordinates without clicking.

        Use this to hover the cursor over a target before sending a keyboard
        shortcut that depends on cursor position (e.g. Alt+Q quick loot).
        Thread-safe: serialised via ``_input_lock`` with timeout.
        """
        if not self.is_connected():
            return False
        acquired = self._input_lock.acquire(timeout=self._lock_timeout)
        if not acquired:
            return False
        try:
            lparam = MAKELONG(x, y)
            user32.PostMessageW(self._hwnd, WM_MOUSEMOVE, 0, lparam)
        finally:
            self._input_lock.release()
        return True

    def left_click(self, x: int, y: int) -> bool:
        """Left-click en coordenadas de cliente (alias de click con button='left')."""
        return self.click(x, y, button="left")

    def right_click(self, x: int, y: int) -> bool:
        """Right-click en coordenadas de cliente (alias de click con button='right')."""
        return self.click(x, y, button="right")

    def shift_click(self, x: int, y: int) -> bool:
        """
        Shift+left-click en coordenadas de cliente.
        Usado por DepotManager para depositar ítems.

        Thread-safe: serialised via ``_input_lock``.
        """
        if not self.is_connected():
            return False
        VK_SHIFT = 0x10
        with self._input_lock:
            if self._using_arduino_failover and self._arduino_hid is not None:
                return self._send_arduino_shift_click(x, y, action="shift_click")
            elif self.input_method == "interception" and not self._interception_failed:
                try:
                    from interception.strokes import KeyStroke  # type: ignore[import-untyped]
                    from interception.constants import KeyFlag   # type: ignore[import-untyped]
                    ctx = self._get_interception()
                    scan = ctypes.windll.user32.MapVirtualKeyW(VK_SHIFT, 0)
                    # Hold shift down, click, release shift
                    ctx.send(ctx.keyboard, KeyStroke(scan, KeyFlag.KEY_DOWN))
                    time.sleep(self._jitter(0.03))
                    self._click_interception(x, y, "left")
                    time.sleep(self._jitter(0.03))
                    ctx.send(ctx.keyboard, KeyStroke(scan, KeyFlag.KEY_UP))
                    self._log_event(f"SHIFT_CLICK ({x},{y})")
                    return True
                except (RuntimeError, ImportError):
                    self._interception_warn_fallback("shift_click")
                    if self._using_arduino_failover and self._arduino_hid is not None:
                        return self._send_arduino_shift_click(x, y, action="shift_click")
                    return False
            else:
                lparam = MAKELONG(x, y)
                user32.PostMessageW(self._hwnd, WM_KEYDOWN, VK_SHIFT, self._make_lparam(VK_SHIFT, 0))
                time.sleep(self._jitter(0.03))
                user32.PostMessageW(self._hwnd, WM_LBUTTONDOWN, 0x0001, lparam)
                time.sleep(self._jitter(0.05))
                user32.PostMessageW(self._hwnd, WM_LBUTTONUP,   0x0000, lparam)
                time.sleep(self._jitter(0.03))
                user32.PostMessageW(self._hwnd, WM_KEYUP, VK_SHIFT, self._make_lparam(VK_SHIFT, 1))
        self._log_event(f"SHIFT_CLICK ({x},{y})")
        return True

    def click_absolute(self, x: int, y: int, button: str = "left") -> bool:
        """Click en coordenadas de pantalla absolutas (mueve el cursor real)."""
        rect = wt.RECT()
        user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        cx = x - rect.left
        cy = y - rect.top
        return self.click(cx, cy, button)

    # ── Human-like mouse (Bézier) ────────────────────────────────────────────

    def click_human(self, x: int, y: int, button: str = "left") -> bool:
        """Move cursor along a Bézier curve then click — mimics human hand.

        This method moves the **real** Windows cursor to the target pixel
        (screen coordinates derived from client *x, y*) using a randomised
        cubic Bézier path, then sends a SendInput mouse click.  Unlike
        :meth:`click`, this is **not** background-safe but is far harder
        for anti-cheat to distinguish from a real user.

        Falls back to :meth:`click` (PostMessage) when the window handle is
        missing or the import fails.

        Thread-safe: serialised via ``_input_lock``.
        """
        if not self.is_connected():
            return False
        try:
            from .mouse_bezier import move_mouse_to
        except ImportError:
            return self.click(x, y, button)  # fallback (click already locked)

        # Client → screen coords  (M5-fix: use ClientToScreen for accurate client-area offset)
        pt = wt.POINT(x, y)
        user32.ClientToScreen(self._hwnd, ctypes.byref(pt))
        sx = pt.x
        sy = pt.y

        with self._input_lock:
            # Arduino HID failover: SetCursorPos + Pico2 click (no mouse move)
            if self._using_arduino_failover and self._arduino_hid is not None:
                self._ensure_pico_foreground()
                user32.SetCursorPos(sx, sy)
                time.sleep(random.uniform(0.03, 0.07))
                if not self._send_arduino_mouse_click(button, action="click_human"):
                    return False
            # Click: prefer interception mouse + Bézier move via driver
            elif self.input_method == "interception" and not self._interception_failed:
                try:
                    from interception.strokes import MouseStroke     # type: ignore[import-untyped]
                    from interception.constants import (
                        MouseFlag, MouseButtonFlag,
                    )
                    ctx = self._get_interception()
                    scr_w = user32.GetSystemMetrics(0) or 1920
                    scr_h = user32.GetSystemMetrics(1) or 1080

                    def _move_via_interception(px: int, py: int) -> None:
                        ax = int(px * 65535 / scr_w)
                        ay = int(py * 65535 / scr_h)
                        ctx.send(ctx.mouse, MouseStroke(
                            MouseFlag.MOUSE_MOVE_ABSOLUTE, 0, 0, ax, ay))

                    # Bézier movement fully through Interception driver
                    move_mouse_to((sx, sy), move_fn=_move_via_interception)

                    if button == "left":
                        bd, bu = MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN, MouseButtonFlag.MOUSE_LEFT_BUTTON_UP
                    else:
                        bd, bu = MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN, MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP
                    ctx.send(ctx.mouse, MouseStroke(0, bd, 0, 0, 0))
                    time.sleep(random.uniform(0.03, 0.07))
                    ctx.send(ctx.mouse, MouseStroke(0, bu, 0, 0, 0))
                except (RuntimeError, ImportError):
                    self._interception_warn_fallback("click_human")
                    if self._using_arduino_failover and self._arduino_hid is not None:
                        self._ensure_pico_foreground()
                        user32.SetCursorPos(sx, sy)
                        time.sleep(random.uniform(0.03, 0.07))
                        if not self._send_arduino_mouse_click(button, action="click_human"):
                            return False
                    else:
                        return False
            else:
                # Non-interception mode: SetCursorPos + SendInput (only for non-BattlEye games)
                move_mouse_to((sx, sy))
                self._send_input_mouse_click(button)
        self._log_event(f"CLICK_HUMAN {button} ({x},{y}) screen=({sx},{sy})")
        return True

    @staticmethod
    def _send_input_mouse_click(button: str = "left") -> None:
        """Send a mouse click via SendInput at the current cursor position.

        dwExtraInfo is set via GetMessageExtraInfo() to mimic a real device
        event — BattlEye flags SendInput mouse clicks that have dwExtraInfo==0.
        """
        INPUT_MOUSE = 0
        MOUSEEVENTF_LEFTDOWN  = 0x0002
        MOUSEEVENTF_LEFTUP    = 0x0004
        MOUSEEVENTF_RIGHTDOWN = 0x0008
        MOUSEEVENTF_RIGHTUP   = 0x0010

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx",          ctypes.c_long),
                ("dy",          ctypes.c_long),
                ("mouseData",   ctypes.c_ulong),
                ("dwFlags",     ctypes.c_ulong),
                ("time",        ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR: 8 bytes on 64-bit
            ]

        class INPUT(ctypes.Structure):
            class _UNION(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT)]
            _anonymous_ = ("_u",)
            _fields_ = [("type", ctypes.c_ulong), ("_u", _UNION)]

        extra = ctypes.windll.user32.GetMessageExtraInfo() or 0x80040000

        if button == "left":
            down_flag, up_flag = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
        else:
            down_flag, up_flag = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP

        inp_down = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dwFlags=down_flag, dwExtraInfo=extra))
        inp_up   = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dwFlags=up_flag, dwExtraInfo=extra))
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(inp_down))
        time.sleep(random.uniform(0.03, 0.07))  # human hold time
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(inp_up))

    def get_window_rect(self) -> Optional[Tuple[int, int, int, int]]:
        """Devuelve (left, top, right, bottom) de la ventana destino."""
        if not self.is_connected():
            return None
        rect = wt.RECT()
        user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)

    # ── Log interno ───────────────────────────────────────────────────────────
    def _log_event(self, msg: str) -> None:
        ts = f"[{time.strftime('%H:%M:%S')}] "
        self._log.append(ts + msg)
        if len(self._log) > self._max_log:
            self._log.pop(0)

    def get_log(self, n: int = 10) -> List[str]:
        return self._log[-n:]

    def clear_log(self) -> None:
        """Erase all entries from the internal event log."""
        self._log.clear()

    def set_key_delay(self, delay: float) -> None:
        """Update the default pause (seconds) between KEYDOWN and KEYUP.

        Raises ``ValueError`` if *delay* is negative.
        """
        if delay < 0:
            raise ValueError(f"key_delay must be >= 0, got {delay!r}")
        self.key_delay = delay

    def set_move_mode(self, mode: str) -> None:
        """Switch between ``'arrow'`` and ``'wasd'`` movement modes.

        Raises ``ValueError`` for unrecognised modes.
        """
        if mode not in ("arrow", "wasd"):
            raise ValueError(f"move_mode must be 'arrow' or 'wasd', got {mode!r}")
        self.move_mode = mode
    @property
    def log_count(self) -> int:
        """Number of entries currently in the internal event log."""
        return len(self._log)

    @property
    def input_method_valid(self) -> bool:
        """True when ``input_method`` is one of the recognised values."""
        return self.input_method in ("postmessage", "scancode", "hybrid", "interception")

    def stats_snapshot(self) -> dict[str, Any]:
        """Keys: is_connected, hwnd, target_title, log_count, key_delay, move_mode, input_method, jitter_pct."""
        return {
            "is_connected":   self.is_connected(),
            "hwnd":           self._hwnd,
            "target_title":   self.target_title,
            "log_count":      self.log_count,
            "key_delay":      self.key_delay,
            "move_mode":      self.move_mode,
            "input_method":   self.input_method,
            "jitter_pct":     self.jitter_pct,
        }

    @property
    def has_log(self) -> bool:
        """True when the event log contains at least one entry."""
        return self.log_count > 0

    @property
    def is_scancode(self) -> bool:
        """True when ``input_method`` is 'scancode'."""
        return self.input_method == "scancode"

    @property
    def is_postmessage(self) -> bool:
        """True when ``input_method`` is 'postmessage' (background mode)."""
        return self.input_method == "postmessage"

    @property
    def has_hwnd(self) -> bool:
        """True when a window handle has been found by :meth:`find_target`."""
        return self._hwnd is not None

    @property
    def is_arrow_mode(self) -> bool:
        """True when the movement mode is 'arrow' keys."""
        return self.move_mode == "arrow"

    @property
    def is_wasd_mode(self) -> bool:
        """True when the movement mode is 'wasd' keys."""
        return self.move_mode == "wasd"

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _make_lparam(vk: int, transition: int) -> int:
        """Construye lParam para WM_KEYDOWN/WM_KEYUP."""
        scan = user32.MapVirtualKeyW(vk, 0)
        extended = 1 if vk in _EXTENDED_VK else 0
        context_code  = 0
        prev_state     = transition
        repeat_count   = 1
        lp  = repeat_count
        lp |= (scan & 0xFF) << 16
        lp |= (extended & 1) << 24
        lp |= (context_code & 1) << 29
        lp |= (prev_state & 1) << 30
        lp |= (transition & 1) << 31
        return int(lp & 0xFFFFFFFF)


