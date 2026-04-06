"""Recalibración automática de ROIs para el tamaño actual del frame."""
import sys
import os
import json
import ctypes
from ctypes import wintypes

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, 'src')

import cv2
import numpy as np
from typing import Any, TypeAlias

user32 = ctypes.windll.user32

Frame: TypeAlias = Any
Roi: TypeAlias = list[int]
Size: TypeAlias = tuple[int, int]

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

def scale_roi(roi: Roi, ref_size: Size, actual_size: Size) -> Roi:
    """Escala un ROI [x, y, w, h] de ref_size a actual_size."""
    x, y, w, h = roi
    sx = actual_size[0] / ref_size[0]
    sy = actual_size[1] / ref_size[1]
    return [int(x * sx), int(y * sy), int(w * sx), int(h * sy)]

def draw_roi(frame: Frame, roi: Roi, label: str, color: tuple[int, int, int]) -> None:
    x, y, w, h = roi
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    cv2.putText(frame, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

def main() -> None:
    print("=== Recalibración de ROIs ===\n")
    
    # Capturar frame actual
    frame = capture_frame()
    if frame is None:
        return
    
    h, w = frame.shape[:2]
    print(f"Frame actual: {w}x{h}")
    
    REF_W, REF_H = 1920, 1080
    print(f"Referencia: {REF_W}x{REF_H}")
    
    sx = w / REF_W
    sy = h / REF_H
    print(f"Factores de escala: X={sx:.4f}, Y={sy:.4f}\n")
    
    # Cargar configuraciones actuales
    config_files = [
        ('minimap_config.json', ['roi']),
        ('hpmp_config.json', ['hp_roi', 'mp_roi', 'hp_text_roi', 'mp_text_roi']),
        ('combat_config.json', ['battle_list_roi']),
        ('loot_config.json', ['viewport_roi', 'container_roi']),
    ]
    
    # Dibujar ROIs originales en rojo y escalados en verde
    display = frame.copy()
    
    for cfg_file, roi_keys in config_files:
        if not os.path.exists(cfg_file):
            print(f"[SKIP] {cfg_file} no existe")
            continue
        
        with open(cfg_file) as f:
            cfg: dict[str, Any] = json.load(f)
        
        print(f"\n{cfg_file}:")
        for key in roi_keys:
            if key not in cfg:
                continue
            
            orig_roi = cfg[key]
            scaled_roi = scale_roi(orig_roi, (REF_W, REF_H), (w, h))
            
            print(f"  {key}:")
            print(f"    Original (1920x1080): {orig_roi}")
            print(f"    Escalado ({w}x{h}):   {scaled_roi}")
            
            # Dibujar
            draw_roi(display, orig_roi, f"{key} (orig)", (0, 0, 255))  # Rojo
            draw_roi(display, scaled_roi, f"{key} (scaled)", (0, 255, 0))  # Verde
    
    # Mostrar imagen
    cv2.imwrite('captures/roi_comparison.png', display)
    print(f"\nImagen guardada en captures/roi_comparison.png")
    print("  Rojo = ROI original (1920x1080)")
    print("  Verde = ROI escalado al tamaño actual")
    
    # Mostrar ventana
    cv2.imshow("ROI Comparison (Q para salir)", cv2.resize(display, (960, int(960 * h / w))))
    print("\nPresiona 'Q' para cerrar y continuar...")
    while True:
        if cv2.waitKey(100) & 0xFF == ord('q'):
            break
    cv2.destroyAllWindows()
    
    # Preguntar si actualizar
    print("\n" + "="*50)
    resp = input("¿Actualizar los archivos de configuración con los ROIs escalados? (s/n): ")
    
    if resp.lower() == 's':
        for cfg_file, roi_keys in config_files:
            if not os.path.exists(cfg_file):
                continue
            
            updated_cfg: dict[str, Any]
            with open(cfg_file) as f:
                updated_cfg = json.load(f)
            
            updated = False
            for key in roi_keys:
                if key not in updated_cfg:
                    continue
                orig_roi = updated_cfg[key]
                scaled_roi = scale_roi(orig_roi, (REF_W, REF_H), (w, h))
                updated_cfg[key] = scaled_roi
                updated = True
            
            # Actualizar ref_width y ref_height si existen
            if 'ref_width' in updated_cfg:
                updated_cfg['ref_width'] = w
            if 'ref_height' in updated_cfg:
                updated_cfg['ref_height'] = h
            
            if updated:
                with open(cfg_file, 'w') as f:
                    json.dump(updated_cfg, f, indent=2)
                print(f"[OK] {cfg_file} actualizado")
        
        print("\n¡Configuraciones actualizadas!")
    else:
        print("\nNo se realizaron cambios.")

if __name__ == "__main__":
    main()
