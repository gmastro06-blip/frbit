"""Test de combat loop sin navegación - solo ataca y lootea donde estás."""
import sys
import os
import ctypes
from ctypes import wintypes
import time
from typing import TypeAlias

src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, src_dir)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
from src.frame_capture import build_frame_getter
from src.combat_manager import CombatConfig, BattleDetector

user32 = ctypes.windll.user32

Detection: TypeAlias = tuple[int, int, float, str]

def find_projector_hwnd() -> int | None:
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

def find_tibia_hwnd() -> int | None:
    hwnds: list[int] = []

    def enum_callback(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if "Tibia" in title and "Proyector" not in title:
                    hwnds.append(hwnd)
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return hwnds[0] if hwnds else None

def focus_tibia(hwnd: int) -> bool:
    VK_MENU = 0x12
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    else:
        print(f"  [WARN] Focus falló: fg={hex(fg)} != tibia={hex(hwnd)}")
        return False

def send_key(vk: int) -> None:
    scan = user32.MapVirtualKeyW(vk, 0)
    # Extended keys: Page Up/Down, Home, End, Arrows, Insert, Delete
    EXTENDED_VKS = {33, 34, 35, 36, 37, 38, 39, 40, 45, 46}
    extended = 1 if vk in EXTENDED_VKS else 0  # KEYEVENTF_EXTENDEDKEY
    print(f"  -> Enviando VK={vk} scan={scan} ext={extended}")
    user32.keybd_event(vk, scan, extended, 0)  # down
    time.sleep(0.08)
    user32.keybd_event(vk, scan, extended | 2, 0)  # up (KEYEVENTF_KEYUP)
    time.sleep(0.05)

def send_combo(mod_vk: int, key_vk: int) -> None:
    scan_mod = user32.MapVirtualKeyW(mod_vk, 0)
    scan_key = user32.MapVirtualKeyW(key_vk, 0)
    user32.keybd_event(mod_vk, scan_mod, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(key_vk, scan_key, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(key_vk, scan_key, 2, 0)
    time.sleep(0.05)
    user32.keybd_event(mod_vk, scan_mod, 2, 0)

def main() -> None:
    print("=== Combat Loop Test (sin navegación) ===\n")
    print("Este script detecta monstruos y envía Page Down para atacar.")
    print("Cuando muera el monstruo, envía Alt+Q para lootear.")
    print("Presiona Ctrl+C para detener.\n")
    
    # Setup
    proj_hwnd = find_projector_hwnd()
    tibia_hwnd = find_tibia_hwnd()
    
    if not proj_hwnd:
        print("ERROR: No se encontró proyector OBS")
        return
    if not tibia_hwnd:
        print("ERROR: No se encontró Tibia")
        return
    
    print(f"Proyector: {hex(proj_hwnd)}")
    print(f"Tibia: {hex(tibia_hwnd)}")
    
    grab = build_frame_getter("printwindow", hwnd=proj_hwnd)
    cfg = CombatConfig.load(Path("combat_config.json"))
    detector = BattleDetector(cfg)
    
    VK_PAGEDOWN = 34
    VK_ALT = 0x12
    VK_Q = 0x51
    
    MONSTER_NAMES = ["rat", "cave rat", "spider", "bug", "rotworm", "snake", "bat"]
    
    last_target: str | None = None
    attack_cooldown = 0.0
    loot_cooldown = 0.0
    
    print("\nIniciando loop... (Ctrl+C para parar)")
    print("-" * 50)
    
    attacking = False  # True mientras hay monstruos
    
    try:
        while True:
            frame = grab()
            if frame is None:
                time.sleep(0.5)
                continue
            
            # Detectar monstruos por OCR
            detections = detector.detect_ocr(frame)
            
            # Debug: mostrar todo lo detectado
            if detections:
                names = [d[3] for d in detections]
                print(f"  OCR detectó: {names}")
            
            # Filtrar solo monstruos reales
            monsters: list[Detection] = []
            for fx, fy, conf, name in detections:
                name_lower = name.lower()
                if any(m in name_lower for m in MONSTER_NAMES):
                    monsters.append((fx, fy, conf, name))
            
            now = time.time()
            
            if monsters:
                # Si no tenemos target o el target murió, seleccionar nuevo
                if not attacking:
                    target = monsters[0]
                    target_name = target[3]
                    print(f"[NEW TARGET] {target_name} - {len(monsters)} monstruos en pantalla")
                    last_target = target_name
                    
                    # Enviar Page Down UNA VEZ para iniciar ataque
                    print(f"  Enfocando Tibia...")
                    if focus_tibia(tibia_hwnd):
                        send_key(VK_PAGEDOWN)
                        print(f"  ✓ Ataque iniciado")
                    attacking = True
                    attack_cooldown = now + 5.0  # No re-atacar por 5s
                
                # Re-enviar Page Down solo si pasaron 5s (por si Tibia perdió target)
                elif now > attack_cooldown:
                    print(f"  [KEEPALIVE] Re-seleccionando target...")
                    if focus_tibia(tibia_hwnd):
                        send_key(VK_PAGEDOWN)
                    attack_cooldown = now + 5.0
            
            elif attacking:
                # Battle list vacía - el target murió, lootear
                print(f"[LOOT] {last_target} murió - enviando Alt+Q")
                if focus_tibia(tibia_hwnd):
                    send_combo(VK_ALT, VK_Q)
                    time.sleep(0.3)
                    send_combo(VK_ALT, VK_Q)
                attacking = False
                last_target = None
                loot_cooldown = now + 1.5
            
            time.sleep(0.4)
            
    except KeyboardInterrupt:
        print("\n\nDetenido por usuario.")

if __name__ == "__main__":
    main()
