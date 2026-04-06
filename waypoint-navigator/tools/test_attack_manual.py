"""Test manual de ataque con Page Down."""
import sys
import os
import ctypes
from ctypes import wintypes
import time
from typing import TypeAlias

# Setup paths
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
from src.frame_capture import build_frame_getter
from src.combat_manager import CombatConfig, BattleDetector
from src.input_controller import InputController


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


def main() -> None:
    print("=== Test Manual de Ataque ===\n")
    
    # Cargar config
    cfg = CombatConfig.load(Path("combat_config.json"))
    attack_vk = cfg.attack_vk  # 34 = Page Down
    print(f"Hotkey de ataque: VK={attack_vk} (Page Down)\n")
    
    # Encontrar proyector
    hwnd = find_projector_hwnd()
    if not hwnd:
        print("ERROR: No se encontró ventana del proyector OBS")
        return
    print(f"Proyector OBS: HWND={hex(hwnd)}")
    
    # Inicializar
    grab = build_frame_getter("printwindow", hwnd=hwnd)
    detector = BattleDetector(cfg)
    
    # InputController: usar scancode (SendInput) y conectar a Tibia
    input_ctrl = InputController(target_title="Tibia", input_method="scancode")
    tibia = input_ctrl.find_target()
    if not tibia:
        print("ERROR: No se encontró ventana de Tibia")
        return
    print(f"Tibia encontrada: {tibia.title} (HWND={hex(tibia.hwnd)})")
    
    if not input_ctrl.is_connected():
        print("ERROR: InputController no conectado")
        return
    print(f"Input method: {input_ctrl.input_method}")
    
    # Capturar frame
    frame = grab()
    if frame is None:
        print("ERROR: No se pudo capturar frame")
        return
    
    # Detectar monstruos (OCR mode)
    print("Detectando monstruos en battle list (OCR)...")
    monsters = detector.detect_ocr(frame)
    
    dog = None
    for fx, fy, conf, name in monsters:
        if "spider" in name.lower():
            dog = (fx, fy, conf, name)
            break
    
    if not dog:
        print("ERROR: No se detectó Spider en la battle list")
        print(f"Detectados: {[name for _, _, _, name in monsters]}")
        return
    
    print(f"✓ Spider detectado: {dog[3]} @ conf={dog[2]:.3f}")
    
    # Confirmar ataque
    print("\n" + "="*50)
    print("Se enviará Page Down para atacar al Spider")
    print("="*50)
    
    input("\nPresiona Enter para enviar el ataque...")
    
    # Forzar foco en Tibia con Alt-key trick
    print("\nForzando foco en Tibia...")
    user32 = ctypes.windll.user32
    
    # Alt-key trick: presionar Alt brevemente permite SetForegroundWindow
    VK_MENU = 0x12  # Alt
    user32.keybd_event(VK_MENU, 0, 0, 0)  # Alt down
    user32.keybd_event(VK_MENU, 0, 2, 0)  # Alt up (KEYEVENTF_KEYUP = 2)
    
    # Ahora SetForegroundWindow funcionará
    tibia_hwnd = input_ctrl._hwnd
    if tibia_hwnd is None:
        print("ERROR: InputController no expuso HWND de Tibia")
        return
    user32.SetForegroundWindow(tibia_hwnd)
    
    time.sleep(0.3)  # Pequeña pausa para que el foco se estabilice
    
    fg = user32.GetForegroundWindow()
    if fg == tibia_hwnd:
        print(f"✓ Tibia tiene el foco (fg={hex(fg)})")
    else:
        print(f"WARN: Tibia NO tiene el foco (fg={hex(fg)}, tibia={hex(tibia_hwnd)})")
    
    # Enviar hotkey
    print(f"Enviando VK {attack_vk} (Page Down)...")
    result = input_ctrl.press_key(attack_vk)
    
    if result:
        print("✓ Hotkey enviado exitosamente")
    else:
        print("✗ Fallo al enviar hotkey (is_connected={}, emergency_stopped={})".format(
            input_ctrl.is_connected(), input_ctrl.is_emergency_stopped))
    
    print("\n¿El personaje atacó al Dog? (verifica visualmente)")

if __name__ == "__main__":
    main()
