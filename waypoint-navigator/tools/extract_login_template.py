

"""Extract login_screen.png template from a live OBS/Tibia window or a saved screenshot.

Usage:
    python tools/extract_login_template.py                     # capture from live window
    python tools/extract_login_template.py path/to/screenshot.png  # use saved file
"""
from __future__ import annotations

import sys
import pathlib
import ctypes
import ctypes.wintypes as w

import cv2
import numpy as np

TEMPLATE_DIR = pathlib.Path(__file__).parent.parent / "cache" / "templates"


# ── Window enumeration ──────────────────────────────────────────────
def _find_window() -> int | None:
    """Find OBS projector or Tibia window handle."""
    user32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(256)
    matches: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    def cb(hwnd: int, _lp: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        user32.GetWindowTextW(hwnd, buf, 256)
        t = buf.value.lower()
        if "projector" in t or "tibia" in t:
            matches.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    if not matches:
        return None
    # prefer projector window
    for hwnd, title in matches:
        if "projector" in title.lower():
            print(f"  Found projector: {title!r} (0x{hwnd:08X})")
            return hwnd
    hwnd, title = matches[0]
    print(f"  Found window: {title!r} (0x{hwnd:08X})")
    return hwnd


def _capture_window(hwnd: int) -> np.ndarray | None:
    """Capture client area of hwnd via mss."""
    try:
        import mss
    except ImportError:
        print("ERROR: mss not installed. pip install mss")
        return None

    user32 = ctypes.windll.user32
    pt = w.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    cr = w.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    region = {
        "left": pt.x,
        "top": pt.y,
        "width": cr.right,
        "height": cr.bottom,
    }
    with mss.mss() as sct:
        shot = sct.grab(region)
        img = np.array(shot)[:, :, :3]  # drop alpha
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


# ── Login dialog detection ──────────────────────────────────────────
def _find_login_dialog(frame: np.ndarray) -> tuple[int, int, int, int] | None:
    """Detect the 'Journey Onwards' login dialog box via gray-band detection.

    Returns (x, y, w, h) of the dialog region or None.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    h, w_frame = gray.shape[:2]

    # The login dialog is a dark-gray box (~50-80 brightness) centered on screen.
    # Scan horizontal bands to find the longest contiguous gray region.
    mid_y = h // 2
    scan_range = range(max(0, mid_y - h // 4), min(h, mid_y + h // 4))

    best_x0, best_x1, best_y0, best_y1 = 0, 0, 0, 0
    best_area = 0

    # Look for columns where many rows are uniformly gray (dialog body)
    center_x = w_frame // 2
    search_left = max(0, center_x - w_frame // 3)
    search_right = min(w_frame, center_x + w_frame // 3)

    # Build a mask of "dialog gray" pixels (40-100 brightness, low variance)
    roi = gray[:, search_left:search_right]
    mask = ((roi >= 35) & (roi <= 110)).astype(np.uint8) * 255

    # Find contours on the mask
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        area = cw * ch
        # Dialog should be substantial and roughly centered
        abs_x = x + search_left
        if area > best_area and cw > w_frame * 0.15 and ch > h * 0.15:
            best_x0 = abs_x
            best_y0 = y
            best_x1 = abs_x + cw
            best_y1 = y + ch
            best_area = area

    if best_area == 0:
        return None

    return (best_x0, best_y0, best_x1 - best_x0, best_y1 - best_y0)


def extract_template(frame: np.ndarray) -> np.ndarray | None:
    """Extract the login dialog as a grayscale template from frame.

    Crops the dialog area including the 'Journey Onwards' header,
    Email/Password form, and buttons.
    """
    dialog = _find_login_dialog(frame)
    if dialog is None:
        print("  Could not auto-detect login dialog, using center crop fallback")
        # Fallback: crop center ~40% of the frame
        h, w = frame.shape[:2]
        cx, cy = w // 2, h // 2
        rw, rh = int(w * 0.35), int(h * 0.55)
        x0 = max(0, cx - rw // 2)
        y0 = max(0, cy - rh // 2)
        dialog = (x0, y0, rw, rh)

    x, y, w, h = dialog
    print(f"  Dialog region: x={x}, y={y}, w={w}, h={h}")

    # Add small padding for context
    fh, fw = frame.shape[:2]
    pad = 5
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(fw, x + w + pad)
    y1 = min(fh, y + h + pad)

    crop = frame[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    return gray


def main() -> None:
    out_path = TEMPLATE_DIR / "login_screen.png"
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    frame: np.ndarray | None = None

    if len(sys.argv) > 1:
        # Load from file
        img_path = pathlib.Path(sys.argv[1])
        if not img_path.exists():
            print(f"ERROR: File not found: {img_path}")
            sys.exit(1)
        frame = cv2.imread(str(img_path))
        if frame is None:
            print(f"ERROR: Could not read image: {img_path}")
            sys.exit(1)
        print(f"  Loaded {img_path} ({frame.shape[1]}x{frame.shape[0]})")
    else:
        # Capture from live window
        print("  Searching for OBS/Tibia window...")
        hwnd = _find_window()
        if hwnd is None:
            print("ERROR: No OBS projector or Tibia window found.")
            print("  Either open the game at login screen, or pass a screenshot path:")
            print("  python tools/extract_login_template.py path/to/screenshot.png")
            sys.exit(1)
        frame = _capture_window(hwnd)
        if frame is None:
            sys.exit(1)
        print(f"  Captured frame: {frame.shape[1]}x{frame.shape[0]}")

        # Also save the raw capture for reference
        raw_path = TEMPLATE_DIR.parent.parent / "output" / "login_capture_raw.png"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(raw_path), frame)
        print(f"  Raw capture saved: {raw_path}")

    # Extract template
    tmpl = extract_template(frame)
    if tmpl is None:
        print("ERROR: Failed to extract login template")
        sys.exit(1)

    cv2.imwrite(str(out_path), tmpl)
    print(f"  Template saved: {out_path} ({tmpl.shape[1]}x{tmpl.shape[0]})")

    # Verify
    check = cv2.imread(str(out_path), cv2.IMREAD_GRAYSCALE)
    if check is not None and check.shape == tmpl.shape:
        print("  OK — verified saved file matches")
    else:
        print("  WARNING: saved file verification failed")


if __name__ == "__main__":
    main()
