"""Test manual de loot con Alt+Q."""
import sys
import os
import ctypes
from ctypes import wintypes
import time

# Setup paths
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from src.input_controller import InputController


def main() -> None:
    print("=== Test Manual de Loot (Alt+Q) ===\n")
    
    # InputController: usar scancode y conectar a Tibia
    input_ctrl = InputController(target_title="Tibia", input_method="scancode")
    tibia = input_ctrl.find_target()
    if not tibia:
        print("ERROR: No se encontró ventana de Tibia")
        return
    print(f"Tibia encontrada: {tibia.title} (HWND={hex(tibia.hwnd)})")
    
    print("\n" + "="*50)
    print("Se enviará Alt+Q para quick loot")
    print("Asegúrate de que haya un cadáver cerca!")
    print("="*50)
    
    input("\nPresiona Enter para enviar Alt+Q...")
    
    # Forzar foco en Tibia con Alt-key trick
    print("\nForzando foco en Tibia...")
    user32 = ctypes.windll.user32
    
    VK_MENU = 0x12  # Alt
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 2, 0)
    
    tibia_hwnd = input_ctrl._hwnd
    if tibia_hwnd is None:
        print("ERROR: InputController no expuso HWND de Tibia")
        return
    user32.SetForegroundWindow(tibia_hwnd)

    time.sleep(0.3)
    
    fg = user32.GetForegroundWindow()
    if fg == tibia_hwnd:
        print(f"✓ Tibia tiene el foco")
    else:
        print(f"WARN: Tibia NO tiene el foco")
    
    # Enviar Alt+Q manualmente con keybd_event (scancode)
    print("Enviando Alt+Q (keybd_event)...")
    VK_Q = 0x51
    VK_MENU = 0x12  # Alt
    
    # Obtener scancodes
    scan_alt = user32.MapVirtualKeyW(VK_MENU, 0)
    scan_q = user32.MapVirtualKeyW(VK_Q, 0)
    
    KEYEVENTF_KEYUP = 0x0002
    
    # Alt down
    user32.keybd_event(VK_MENU, scan_alt, 0, 0)
    time.sleep(0.05)
    
    # Q down
    user32.keybd_event(VK_Q, scan_q, 0, 0)
    time.sleep(0.05)
    
    # Q up
    user32.keybd_event(VK_Q, scan_q, KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)
    
    # Alt up
    user32.keybd_event(VK_MENU, scan_alt, KEYEVENTF_KEYUP, 0)
    
    print("✓ Alt+Q enviado")
    
    print("\n¿Se abrió el quick loot? (verifica visualmente)")

if __name__ == "__main__":
    main()
