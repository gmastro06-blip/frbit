"""Diagnóstico de posición del minimap reader."""
import sys
import os
import ctypes
from ctypes import wintypes
from typing import Any

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, 'src')

user32 = ctypes.windll.user32

# Buscar ventana del proyector OBS
hwnds = []


def enum_callback(hwnd: int, _: int) -> bool:
    if user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.lower()
            if 'proyector' in title or 'projector' in title or 'tibia' in title:
                print(f'  hwnd={hex(hwnd)} title="{buf.value}"')
                hwnds.append((hwnd, buf.value))
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
print('Ventanas encontradas:')
user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
print(f'Total: {len(hwnds)} ventanas relevantes\n')

# Intentar capturar del proyector
proj_hwnd = None
for hwnd, title in hwnds:
    if 'proyector' in title.lower() or 'projector' in title.lower():
        proj_hwnd = hwnd
        break

if proj_hwnd:
    from src.frame_capture import PrintWindowCapture
    cap = PrintWindowCapture(proj_hwnd)
    get_frame = cap.open()  # .open() returns a callable
    frame = get_frame()
    print(f'Captura del proyector: {frame.shape if frame is not None else None}')
    
    if frame is not None:
        import cv2
        os.makedirs('captures', exist_ok=True)
        cv2.imwrite('captures/position_check.png', frame)
        print('Frame guardado en captures/position_check.png')
        
        # Leer posicion con TibiaLocalMinimapReader
        from src.minimap_radar import TibiaLocalMinimapReader
        reader = TibiaLocalMinimapReader()
        print(f'\nMinimap reader disponible: {reader.is_available}')
        print(f'Floor actual (local): {reader.current_floor()}')
        
        pos = reader.read(frame)
        print(f'Posición detectada: {pos}')
        
        if pos:
            print(f'\n  X: {pos.x}')
            print(f'  Y: {pos.y}')
            print(f'  Z: {pos.z}')
            
            # Comparar con coordenadas conocidas de Thais
            print('\nReferencias Thais:')
            print('  Depot: 32369, 32241, 7')
            print('  Temple: 32343, 32211, 7')
            print('  Sewer entrada: 32332, 32192, 7')
            
            dx_depot = pos.x - 32369
            dy_depot = pos.y - 32241
            print(f'\nDistancia a Thais Depot: dx={dx_depot}, dy={dy_depot}')
else:
    print('ERROR: No se encontró ventana del proyector OBS')
    print('Asegúrate de tener OBS con Windowed Projector abierto')
