from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from .depot_manager import DepotManager


def wait_for_container(
    manager: "DepotManager",
    max_wait: float,
    *,
    jittered_sleep_fn: Callable[[float], Any],
    time_module: Any,
) -> bool:
    if manager._frame_getter is None:
        time_module.sleep(manager._cfg.open_wait * manager._runtime_random.uniform(0.8, 1.25))
        return True

    roi = manager._cfg.container_roi
    deadline = time_module.monotonic() + max_wait
    while time_module.monotonic() < deadline:
        frame = manager._frame_getter()
        if frame is not None and len(roi) == 4:
            x, y, w, h = roi
            frame_h, frame_w = frame.shape[:2]
            if frame_w != 1920 or frame_h != 1080:
                x = int(x * frame_w / 1920)
                y = int(y * frame_h / 1080)
                w = int(w * frame_w / 1920)
                h = int(h * frame_h / 1080)
            x1 = max(0, min(x, frame_w))
            y1 = max(0, min(y, frame_h))
            x2 = max(0, min(x + w, frame_w))
            y2 = max(0, min(y + h, frame_h))
            if x2 > x1 and y2 > y1:
                crop = frame[y1:y2, x1:x2]
                if manager._detect_open_container(crop):
                    manager._log(f"  [P] Container detectado (t={time_module.time():.1f})")
                    return True
        jittered_sleep_fn(0.15)
    return False


def detect_open_container(
    crop: Optional[np.ndarray],
    *,
    detect_container_window_fn: Callable[..., Any],
    cv2_module: Any,
) -> bool:
    if crop is None or crop.size == 0:
        return False

    result = detect_container_window_fn(
        crop,
        search_roi=(0, 0, crop.shape[1], crop.shape[0]),
        min_width=60,
        min_height=40,
    )
    if result is not None:
        return True

    hsv = cv2_module.cvtColor(crop, cv2_module.COLOR_BGR2HSV)
    lower = np.array([100, 50, 50])
    upper = np.array([150, 255, 255])
    mask = cv2_module.inRange(hsv, lower, upper)
    blue_ratio = float(np.sum(mask > 0)) / max(crop.shape[0] * crop.shape[1], 1)
    return bool(blue_ratio > 0.02)


def deposit_items(
    manager: "DepotManager",
    item_names: Optional[List[str]],
    *,
    jittered_sleep_fn: Callable[[float], Any],
) -> int:
    if manager._cfg.deposit_mode == "loot_all":
        return manager._deposit_loot_all()

    count = 0
    slots = manager._find_backpack_slots()
    if not slots:
        manager._log("  [P] No se encontraron slots de backpack en el frame")
        return 0

    limit = manager._cfg.max_items_per_cycle if manager._cfg.max_items_per_cycle > 0 else len(slots)
    for px, py in slots[:limit]:
        if item_names is not None and not manager._slot_matches(px, py, item_names):
            continue
        manager._log(f"  [P] Shift+click slot ({px},{py})")
        manager._ctrl.shift_click(px, py)
        jittered_sleep_fn(0.15)
        count += 1
    return count


def deposit_loot_all(
    manager: "DepotManager",
    *,
    jittered_sleep_fn: Callable[[float], Any],
) -> int:
    vk = manager._cfg.loot_all_vk
    pos = manager._cfg.loot_all_btn_pos

    if vk:
        manager._log(f"  [P] Loot-all VK={vk:#x}")
        manager._ctrl.press_key(vk)
        jittered_sleep_fn(0.5)
        return 1

    if pos and len(pos) == 2:
        manager._log(f"  [P] Loot-all clic en ({pos[0]},{pos[1]})")
        manager._ctrl.left_click(pos[0], pos[1])
        jittered_sleep_fn(0.5)
        return 1

    manager._log(
        "  [P] ⚠ loot_all_vk y loot_all_btn_pos no configurados — usando fallback shift_click"
    )
    count = 0
    slots = manager._find_backpack_slots()
    limit = manager._cfg.max_items_per_cycle if manager._cfg.max_items_per_cycle > 0 else len(slots)
    for px, py in slots[:limit]:
        manager._ctrl.shift_click(px, py)
        jittered_sleep_fn(0.15)
        count += 1
    return count


def deposit_stow_all(
    manager: "DepotManager",
    player_pos: Optional[Any],
    *,
    detect_context_menu_fn: Callable[..., Any],
    find_menu_entry_offset_fn: Callable[..., Any],
    jittered_sleep_fn: Callable[[float], Any],
    cv2_module: Any,
) -> bool:
    origin = manager._cfg.backpack_slot_origin
    if not origin or len(origin) < 2:
        manager._log("  [P] stow_all: backpack_slot_origin no configurado")
        return False
    if not manager._ctrl.is_connected():
        manager._log("  [P] ⚠ stow_all: InputController no conectado")
        return False

    frame_init = manager._frame_getter() if manager._frame_getter else None
    dynamic_slot = None
    if frame_init is not None and manager._cfg.stow_container_index >= 0:
        dynamic_slot = manager._find_container_first_slot(frame_init)

    if dynamic_slot is not None:
        px, py = dynamic_slot
        manager._log(f"  [P] stow_all: container detectado dinámicamente → slot ({px},{py})")
    else:
        px, py = int(origin[0]), int(origin[1])
        manager._log(f"  [P] stow_all: usando backpack_slot_origin → slot ({px},{py})")

    entry_idx = manager._cfg.stow_all_item_entry_index
    max_iters = manager._cfg.max_items_per_cycle if manager._cfg.max_items_per_cycle > 0 else 20
    stowed = 0
    menu_miss_tolerance = 2
    consecutive_menu_misses = 0
    manager._log(f"  [P] stow_all: entry_idx={entry_idx}, max={max_iters}")

    for index in range(max_iters):
        frame_before = manager._frame_getter() if manager._frame_getter else None
        if not manager._ctrl.right_click(px, py):
            manager._log("  [P] ⚠ stow_all: right_click falló")
            break
        jittered_sleep_fn(0.4)

        frame_after = manager._frame_getter() if manager._frame_getter else None
        menu_roi = None
        if frame_before is not None and frame_after is not None:
            menu_roi = detect_context_menu_fn(frame_before, frame_after, px, py)

        if menu_roi is None:
            consecutive_menu_misses += 1
            if consecutive_menu_misses < menu_miss_tolerance:
                manager._log(
                    f"  [P] stow_all: sin menú en iter={index + 1} (miss {consecutive_menu_misses}/{menu_miss_tolerance}) — reintentando"
                )
                jittered_sleep_fn(0.3)
                continue
            manager._log(
                f"  [P] stow_all: sin menú {menu_miss_tolerance} veces seguidas en iter={index + 1} — slot vacío, terminando"
            )
            if index == 0 and frame_before is not None and frame_after is not None:
                try:
                    debug_dir = Path(__file__).resolve().parent.parent / "output" / "debug"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    cv2_module.imwrite(str(debug_dir / "stow_before.png"), frame_before)
                    cv2_module.imwrite(str(debug_dir / "stow_after.png"), frame_after)
                    diff = cv2_module.absdiff(frame_after, frame_before)
                    cv2_module.imwrite(str(debug_dir / "stow_diff.png"), diff)
                    manager._log(f"  [P] debug frames guardados en {debug_dir}")
                except Exception as exc:
                    manager._log(f"  [P] debug save error: {exc}")
            break

        consecutive_menu_misses = 0
        entry = None
        if frame_after is not None:
            entry = find_menu_entry_offset_fn(frame_after, menu_roi, entry_index=entry_idx)
        if entry is None:
            manager._log(
                f"  [P] ⚠ stow_all: 'Stow All' (idx={entry_idx}) no encontrado en iter={index + 1}. Prueba ajustar stow_all_item_entry_index en depot_config.json"
            )
            manager._ctrl.left_click(px - 80, py)
            break

        click_x, click_y = entry
        manager._log(f"  [P] stow_all iter={index + 1}: 'Stow All' → ({click_x},{click_y})")
        if not manager._ctrl.left_click(click_x, click_y):
            manager._log("  [P] ⚠ stow_all: left_click falló")
            break
        jittered_sleep_fn(0.6)
        stowed += 1

    if stowed > 0:
        manager._log(f"  [P] stow_all: {stowed} tipo(s) de ítem depositados ✓")
        return True
    manager._log("  [P] ⚠ stow_all: ningún ítem depositado")
    return False


def find_container_first_slot(
    manager: "DepotManager",
    frame: np.ndarray,
    *,
    cv2_module: Any,
) -> Optional[Tuple[int, int]]:
    if frame is None or frame.size == 0:
        return None

    frame_h, frame_w = frame.shape[:2]
    origin = manager._cfg.backpack_slot_origin
    scan_x = max(0, int(origin[0]) - 80) if len(origin) >= 1 else int(frame_w * 0.88)
    panel = frame[:, scan_x:]
    panel_h, panel_w = panel.shape[:2]
    gray = cv2_module.cvtColor(panel, cv2_module.COLOR_BGR2GRAY)
    y_start = max(0, manager._cfg.stow_panel_y_start)

    headers: list[dict[str, int]] = []
    in_band = False
    band_start = 0
    for y in range(y_start, panel_h):
        mean_value = float(gray[y].mean())
        in_range = 44.0 <= mean_value <= 68.0
        if in_range and not in_band:
            in_band = True
            band_start = y
        elif not in_range and in_band:
            in_band = False
            band_height = y - band_start
            if 8 <= band_height <= 35:
                headers.append({"y_top": band_start, "y_bottom": y})
    if in_band:
        band_height = panel_h - band_start
        if 8 <= band_height <= 35:
            headers.append({"y_top": band_start, "y_bottom": panel_h})

    manager._log(f"  [P] container scan: {len(headers)} titulo(s) detectado(s) → {headers}")
    index = manager._cfg.stow_container_index
    if index < 0 or index >= len(headers):
        manager._log(f"  [P] ⚠ container_index={index}: solo {len(headers)} container(s) visible(s)")
        return None

    header = headers[index]
    slot_y = header["y_bottom"] + manager._cfg.container_slot_y_offset
    slot_x = int(origin[0]) if len(origin) >= 1 else scan_x + panel_w // 2
    manager._log(
        f"  [P] container [{index}]: header y={header['y_top']}..{header['y_bottom']} → primer slot ({slot_x}, {slot_y})"
    )
    return (slot_x, slot_y)


def find_backpack_slots(
    manager: "DepotManager",
    *,
    detect_container_window_fn: Callable[..., Any],
    scale_offset_x_fn: Callable[..., int],
    scale_offset_y_fn: Callable[..., int],
) -> List[Tuple[int, int]]:
    frame = manager._frame_getter() if manager._frame_getter else None
    if frame is not None:
        roi = manager._cfg.container_roi
        search = tuple(roi) if len(roi) == 4 else None
        container_window = detect_container_window_fn(frame, search)  # type: ignore[arg-type]
        if container_window is not None:
            cx, cy, _cwidth, _cheight = container_window
            origin_x = cx + 12
            origin_y = cy + 24
            manager._log(f"  [P] container visual → origin ({origin_x},{origin_y})")
            slots: List[Tuple[int, int]] = []
            for row in range(manager._cfg.backpack_slot_rows):
                for col in range(manager._cfg.backpack_slot_cols):
                    slots.append(
                        (
                            origin_x + col * manager._cfg.backpack_slot_spacing,
                            origin_y + row * manager._cfg.backpack_slot_spacing,
                        )
                    )
            return slots

    if len(manager._cfg.backpack_slot_origin) < 2:
        manager._log("  [P] ⚠ backpack_slot_origin inválido — usando defaults escalados")
        if frame is not None:
            origin_x = scale_offset_x_fn(834, frame)
            origin_y = scale_offset_y_fn(270, frame)
        else:
            origin_x, origin_y = 834, 270
    else:
        origin_x = manager._cfg.backpack_slot_origin[0]
        origin_y = manager._cfg.backpack_slot_origin[1]

    fallback_slots: List[Tuple[int, int]] = []
    for row in range(manager._cfg.backpack_slot_rows):
        for col in range(manager._cfg.backpack_slot_cols):
            fallback_slots.append(
                (
                    origin_x + col * manager._cfg.backpack_slot_spacing,
                    origin_y + row * manager._cfg.backpack_slot_spacing,
                )
            )
    return fallback_slots