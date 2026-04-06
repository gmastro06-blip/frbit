#!/usr/bin/env python3
"""
Diagnóstico completo de captura - pantalla negra
"""

import sys
import cv2
import numpy as np
from pathlib import Path
from typing import Any

ProjectorWindow = dict[str, Any]

def diagnose_black_screen() -> int | None:
    """Diagnostica por qué se captura pantalla negra"""
    print("DIAGNÓSTICO CAPTURA PANTALLA NEGRA")
    print("=" * 50)

    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter
        import mss

        # 1. Test MSS monitors
        with mss.mss() as sct:
            monitors = sct.monitors
            print(f"MSS Monitors detectados: {len(monitors)-1}")
            for i, monitor in enumerate(monitors[1:], 1):
                print(f"  Monitor {i}: {monitor}")

        # 2. Test capturas individuales
        for monitor_idx in [1, 2]:
            print(f"\nTESTING MONITOR {monitor_idx}:")
            try:
                frame_getter = build_frame_getter("mss", monitor_idx=monitor_idx)
                frame = frame_getter()

                if frame is None:
                    print(f"  ERROR: Frame is None")
                    continue

                # Análisis básico
                height, width = frame.shape[:2]
                is_black = np.all(frame == 0)
                mean_brightness = np.mean(frame)
                non_zero_pixels = np.count_nonzero(frame)
                total_pixels = height * width * 3

                print(f"  Tamaño: {width}x{height}")
                print(f"  Es completamente negro: {is_black}")
                print(f"  Brillo promedio: {mean_brightness:.2f}")
                print(f"  Píxeles no negros: {non_zero_pixels}/{total_pixels}")
                print(f"  % contenido: {(non_zero_pixels/total_pixels)*100:.1f}%")

                # Guardar para inspección
                filename = f"captures/debug_monitor_{monitor_idx}_diagnosis.png"
                cv2.imwrite(filename, frame)
                print(f"  Guardado: {filename}")

                # Análisis de contenido
                if is_black:
                    print(f"  → PROBLEMA: Monitor {monitor_idx} está completamente negro")
                elif mean_brightness < 5:
                    print(f"  → PROBLEMA: Monitor {monitor_idx} muy oscuro, posible problema")
                elif mean_brightness > 200:
                    print(f"  → Monitor {monitor_idx} muy brillante (texto/browser?)")
                else:
                    print(f"  → Monitor {monitor_idx} tiene contenido válido")

            except Exception as e:
                print(f"  ERROR capturando Monitor {monitor_idx}: {e}")

        # 3. Test ventana específica del proyector
        print(f"\nTESTING VENTANA PROYECTOR OBS:")
        try:
            import win32gui

            def find_obs_projector() -> ProjectorWindow | None:
                windows: list[ProjectorWindow] = []

                def enum_callback(hwnd: int, results: list[ProjectorWindow]) -> None:
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        if "Tibia_Fuente" in title and "Proyector" in title:
                            rect = win32gui.GetWindowRect(hwnd)
                            results.append({
                                'hwnd': hwnd,
                                'title': title,
                                'rect': rect
                            })
                win32gui.EnumWindows(enum_callback, windows)
                return windows[0] if windows else None

            projector = find_obs_projector()
            if projector:
                print(f"  Proyector encontrado: {projector['title']}")
                print(f"  HWND: {hex(projector['hwnd'])}")
                print(f"  Rect: {projector['rect']}")

                # Test captura por HWND
                frame_getter = build_frame_getter("mss", hwnd=projector['hwnd'])
                frame = frame_getter()

                if frame is not None:
                    mean_brightness = np.mean(frame)
                    is_black = np.all(frame == 0)

                    print(f"  Frame size: {frame.shape[1]}x{frame.shape[0]}")
                    print(f"  Brillo: {mean_brightness:.2f}")
                    print(f"  Es negro: {is_black}")

                    cv2.imwrite("captures/debug_projector_hwnd.png", frame)
                    print(f"  Guardado: captures/debug_projector_hwnd.png")

                    if not is_black and mean_brightness > 20:
                        print(f"  → SOLUCIÓN: Usar hwnd={hex(projector['hwnd'])}")
                        return projector['hwnd']
                    else:
                        print(f"  → Problema: Captura por HWND también está negra")
                else:
                    print(f"  → Error: No se pudo capturar por HWND")
            else:
                print(f"  No se encontró ventana del proyector")

        except ImportError:
            print(f"  Win32gui no disponible")
        except Exception as e:
            print(f"  Error: {e}")

        # 4. Test diferentes backends
        print(f"\nTESTING OTROS BACKENDS:")
        backends = ["wgc", "dxcam"]
        for backend in backends:
            try:
                print(f"  Testing {backend}...")
                frame_getter = build_frame_getter(backend, monitor_idx=2)
                frame = frame_getter()

                if frame is not None:
                    mean_brightness = np.mean(frame)
                    is_black = np.all(frame == 0)
                    print(f"    {backend}: {frame.shape[1]}x{frame.shape[0]}, brillo={mean_brightness:.2f}, negro={is_black}")

                    if not is_black:
                        cv2.imwrite(f"captures/debug_{backend}_monitor2.png", frame)
                        print(f"    → {backend} funciona! Guardado: captures/debug_{backend}_monitor2.png")
                else:
                    print(f"    {backend}: Frame None")
            except Exception as e:
                print(f"    {backend}: Error - {e}")

    except Exception as e:
        print(f"ERROR GENERAL: {e}")
        import traceback
        traceback.print_exc()
        return None

    return None

if __name__ == "__main__":
    result = diagnose_black_screen()

    print(f"\n" + "="*50)
    print("RECOMENDACIONES:")
    print("1. Revisa captures/debug_*.png para ver qué capturó cada método")
    print("2. Si algún archivo muestra Tibia correctamente, usa ese método")
    print("3. Si todo está negro, verifica que OBS proyector esté visible")