#!/usr/bin/env python
"""Quick targeting test - verifica detección de Dog en battle list."""
import sys
import os

# Add src to path so imports work
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Change to project directory so config files are found
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import ctypes
from ctypes import wintypes
from typing import TypeAlias

from pathlib import Path

# Import as package
from src.frame_capture import build_frame_getter
from src.combat_manager import CombatConfig, BattleDetector

HwndTitle: TypeAlias = tuple[int, str]


def find_projector_hwnd() -> int | None:
    """Busca la ventana del proyector OBS."""
    user32 = ctypes.windll.user32
    hwnds: list[HwndTitle] = []
    
    def enum_callback(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.lower()
                if "proyector" in title or "projector" in title:
                    hwnds.append((hwnd, buf.value))
        return True
    
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return hwnds[0][0] if hwnds else None

def main() -> int:
    print("=== Test Targeting: Dog ===\n")
    
    # Cargar config
    cfg = CombatConfig.load(Path("combat_config.json"))
    print(f"Battle list ROI: {cfg.battle_list_roi}")
    print(f"Attack VK: {cfg.attack_vk} (Page Down = 34)")
    print(f"Monster priority: {cfg.monster_priority}")
    print(f"Monster filter: {cfg.monster_filter}")
    print()
    
    # Crear BattleDetector (solo detección, sin controller)
    detector = BattleDetector(cfg)
    
    if cfg.ocr_detection:
        print("Modo: OCR detection (lee texto de la battle list)")
    else:
        print(f"Modo: Template matching")
        print(f"Templates cargados: {detector.template_count}")
        if not detector.has_templates:
            print("ERROR: No hay templates de monstruos cargados")
            print(f"  Verifica que existan imágenes en cache/templates/monsters/")
            print(f"  Nombres esperados: dog.png (según monster_filter)")
            return 1
    
    # Buscar ventana del proyector
    hwnd = find_projector_hwnd()
    if not hwnd:
        print("ERROR: No se encontró la ventana del Proyector OBS")
        return 1
    print(f"Proyector OBS encontrado: hwnd={hex(hwnd)}")
    
    # Capturar frame
    print("\nCapturando frame...")
    getter = build_frame_getter("printwindow", hwnd=hwnd)
    frame = getter()
    if frame is None:
        print("ERROR: No se pudo capturar frame")
        return 1
    print(f"Frame capturado: {frame.shape}")
    
    # Detectar monstruos en battle list
    print("\nEscaneando battle list...")
    
    # Guardar debug image del ROI
    import cv2
    h, w = frame.shape[:2]
    rx = w / cfg.ref_width
    ry = h / cfg.ref_height
    x, y, rw, rh = cfg.battle_list_roi
    sx, sy, sw, sh = int(x * rx), int(y * ry), int(rw * rx), int(rh * ry)
    roi = frame[sy:sy+sh, sx:sx+sw].copy()
    cv2.imwrite("output/debug_battle_list_roi.png", roi)
    print(f"ROI guardado en output/debug_battle_list_roi.png ({sw}x{sh})")
    
    # También guardar frame completo con ROI marcado
    debug_frame = frame.copy()
    cv2.rectangle(debug_frame, (sx, sy), (sx+sw, sy+sh), (0, 255, 0), 2)
    cv2.imwrite("output/debug_frame_with_roi.png", debug_frame)
    print(f"Frame con ROI marcado en output/debug_frame_with_roi.png")
    
    if cfg.ocr_detection:
        detections = detector.detect_ocr(frame)
    else:
        detections = detector.detect(frame)
    
    if not detections:
        print("No se detectaron monstruos en la battle list.")
        print("\nVerifica:")
        print("  1. El perro está visible en la battle list de Tibia")
        print("  2. El ROI de battle_list está bien calibrado")
        print("  3. Existe cache/templates/monsters/dog.png")
        return 1
    
    print(f"\nMonstruos detectados: {len(detections)}")
    for i, (fx, fy, conf, name) in enumerate(detections):
        print(f"  [{i+1}] {name} @ ({fx},{fy}) conf={conf:.3f}")
    
    # Verificar si Dog está en la lista
    dog_found = any('dog' in name.lower() for _, _, _, name in detections)
    if dog_found:
        print("\n✓ Dog DETECTADO en battle list")
    else:
        print("\n⚠ Dog NO detectado")
        print("  Los monstruos detectados no incluyen 'dog'")
        print("  Verifica que el template dog.png coincida con el icono en Tibia")
    
    return 0 if dog_found else 1

if __name__ == "__main__":
    sys.exit(main())
