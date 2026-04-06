"""
tools/calibrate_storage_tabs.py
--------------------------------
Calibra los offsets de los tabs del depot (Stash, Inbox, Store Inbox).

Uso:
  1. Abre el chest del depot en Tibia (que se vean los tabs)
  2. python tools/calibrate_storage_tabs.py
  3. En la ventana:
       - Click en el tab "Stash"       → botón S / tecla S
       - Click en el tab "Inbox"       → botón I / tecla I
       - Click en el tab "Store Inbox" → botón T / tecla T
       - Botón "Listo" para ver valores
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageDraw
import numpy as np
import win32gui

OBS_TITLE_FRAGMENT = "Tibia_Fuente"

TABS = {
    "s": ("STASH",       "StorageSurface.STASH"),
    "i": ("INBOX",       "StorageSurface.INBOX"),
    "t": ("STORE_INBOX", "StorageSurface.STORE_INBOX"),
}


# ── Captura ──────────────────────────────────────────────────────────────────

def _find_obs_hwnd() -> int:
    result = []
    def cb(hwnd, _):
        if OBS_TITLE_FRAGMENT in win32gui.GetWindowText(hwnd):
            result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else 0


def _capture_hwnd(hwnd: int) -> np.ndarray | None:
    try:
        import win32ui, win32con
        from ctypes import windll
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc  = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp     = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)
        bits = bmp.GetBitmapBits(True)
        img  = np.frombuffer(bits, dtype=np.uint8).reshape(h, w, 4)
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC(); mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        # BGRA → RGB for PIL
        return img[:, :, [2, 1, 0]]
    except Exception as e:
        print(f"[!] Capture failed: {e}")
        return None


# ── Detector ─────────────────────────────────────────────────────────────────

def _detect_container(frame_rgb):
    import cv2
    bgr = frame_rgb[:, :, ::-1].copy()
    from src.storage_detector import StorageDetector, StorageDetectorConfig
    det = StorageDetector(config=StorageDetectorConfig(state_ttl_s=0.0))
    state = det.detect(bgr)
    if state.open_windows:
        return state.open_windows[0].roi  # (x, y, w, h)
    return None


# ── App Tkinter ───────────────────────────────────────────────────────────────

class CalibApp:
    SCALE = 0.67   # display scale (1920→~1280)

    def __init__(self, frame_rgb: np.ndarray, container_roi):
        self.frame_rgb     = frame_rgb
        self.container_roi = container_roi   # (x, y, w, h) or None
        self.fh, self.fw   = frame_rgb.shape[:2]
        self.recorded: dict[str, tuple[int,int]] = {}   # key → (raw_x, raw_y)
        self.pending_key: str | None = list(TABS.keys())[0]
        self.last_click: tuple[int,int] = (0, 0)

        self.root = tk.Tk()
        self.root.title("Calibrate Storage Tabs")

        # Canvas
        dw = int(self.fw * self.SCALE)
        dh = int(self.fh * self.SCALE)
        self.canvas = tk.Canvas(self.root, width=dw, height=dh, cursor="crosshair")
        self.canvas.pack(side=tk.LEFT)

        # Control panel
        panel = tk.Frame(self.root, padx=10, pady=10)
        panel.pack(side=tk.RIGHT, fill=tk.Y)

        self.status_var = tk.StringVar(value="Haz click en el frame")
        tk.Label(panel, textvariable=self.status_var, wraplength=220,
                 justify=tk.LEFT, font=("Consolas", 10)).pack(anchor=tk.W, pady=(0,12))

        self.coord_var = tk.StringVar(value="—")
        tk.Label(panel, text="Último click:", font=("Consolas", 9, "bold")).pack(anchor=tk.W)
        tk.Label(panel, textvariable=self.coord_var,
                 font=("Consolas", 10), fg="#0066cc").pack(anchor=tk.W, pady=(0,12))

        tk.Label(panel, text="Registrar tab:", font=("Consolas", 10, "bold")).pack(anchor=tk.W)
        for key, (label, _) in TABS.items():
            btn = tk.Button(panel, text=f"[{key.upper()}] {label}",
                            font=("Consolas", 10), width=18,
                            command=lambda k=key: self._record(k))
            btn.pack(anchor=tk.W, pady=2)
            setattr(self, f"btn_{key}", btn)

        ttk.Separator(panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        self.result_text = tk.Text(panel, width=28, height=12,
                                   font=("Consolas", 8), state=tk.DISABLED)
        self.result_text.pack(anchor=tk.W)

        tk.Button(panel, text="Listo / Mostrar valores",
                  font=("Consolas", 10), bg="#28a745", fg="white",
                  command=self._show_results).pack(pady=(8,0), fill=tk.X)

        # Bind events
        self.canvas.bind("<Button-1>", self._on_click)
        self.root.bind("<Key>", self._on_key)

        # Draw initial frame
        self._pil_base = Image.fromarray(frame_rgb).resize((dw, dh), Image.LANCZOS)
        self._draw_overlay()

    def _draw_overlay(self):
        img = self._pil_base.copy()
        draw = ImageDraw.Draw(img)
        dw, dh = img.size
        sx = dw / self.fw
        sy = dh / self.fh

        # Container box
        if self.container_roi:
            x, y, w, h = self.container_roi
            draw.rectangle([x*sx, y*sy, (x+w)*sx, (y+h)*sy],
                           outline="#ff8800", width=2)

        # Recorded clicks
        COLORS = {"s": "#00ee44", "i": "#44aaff", "t": "#ff44ff"}
        for k, (rx, ry) in self.recorded.items():
            cx, cy = int(rx*sx), int(ry*sy)
            r = 8
            draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=COLORS[k], width=2)
            draw.line([cx-14,cy, cx+14,cy], fill=COLORS[k], width=2)
            draw.line([cx,cy-14, cx,cy+14], fill=COLORS[k], width=2)
            draw.text((cx+10, cy-14), TABS[k][0], fill=COLORS[k])

        self._tk_img = ImageTk.PhotoImage(img)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self._tk_img)

    def _on_click(self, event):
        sx = self.fw / (self.fw * self.SCALE)
        sy = self.fh / (self.fh * self.SCALE)
        rx = int(event.x * sx)
        ry = int(event.y * sy)
        self.last_click = (rx, ry)
        ox, oy = (self.container_roi[0], self.container_roi[1]) if self.container_roi else (0, 0)
        self.coord_var.set(f"raw=({rx},{ry})\nrel=({rx-ox},{ry-oy})")
        self._update_status()

    def _on_key(self, event):
        k = event.char.lower()
        if k in TABS:
            self._record(k)

    def _record(self, key: str):
        if self.last_click == (0, 0):
            messagebox.showwarning("Sin click", "Haz click en el tab primero")
            return
        self.recorded[key] = self.last_click
        self._draw_overlay()
        self._update_status()
        print(f"  [{TABS[key][0]}] → raw={self.last_click}")
        if len(self.recorded) == len(TABS):
            self._show_results()

    def _update_status(self):
        pending = [TABS[k][0] for k in TABS if k not in self.recorded]
        done    = [TABS[k][0] for k in self.recorded]
        msg = ""
        if pending:
            msg += f"Pendiente: {', '.join(pending)}\n"
        if done:
            msg += f"Registrado: {', '.join(done)}"
        self.status_var.set(msg or "¡Todos registrados!")

    def _show_results(self):
        if not self.recorded:
            messagebox.showinfo("Sin datos", "No se registró ningún tab")
            return

        ox, oy = (self.container_roi[0], self.container_roi[1]) if self.container_roi else (0,0)
        lines = ["# _DEPOT_TABS en storage_navigator.py\n",
                 "_DEPOT_TABS = {\n"]
        for k, label in [("s","STASH"),("i","INBOX"),("t","STORE_INBOX")]:
            if k in self.recorded:
                rx, ry = self.recorded[k]
                rel_x = rx - ox
                rel_y = ry - oy
                # Scale to 1920×1080 reference
                ref_x = round(rel_x * 1920 / self.fw)
                ref_y = round(rel_y * 1080 / self.fh)
                lines.append(f"    StorageSurface.{label}:\n      ({ref_x}, {ref_y}),\n")
            else:
                lines.append(f"    # {label}: no registrado\n")
        lines.append("}\n")

        # Manage button estimate
        if "t" in self.recorded:
            rx, ry = self.recorded["t"]
            ref_x = round((rx - ox + 40) * 1920 / self.fw)
            ref_y = round((ry - oy) * 1080 / self.fh)
            lines.append(f"\n# _MANAGE_BTN_OFFSET\n({ref_x}, {ref_y})\n")

        result = "".join(lines)
        print("\n" + "="*55)
        print(result)
        print("="*55)

        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", result)
        self.result_text.config(state=tk.DISABLED)

    def run(self):
        self.root.mainloop()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("="*55)
    print("  Calibración de tabs del depot")
    print("="*55)

    hwnd = _find_obs_hwnd()
    if not hwnd:
        print(f"[!] No se encontró ventana OBS '{OBS_TITLE_FRAGMENT}'")
        sys.exit(1)
    print(f"[+] OBS HWND={hwnd}")

    frame_rgb = _capture_hwnd(hwnd)
    if frame_rgb is None:
        print("[!] Captura fallida")
        sys.exit(1)
    print(f"[+] Frame: {frame_rgb.shape[1]}x{frame_rgb.shape[0]}")

    roi = _detect_container(frame_rgb)
    if roi:
        print(f"[+] Contenedor: roi={roi}")
    else:
        print("[!] Contenedor no detectado — abre el depot chest primero")

    app = CalibApp(frame_rgb, roi)
    app.run()


if __name__ == "__main__":
    main()
