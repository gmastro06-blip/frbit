"""Calibración manual interactiva de ROIs con mouse."""
import sys
import os
import json
import ctypes
from ctypes import wintypes

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, 'src')

import cv2
import numpy as np
from typing import Any, Literal, TypeAlias, TypedDict

user32 = ctypes.windll.user32

Frame: TypeAlias = Any
Roi: TypeAlias = list[int]


class RoiCalibration(TypedDict):
    name: str
    file: str
    key: str
    desc: str


class RoiUpdate(TypedDict):
    file: str
    key: str
    value: Roi

# Estado global para el callback del mouse
drawing = False
ix, iy = -1, -1
current_roi: Roi = [0, 0, 0, 0]
temp_frame: Frame | None = None

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

def mouse_callback(event: int, x: int, y: int, flags: int, param: Any) -> None:
    global drawing, ix, iy, current_roi, temp_frame
    
    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        ix, iy = x, y
        current_roi = [x, y, 0, 0]
    
    elif event == cv2.EVENT_MOUSEMOVE:
        if drawing:
            if temp_frame is None:
                return
            temp = temp_frame.copy()
            cv2.rectangle(temp, (ix, iy), (x, y), (0, 255, 0), 2)
            cv2.imshow('Calibracion', temp)
    
    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        x1, y1 = min(ix, x), min(iy, y)
        x2, y2 = max(ix, x), max(iy, y)
        current_roi = [x1, y1, x2 - x1, y2 - y1]

def calibrate_roi(
    frame: Frame,
    name: str,
    description: str,
    current_value: Roi | None = None,
) -> Roi | None | Literal['quit']:
    """Permite al usuario dibujar un ROI sobre el frame."""
    global temp_frame, current_roi
    
    temp_frame = frame.copy()
    current_roi = current_value if current_value else [0, 0, 100, 100]
    
    # Dibujar ROI actual si existe
    if current_value:
        x, y, w, h = current_value
        cv2.rectangle(temp_frame, (x, y), (x + w, y + h), (0, 0, 255), 2)
        cv2.putText(temp_frame, "Actual (rojo)", (x, y - 5), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    
    # Instrucciones
    cv2.putText(temp_frame, f"ROI: {name}", (10, 30), 
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    cv2.putText(temp_frame, description, (10, 60), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 1)
    cv2.putText(temp_frame, "Dibuja rectangulo con mouse. ENTER=confirmar, S=skip, Q=salir", 
                (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    
    cv2.namedWindow('Calibracion')
    cv2.setMouseCallback('Calibracion', mouse_callback)
    cv2.imshow('Calibracion', temp_frame)
    
    while True:
        key = cv2.waitKey(1) & 0xFF
        if key == 13:  # ENTER
            if current_roi[2] > 10 and current_roi[3] > 10:
                return current_roi
            else:
                print("  ROI muy pequeño, intenta de nuevo")
        elif key == ord('s'):  # Skip
            return None
        elif key == ord('q'):  # Quit
            return 'quit'
    
    return None

ROIS_TO_CALIBRATE: list[RoiCalibration] = [
    {
        'name': 'minimap',
        'file': 'minimap_config.json',
        'key': 'roi',
        'desc': 'Minimap - cuadrado en esquina superior derecha'
    },
    {
        'name': 'hp_bar',
        'file': 'hpmp_config.json',
        'key': 'hp_roi',
        'desc': 'Barra de HP - barra roja arriba izquierda'
    },
    {
        'name': 'mp_bar',
        'file': 'hpmp_config.json',
        'key': 'mp_roi',
        'desc': 'Barra de MP - barra azul arriba (junto a HP)'
    },
    {
        'name': 'battle_list',
        'file': 'combat_config.json',
        'key': 'battle_list_roi',
        'desc': 'Battle List - lista de monstruos panel derecho'
    },
    {
        'name': 'viewport',
        'file': 'loot_config.json',
        'key': 'viewport_roi',
        'desc': 'Viewport - area de juego central (donde camina personaje)'
    },
]

def main() -> None:
    print("="*60)
    print("  CALIBRACION MANUAL DE ROIs")
    print("="*60)
    print("\nInstrucciones:")
    print("  - Dibuja un rectangulo con el mouse sobre cada area")
    print("  - ENTER = confirmar ROI")
    print("  - S = saltar este ROI (mantener valor actual)")
    print("  - Q = salir sin guardar")
    print()
    
    frame = capture_frame()
    if frame is None:
        return
    
    h, w = frame.shape[:2]
    print(f"Frame capturado: {w}x{h}\n")
    
    # Cargar configs actuales
    configs: dict[str, dict[str, Any]] = {}
    for roi_info in ROIS_TO_CALIBRATE:
        cfg_file = roi_info['file']
        if cfg_file not in configs and os.path.exists(cfg_file):
            with open(cfg_file) as f:
                configs[cfg_file] = json.load(f)
    
    # Calibrar cada ROI
    new_rois: dict[str, RoiUpdate] = {}
    for roi_info in ROIS_TO_CALIBRATE:
        name = roi_info['name']
        cfg_file = roi_info['file']
        key = roi_info['key']
        desc = roi_info['desc']
        
        current = None
        if cfg_file in configs and key in configs[cfg_file]:
            current = configs[cfg_file][key]
        
        print(f"\n>>> Calibrando: {name}")
        print(f"    Actual: {current}")
        
        result = calibrate_roi(frame, name, desc, current)
        
        if result == 'quit':
            print("\nSaliendo sin guardar...")
            cv2.destroyAllWindows()
            return
        elif result is None:
            print(f"    Saltado - manteniendo valor actual")
        else:
            new_rois[name] = {
                'file': cfg_file,
                'key': key,
                'value': result
            }
            print(f"    Nuevo: {result}")
    
    cv2.destroyAllWindows()
    
    # Mostrar resumen y confirmar
    print("\n" + "="*60)
    print("  RESUMEN DE CAMBIOS")
    print("="*60)
    
    if not new_rois:
        print("\nNo hay cambios que guardar.")
        return
    
    for name, info in new_rois.items():
        print(f"  {name}: {info['value']}")
    
    print()
    resp = input("¿Guardar estos cambios? (s/n): ")
    
    if resp.lower() == 's':
        # Agrupar por archivo
        changes_by_file: dict[str, dict[str, list[int]]] = {}
        for name, info in new_rois.items():
            cfg_file = info['file']
            if cfg_file not in changes_by_file:
                changes_by_file[cfg_file] = {}
            changes_by_file[cfg_file][info['key']] = info['value']
        
        # Aplicar cambios
        for cfg_file, changes in changes_by_file.items():
            cfg: dict[str, Any]
            if os.path.exists(cfg_file):
                with open(cfg_file) as f:
                    cfg = json.load(f)
            else:
                cfg = {}
            
            for key, value in changes.items():
                cfg[key] = value
            
            # Actualizar ref_width/ref_height
            cfg['ref_width'] = w
            cfg['ref_height'] = h
            
            with open(cfg_file, 'w') as f:
                json.dump(cfg, f, indent=2)
            
            print(f"  [OK] {cfg_file} actualizado")
        
        print("\n¡Calibración completada!")
    else:
        print("\nNo se guardaron cambios.")

if __name__ == "__main__":
    main()
