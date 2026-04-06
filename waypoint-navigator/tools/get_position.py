"""Detecta posición actual del personaje."""
import sys, os
from typing import Any, TypeAlias

src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, src_dir)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import ctypes
from ctypes import wintypes
from src.frame_capture import build_frame_getter
from src.minimap_radar import MinimapRadar
from src.map_loader import TibiaMapLoader

Frame: TypeAlias = Any


def find_projector_hwnd() -> int | None:
    user32 = ctypes.windll.user32
    hwnds: list[int] = []

    def enum_callback(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.lower()
                if "proyector" in title or "projector" in title:
                    hwnds.append(hwnd)
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return hwnds[0] if hwnds else None

def main() -> None:
    hwnd = find_projector_hwnd()
    if not hwnd:
        print("ERROR: No se encontró proyector OBS")
        return
    
    grab = build_frame_getter("printwindow", hwnd=hwnd)
    loader = TibiaMapLoader()
    radar = MinimapRadar(loader)
    
    frame = grab()
    if frame is None:
        print("ERROR: No se pudo capturar frame")
        return
    
    # Intentar varias veces sin hint
    for i in range(5):
        coord = radar.read(frame, hint=None)
        if coord:
            print(f"Posición detectada: x={coord.x}, y={coord.y}, z={coord.z}")
            return
        frame = grab()
        if frame is None:
            print("ERROR: No se pudo capturar frame")
            return
    
    print("No se pudo detectar posición")

if __name__ == "__main__":
    main()
