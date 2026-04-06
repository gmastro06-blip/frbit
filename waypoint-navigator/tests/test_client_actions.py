from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from src.client_actions import (
    close_open_containers,
    quick_loot_target,
    select_context_menu_entry,
    use_hotkey_on_current_tile,
    wait_for_container_closed,
)


def _frame() -> np.ndarray:
    return np.zeros((1080, 1920, 3), dtype=np.uint8)


def test_select_context_menu_entry_prefers_visual_path() -> None:
    ctrl = MagicMock()
    ctrl.click.return_value = True
    result = select_context_menu_entry(
        ctrl=ctrl,
        click_x=100,
        click_y=200,
        entry_index=0,
        fallback_offset_y=18,
        frame_getter=lambda: _frame(),
        detect_context_menu_fn=lambda *_args, **_kwargs: (90, 180, 80, 40),
        find_menu_entry_offset_fn=lambda *_args, **_kwargs: (95, 210),
        sleep_fn=lambda _secs: None,
    )
    assert result.success is True
    assert result.method == "visual"
    assert ctrl.click.call_args_list[1].args[:2] == (95, 210)


def test_select_context_menu_entry_uses_offset_fallback() -> None:
    ctrl = MagicMock()
    ctrl.click.return_value = True
    result = select_context_menu_entry(
        ctrl=ctrl,
        click_x=100,
        click_y=200,
        entry_index=0,
        fallback_offset_y=18,
        frame_getter=lambda: _frame(),
        detect_context_menu_fn=lambda *_args, **_kwargs: None,
        find_menu_entry_offset_fn=lambda *_args, **_kwargs: None,
        sleep_fn=lambda _secs: None,
    )
    assert result.success is True
    assert result.method == "offset"
    assert ctrl.click.call_args_list[1].args[:2] == (105, 218)


def test_quick_loot_target_hotkey_path() -> None:
    ctrl = MagicMock()
    ctrl.move_mouse.return_value = True
    ctrl.key_combo.return_value = True
    result = quick_loot_target(
        ctrl=ctrl,
        click_x=320,
        click_y=240,
        use_hotkey=True,
        quick_loot_menu_offset_y=36,
        sleep_fn=lambda _secs: None,
    )
    assert result.success is True
    assert result.method == "hotkey"
    ctrl.move_mouse.assert_called_once_with(320, 240)
    ctrl.key_combo.assert_called_once_with(0x12, 0x51)


def test_use_hotkey_on_current_tile_presses_and_clicks() -> None:
    ctrl = MagicMock()
    ctrl.press_key.return_value = True
    click_tile = MagicMock()
    result = use_hotkey_on_current_tile(
        ctrl=ctrl,
        hotkey_vk=0x46,
        click_character_tile_fn=click_tile,
        sleep_fn=lambda _secs: None,
    )
    assert result.success is True
    assert result.method == "hotkey_on_tile"
    ctrl.press_key.assert_called_once_with(0x46)
    click_tile.assert_called_once_with()


def test_wait_for_container_closed_returns_true_when_not_visible() -> None:
    result = wait_for_container_closed(
        frame_getter=lambda: _frame(),
        container_roi=(820, 220, 280, 320),
        sleep_fn=lambda _secs: None,
        timeout=0.01,
        poll_interval=0.001,
    )
    assert result is True


def test_close_open_containers_presses_escape() -> None:
    ctrl = MagicMock()
    ctrl.press_key.return_value = True
    result = close_open_containers(
        ctrl=ctrl,
        close_vk=0x1B,
        sleep_fn=lambda _secs: None,
        wait_s=0.25,
    )
    assert result.success is True
    assert result.method == "cancel"
    ctrl.press_key.assert_called_once_with(0x1B)