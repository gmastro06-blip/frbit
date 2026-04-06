"""code.py — Pico 2 HID Emulator (CircuitPython).

Receives serial commands over USB CDC and emulates real USB HID
keyboard/mouse events indistinguishable from physical hardware.

Protocol (newline-terminated, USB CDC serial):
  PING                          -> PONG
  KEY_PRESS|<key>|<duration_ms> -> ACK
  KEY_RELEASE|<key>             -> ACK
  RELEASE_ALL                   -> ACK  (release all held keys + mouse buttons)
  MOUSE_MOVE|<x>|<y>|<rel>     -> ACK  (rel: 0=absolute, 1=relative)
  MOUSE_CLICK|<button>          -> ACK  (LEFT / RIGHT / MIDDLE)
  MOUSE_SCROLL|<amount>         -> ACK  (positive=up, negative=down)
  STATUS                        -> OK|<uptime_ms>|<cmd_count>

Board: Raspberry Pi Pico 2 (RP2350)
Firmware: CircuitPython 9.0+

Setup:
  1. Install CircuitPython 9.x on your Pico 2
  2. Copy boot.py and code.py to CIRCUITPY drive
  3. Pico will appear as both HID device and serial port
"""

try:
    import supervisor
    _get_ticks_ms = supervisor.ticks_ms
except ImportError:
    import time
    _get_ticks_ms = lambda: int(time.time() * 1000)
import time
import usb_cdc
import usb_hid

from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
from adafruit_hid.mouse import Mouse

# ── Setup ────────────────────────────────────────────────────────────────────

_kbd = Keyboard(usb_hid.devices)
_mouse = Mouse(usb_hid.devices)
_serial = usb_cdc.console

_CMD_BUF_SIZE = 128
_cmd_buf = bytearray(_CMD_BUF_SIZE)
_cmd_idx = 0
_boot_ticks = _get_ticks_ms()
_cmd_count = 0

# ── Key Mapping ──────────────────────────────────────────────────────────────

_SPECIAL_KEYS = {
    "ENTER": Keycode.ENTER,
    "RETURN": Keycode.ENTER,
    "ESC": Keycode.ESCAPE,
    "ESCAPE": Keycode.ESCAPE,
    "TAB": Keycode.TAB,
    "SPACE": Keycode.SPACE,
    "BACKSPACE": Keycode.BACKSPACE,
    "DELETE": Keycode.DELETE,
    "INSERT": Keycode.INSERT,
    "HOME": Keycode.HOME,
    "END": Keycode.END,
    "PAGEUP": Keycode.PAGE_UP,
    "PAGEDOWN": Keycode.PAGE_DOWN,
    "UP": Keycode.UP_ARROW,
    "DOWN": Keycode.DOWN_ARROW,
    "LEFT": Keycode.LEFT_ARROW,
    "RIGHT": Keycode.RIGHT_ARROW,
    "LCTRL": Keycode.LEFT_CONTROL,
    "CTRL": Keycode.LEFT_CONTROL,
    "LSHIFT": Keycode.LEFT_SHIFT,
    "SHIFT": Keycode.LEFT_SHIFT,
    "LALT": Keycode.LEFT_ALT,
    "ALT": Keycode.LEFT_ALT,
    "CAPSLOCK": Keycode.CAPS_LOCK,
}

# Function keys F1-F12
for _i in range(1, 13):
    _SPECIAL_KEYS[f"F{_i}"] = getattr(Keycode, f"F{_i}")

# Lowercase ASCII a-z -> Keycode.A .. Keycode.Z
_ASCII_KEYS = {}
for _c in range(ord("a"), ord("z") + 1):
    _ASCII_KEYS[chr(_c)] = getattr(Keycode, chr(_c).upper())
for _c in range(ord("A"), ord("Z") + 1):
    _ASCII_KEYS[chr(_c)] = getattr(Keycode, chr(_c))

# Digits 0-9
for _d in range(10):
    _k = f"ZERO" if _d == 0 else ["ONE", "TWO", "THREE", "FOUR", "FIVE",
          "SIX", "SEVEN", "EIGHT", "NINE"][_d - 1]
    _ASCII_KEYS[str(_d)] = getattr(Keycode, _k)


def _parse_key(key_str):
    """Parse a key string to a Keycode constant."""
    upper = key_str.upper()
    if upper in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[upper]
    if key_str in _ASCII_KEYS:
        return _ASCII_KEYS[key_str]
    if upper in _ASCII_KEYS:
        return _ASCII_KEYS[upper]
    return None


def _parse_mouse_button(btn_str):
    """Parse a mouse button string."""
    upper = btn_str.upper()
    if upper == "LEFT":
        return Mouse.LEFT_BUTTON
    if upper == "RIGHT":
        return Mouse.RIGHT_BUTTON
    if upper == "MIDDLE":
        return Mouse.MIDDLE_BUTTON
    return None


# ── Command Handlers ─────────────────────────────────────────────────────────

def _respond(msg):
    """Send response over serial."""
    _serial.write((msg + "\n").encode())


def _handle_ping():
    _respond("PONG")


def _handle_key_press(args):
    """KEY_PRESS|<key>|<duration_ms>"""
    parts = args.split("|")
    if len(parts) < 1:
        _respond("ERR")
        return
    key_str = parts[0]
    duration_ms = int(parts[1]) if len(parts) > 1 else 80

    kc = _parse_key(key_str)
    if kc is None:
        _respond("ERR")
        return

    # Cap at 5 seconds
    if duration_ms > 5000:
        duration_ms = 5000

    _kbd.press(kc)
    time.sleep(duration_ms / 1000.0)
    _kbd.release(kc)
    _respond("ACK")


def _handle_key_release(args):
    """KEY_RELEASE|<key>"""
    kc = _parse_key(args.strip())
    if kc is None:
        _respond("ERR")
        return
    _kbd.release(kc)
    _respond("ACK")


def _handle_mouse_move(args):
    """MOUSE_MOVE|<x>|<y>|<rel>"""
    parts = args.split("|")
    if len(parts) < 2:
        _respond("ERR")
        return

    x = int(parts[0])
    y = int(parts[1])
    rel = int(parts[2]) if len(parts) > 2 else 1

    if rel:
        # Relative move — chunk into <=127 steps (HID report limit)
        while x != 0 or y != 0:
            dx = max(-127, min(127, x))
            dy = max(-127, min(127, y))
            _mouse.move(dx, dy, 0)
            x -= dx
            y -= dy
    else:
        # Absolute: move to origin, then to target
        _mouse.move(-16383, -16383, 0)
        time.sleep(0.005)
        rx, ry = x, y
        while rx != 0 or ry != 0:
            dx = max(-127, min(127, rx))
            dy = max(-127, min(127, ry))
            _mouse.move(dx, dy, 0)
            rx -= dx
            ry -= dy

    _respond("ACK")


def _handle_mouse_click(args):
    """MOUSE_CLICK|<button>"""
    btn = _parse_mouse_button(args.strip())
    if btn is None:
        _respond("ERR")
        return

    _mouse.press(btn)
    # Human-like click duration: random-ish via ticks
    jitter = (_get_ticks_ms() % 50) + 10
    time.sleep(jitter / 1000.0)
    _mouse.release(btn)
    _respond("ACK")


def _handle_mouse_scroll(args):
    """MOUSE_SCROLL|<amount>"""
    try:
        amount = int(args.strip())
    except ValueError:
        _respond("ERR")
        return
    _mouse.move(0, 0, amount)
    _respond("ACK")


def _handle_combo(args):
    """COMBO|<modifier>|<key>|<duration_ms> — holds modifier while pressing key."""
    parts = args.split("|")
    if len(parts) < 2:
        _respond("ERR")
        return
    mod_kc = _parse_key(parts[0])
    key_kc = _parse_key(parts[1])
    if mod_kc is None or key_kc is None:
        _respond("ERR")
        return
    duration_ms = int(parts[2]) if len(parts) > 2 else 80
    if duration_ms > 5000:
        duration_ms = 5000
    _kbd.press(mod_kc)
    time.sleep(0.03)
    _kbd.press(key_kc)
    time.sleep(duration_ms / 1000.0)
    _kbd.release(key_kc)
    _kbd.release(mod_kc)
    _respond("ACK")


def _handle_release_all(_args=""):
    """RELEASE_ALL -> ACK — release every held key and mouse button."""
    _kbd.release_all()
    _mouse.release_all()
    _respond("ACK")


def _handle_status():
    """STATUS -> OK|<uptime_ms>|<cmd_count>"""
    uptime = _get_ticks_ms() - _boot_ticks
    _respond(f"OK|{uptime}|{_cmd_count}")


# ── Command Dispatcher ───────────────────────────────────────────────────────

_HANDLERS = {
    "PING": lambda _: _handle_ping(),
    "KEY_PRESS": _handle_key_press,
    "KEY_RELEASE": _handle_key_release,
    "COMBO": _handle_combo,
    "RELEASE_ALL": _handle_release_all,
    "MOUSE_MOVE": _handle_mouse_move,
    "MOUSE_CLICK": _handle_mouse_click,
    "MOUSE_SCROLL": _handle_mouse_scroll,
    "STATUS": lambda _: _handle_status(),
}


def _process_command(cmd_str):
    """Parse and dispatch a command string."""
    global _cmd_count

    cmd_str = cmd_str.strip()
    if not cmd_str:
        return

    pipe_idx = cmd_str.find("|")
    if pipe_idx == -1:
        verb = cmd_str
        rest = ""
    else:
        verb = cmd_str[:pipe_idx]
        rest = cmd_str[pipe_idx + 1:]

    handler = _HANDLERS.get(verb)
    if handler is not None:
        handler(rest)
        _cmd_count += 1
    else:
        _respond("ERR")


# ── Main Loop ────────────────────────────────────────────────────────────────

while True:
    if _serial.in_waiting:
        data = _serial.read(_serial.in_waiting)
        for byte in data:
            if byte in (0x0A, 0x0D):  # \n or \r
                if _cmd_idx > 0:
                    cmd = _cmd_buf[:_cmd_idx].decode("utf-8", "ignore")
                    _cmd_idx = 0
                    _process_command(cmd)
            else:
                if _cmd_idx < _CMD_BUF_SIZE:
                    _cmd_buf[_cmd_idx] = byte
                    _cmd_idx += 1
    else:
        time.sleep(0.001)  # 1ms idle — low latency, low power
