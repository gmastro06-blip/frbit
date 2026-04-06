"""Calibración visual interactiva de ROIs."""
import sys
import os
import json
import ctypes
from ctypes import wintypes

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, 'src')

import cv2
import numpy as np
from typing import Any, TypeAlias, TypedDict

user32 = ctypes.windll.user32

Frame: TypeAlias = Any
Roi: TypeAlias = list[int]

def find_projector_hwnd() -> int | None:
    hwnds: list[int] = []

    def enum_callback(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.lower()
                if 'proyector' in title or 'projector' in title:
                    hwnds.append(hwnd)
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return hwnds[0] if hwnds else None

def capture_frame() -> Frame | None:
    proj_hwnd = find_projector_hwnd()
    if not proj_hwnd:
        print("ERROR: No se encontró proyector OBS")
        return None
    
    from src.frame_capture import PrintWindowCapture
    cap = PrintWindowCapture(proj_hwnd)
    get_frame = cap.open()
    return get_frame()

def draw_roi(frame: Frame, roi: Roi, label: str, color: tuple[int, int, int], thickness: int = 2) -> None:
    x, y, w, h = roi
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, thickness)
    cv2.putText(frame, label, (x + 5, y + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

class RoiConfig(TypedDict):
    file: str
    key: str
    color: tuple[int, int, int]
    desc: str


# ROIs configurados - los voy a mostrar sobre el frame actual
ROIS: dict[str, RoiConfig] = {
    'minimap': {
        'file': 'minimap_config.json',
        'key': 'roi',
        'color': (0, 255, 255),  # Amarillo
        'desc': 'Minimap (esquina derecha arriba)'
    },
    'hp_bar': {
        'file': 'hpmp_config.json', 
        'key': 'hp_roi',
        'color': (0, 0, 255),  # Rojo
        'desc': 'Barra HP (izquierda arriba)'
    },
    'mp_bar': {
        'file': 'hpmp_config.json',
        'key': 'mp_roi', 
        'color': (255, 0, 0),  # Azul
        'desc': 'Barra MP (derecha de HP)'
    },
    'battle_list': {
        'file': 'combat_config.json',
        'key': 'battle_list_roi',
        'color': (0, 165, 255),  # Naranja
        'desc': 'Battle List (panel derecho)'
    },
    'viewport': {
        'file': 'loot_config.json',
        'key': 'viewport_roi',
        'color': (0, 255, 0),  # Verde
        'desc': 'Viewport (área de juego)'
    },
}

def load_current_rois() -> dict[str, Roi]:
    """Carga los ROIs actuales de los archivos de configuración."""
    result: dict[str, Roi] = {}
    for name, info in ROIS.items():
        cfg_file = info['file']
        if os.path.exists(cfg_file):
            with open(cfg_file) as f:
                cfg: dict[str, Any] = json.load(f)
            if info['key'] in cfg:
                result[name] = cfg[info['key']]
    return result

def main() -> None:
    print("=== Visualización de ROIs actuales ===\n")
    
    frame = capture_frame()
    if frame is None:
        return
    
    h, w = frame.shape[:2]
    print(f"Frame: {w}x{h}")
    
    # Cargar ROIs actuales
    rois = load_current_rois()
    
    # Dibujar todos los ROIs
    display = frame.copy()
    
    print("\nROIs actuales:")
    for name, roi in rois.items():
        info = ROIS[name]
        print(f"  {name}: {roi} - {info['desc']}")
        draw_roi(display, roi, name, info['color'])
    
    # Guardar imagen
    cv2.imwrite('captures/current_rois.png', display)
    print(f"\nImagen guardada: captures/current_rois.png")
    
    # Mostrar
    print("\nVentana abierta. Observa si los ROIs están bien posicionados.")
    print("Presiona cualquier tecla para cerrar...")
    
    cv2.imshow("ROIs actuales (presiona tecla para cerrar)", display)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
    
    print("\n" + "="*60)
    print("Si los ROIs están mal, necesitas recalibrar manualmente.")
    print("Ejecuta: python main.py calibrate")
    print("="*60)

if __name__ == "__main__":
    main()
