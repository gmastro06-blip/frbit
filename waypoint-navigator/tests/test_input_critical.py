"""Tests for critical production paths in input_controller.py — Phase A (A5-A6).

These tests cover the ZERO-coverage input paths that are the ONLY way
the bot interacts with the game:

A5: scancode / hybrid key press — _press_scancode, _press_hybrid, _ensure_foreground
A6: Diagonal walking (_press_two_keys), hold_key, click_human, shift_click
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, call

import pytest

from src.input_controller import InputController, Key, WASD_KEYS


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_ctrl(method: str = "postmessage", connected: bool = True) -> InputController:
    """Create an InputController with a fake hwnd and mocked user32."""
    ctrl = InputController(
        target_title="TestTibia",
        key_delay=0.001,
        input_method=method,
        fg_delay=0.001,
        jitter_pct=0.0,
    )
    if connected:
        ctrl._hwnd = 0xDEAD  # fake handle
    return ctrl


# ══════════════════════════════════════════════════════════════════════════════
# A5: press_key dispatches to correct backend
# ══════════════════════════════════════════════════════════════════════════════

class TestPressKeyDispatch:
    """Verify press_key routes to postmessage/scancode/hybrid."""

    @patch('src.input_controller.user32')
    def test_postmessage_sends_keydown_keyup(self, mock_user32: MagicMock):
        """PostMessage mode sends WM_KEYDOWN then WM_KEYUP."""
        mock_user32.IsWindow.return_value = True
        ctrl = _make_ctrl("postmessage")

        with patch('time.sleep'):
            ctrl.press_key(Key.F1, delay=0.001)

        # PostMessageW called at least twice (keydown + keyup)
        calls = mock_user32.PostMessageW.call_args_list
        assert len(calls) >= 2
        # First call: WM_KEYDOWN (0x0100)
        assert calls[0][0][1] == 0x0100
        assert calls[0][0][2] == 0x70  # F1
        # Second call: WM_KEYUP (0x0101)
        assert calls[1][0][1] == 0x0101

    @patch('src.input_controller.user32')
    def test_scancode_calls_keybd_event_without_focus(self, mock_user32: MagicMock):
        """Scancode mode calls _keybd_event without forcing focus."""
        mock_user32.IsWindow.return_value = True
        mock_user32.GetForegroundWindow.return_value = 0xDEAD  # already focused
        mock_user32.MapVirtualKeyW.return_value = 0x3B  # F1 scan
        ctrl = _make_ctrl("scancode")

        with patch('time.sleep'), \
             patch.object(ctrl, '_keybd_event') as mock_kbd:
            ctrl.press_key(Key.F1, delay=0.001)

        # keybd_event called twice: key down + key up
        assert mock_kbd.call_count == 2
        # Down: vk=0x70, scan=0x3B, down=True
        mock_kbd.assert_any_call(0x70, 0x3B, True, False)
        # Up: vk=0x70, scan=0x3B, down=False
        mock_kbd.assert_any_call(0x70, 0x3B, False, False)

    @patch('src.input_controller.user32')
    def test_hybrid_no_focus_switch(self, mock_user32: MagicMock):
        """Hybrid mode presses key without forcing focus (focus forcing removed)."""
        mock_user32.IsWindow.return_value = True
        mock_user32.GetForegroundWindow.return_value = 0xBEEF
        mock_user32.MapVirtualKeyW.return_value = 0x3B  # F1 scan
        mock_user32.IsIconic.return_value = False

        ctrl = _make_ctrl("hybrid")

        with patch('time.sleep'), \
             patch.object(ctrl, '_keybd_event') as mock_kbd:
            ctrl.press_key(Key.F1, delay=0.001)

        # keybd_event called twice for the key
        assert mock_kbd.call_count == 2
        # No SetForegroundWindow — focus forcing removed
        mock_user32.SetForegroundWindow.assert_not_called()

    @patch('src.input_controller.user32')
    def test_not_connected_returns_false(self, mock_user32: MagicMock):
        """Disconnected controller returns False without sending anything."""
        mock_user32.IsWindow.return_value = False
        ctrl = _make_ctrl("postmessage")
        ctrl._hwnd = None

        result = ctrl.press_key(Key.F1)
        assert result is False
        mock_user32.PostMessageW.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# A5b: _press_scancode — extended keys + scancode mapping
# ══════════════════════════════════════════════════════════════════════════════

class TestPressScancode:
    """Cover _press_scancode: arrow keys get KEYEVENTF_EXTENDEDKEY."""

    @patch('src.input_controller.user32')
    def test_arrow_key_sets_extended_flag(self, mock_user32: MagicMock):
        """Arrow keys (0x25-0x28) should set extended=True."""
        mock_user32.GetForegroundWindow.return_value = 0xDEAD
        mock_user32.MapVirtualKeyW.return_value = 0x48  # UP scan
        ctrl = _make_ctrl("scancode")

        with patch('time.sleep'), \
             patch.object(ctrl, '_keybd_event') as mock_kbd:
            ctrl._press_scancode(Key.ARROW_UP, 0.001)

        # extended=True for arrow keys
        mock_kbd.assert_any_call(0x26, 0x48, True, True)
        mock_kbd.assert_any_call(0x26, 0x48, False, True)

    @patch('src.input_controller.user32')
    def test_non_arrow_key_no_extended_flag(self, mock_user32: MagicMock):
        """Non-arrow keys should set extended=False."""
        mock_user32.GetForegroundWindow.return_value = 0xDEAD
        mock_user32.MapVirtualKeyW.return_value = 0x3B  # F1 scan
        ctrl = _make_ctrl("scancode")

        with patch('time.sleep'), \
             patch.object(ctrl, '_keybd_event') as mock_kbd:
            ctrl._press_scancode(Key.F1, 0.001)

        # extended=False for F1
        mock_kbd.assert_any_call(0x70, 0x3B, True, False)


# ══════════════════════════════════════════════════════════════════════════════
# A5c: _ensure_foreground — wait + force focus logic
# ══════════════════════════════════════════════════════════════════════════════

class TestEnsureForeground:
    """Cover _ensure_foreground: wait-then-force logic."""

    @patch('src.input_controller.user32')
    def test_already_focused_returns_true_immediately(self, mock_user32: MagicMock):
        """If Tibia is already foreground, return True without AttachThreadInput."""
        ctrl = _make_ctrl("scancode")
        mock_user32.GetForegroundWindow.return_value = ctrl._hwnd

        result = ctrl._ensure_foreground()

        assert result is True
        mock_user32.AttachThreadInput.assert_not_called()

    @patch('src.input_controller.user32')
    def test_force_focus_after_wait(self, mock_user32: MagicMock):
        """When not focused, eventually forces focus via Alt-key trick (no AttachThreadInput)."""
        ctrl = _make_ctrl("scancode")
        mock_user32.GetForegroundWindow.return_value = 0x1234  # different window
        mock_user32.IsIconic.return_value = False

        with patch('time.sleep'), \
             patch('time.monotonic') as mock_mono, \
             patch('ctypes.windll.user32') as mock_windll_user32:
            # Simulate time progression past the 3s deadline
            mock_mono.side_effect = [0.0, 4.0]  # start, past deadline
            mock_windll_user32.GetMessageExtraInfo.return_value = 0x80040000
            result = ctrl._ensure_foreground()

        # Should NOT call AttachThreadInput (removed for BattlEye safety)
        mock_user32.AttachThreadInput.assert_not_called()
        # Should call SetForegroundWindow via Alt-key trick
        mock_user32.SetForegroundWindow.assert_called_with(ctrl._hwnd)


# ══════════════════════════════════════════════════════════════════════════════
# A6: _press_two_keys — diagonal movement
# ══════════════════════════════════════════════════════════════════════════════

class TestPressTwoKeys:
    """Cover _press_two_keys: simultaneous key press for diagonal movement."""

    @patch('src.input_controller.user32')
    def test_postmessage_both_keys_down_then_up(self, mock_user32: MagicMock):
        """PostMessage mode sends both KEYDOWN, then both KEYUP."""
        mock_user32.IsWindow.return_value = True
        ctrl = _make_ctrl("postmessage")

        with patch('time.sleep'):
            result = ctrl._press_two_keys(Key.ARROW_RIGHT, Key.ARROW_DOWN, 0.001)

        assert result is True
        calls = mock_user32.PostMessageW.call_args_list
        # Should have 4 PostMessage calls: 2 down + 2 up
        assert len(calls) == 4
        # First two: KEYDOWN
        assert calls[0][0][1] == 0x0100  # WM_KEYDOWN
        assert calls[1][0][1] == 0x0100
        # Last two: KEYUP
        assert calls[2][0][1] == 0x0101  # WM_KEYUP
        assert calls[3][0][1] == 0x0101

    @patch('src.input_controller.user32')
    def test_scancode_uses_keybd_event(self, mock_user32: MagicMock):
        """Scancode mode calls _keybd_event for both keys (H1-fix)."""
        mock_user32.IsWindow.return_value = True
        mock_user32.GetForegroundWindow.return_value = 0xDEAD  # already focused
        mock_user32.MapVirtualKeyW.side_effect = lambda vk, _: {0x27: 0x4D, 0x28: 0x50}.get(vk, 0)
        ctrl = _make_ctrl("scancode")

        with patch('time.sleep'), \
             patch.object(ctrl, '_keybd_event') as mock_kb:
            result = ctrl._press_two_keys(Key.ARROW_RIGHT, Key.ARROW_DOWN, 0.001)

        assert result is True
        # 4 calls: key1 down, key2 down, key1 up, key2 up
        assert mock_kb.call_count == 4
        mock_kb.assert_any_call(0x27, 0x4D, True, True)   # RIGHT down, extended
        mock_kb.assert_any_call(0x28, 0x50, True, True)    # DOWN down, extended
        mock_kb.assert_any_call(0x27, 0x4D, False, True)   # RIGHT up
        mock_kb.assert_any_call(0x28, 0x50, False, True)    # DOWN up

    @patch('src.input_controller.user32')
    def test_not_connected_returns_false(self, mock_user32: MagicMock):
        """Disconnected controller returns False."""
        mock_user32.IsWindow.return_value = False
        ctrl = _make_ctrl("postmessage")
        ctrl._hwnd = None

        result = ctrl._press_two_keys(Key.ARROW_RIGHT, Key.ARROW_DOWN, 0.001)
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# A6b: move_to_tile — decomposes into diagonal + straight
# ══════════════════════════════════════════════════════════════════════════════

class TestMoveToTile:
    """Cover move_to_tile: diagonal decomposition."""

    @patch('src.input_controller.user32')
    def test_pure_cardinal_uses_move(self, mock_user32: MagicMock):
        """dx=3, dy=0 → 3 right moves (no diagonal)."""
        mock_user32.IsWindow.return_value = True
        ctrl = _make_ctrl("postmessage")

        with patch('time.sleep'), \
             patch.object(ctrl, 'move', return_value=True) as mock_move, \
             patch.object(ctrl, '_press_two_keys', return_value=True) as mock_diag:
            ctrl.move_to_tile(3, 0)

        mock_move.assert_called_once_with("right", 3, 0.15)
        mock_diag.assert_not_called()

    @patch('src.input_controller.user32')
    def test_diagonal_uses_press_two_keys(self, mock_user32: MagicMock):
        """dx=2, dy=2 → 2 diagonal steps (no straight remainder)."""
        mock_user32.IsWindow.return_value = True
        ctrl = _make_ctrl("postmessage")

        with patch('time.sleep'), \
             patch.object(ctrl, '_press_two_keys', return_value=True) as mock_diag, \
             patch.object(ctrl, 'move', return_value=True) as mock_move:
            ctrl.move_to_tile(2, 2)

        assert mock_diag.call_count == 2
        mock_move.assert_not_called()

    @patch('src.input_controller.user32')
    def test_mixed_diagonal_and_straight(self, mock_user32: MagicMock):
        """dx=3, dy=1 → 1 diagonal + 2 straight right."""
        mock_user32.IsWindow.return_value = True
        ctrl = _make_ctrl("postmessage")

        with patch('time.sleep'), \
             patch.object(ctrl, '_press_two_keys', return_value=True) as mock_diag, \
             patch.object(ctrl, 'move', return_value=True) as mock_move:
            ctrl.move_to_tile(3, 1)

        # 1 diagonal step
        assert mock_diag.call_count == 1
        # 2 straight steps right
        mock_move.assert_called_once_with("right", 2, 0.15)

    @patch('src.input_controller.user32')
    def test_negative_directions(self, mock_user32: MagicMock):
        """dx=-1, dy=-1 → left+up diagonal."""
        mock_user32.IsWindow.return_value = True
        ctrl = _make_ctrl("postmessage")

        with patch('time.sleep'), \
             patch.object(ctrl, '_press_two_keys', return_value=True) as mock_diag:
            ctrl.move_to_tile(-1, -1)

        # 1 diagonal step: left + up
        assert mock_diag.call_count == 1
        vk_left = WASD_KEYS["left"][0]  # arrow key mode
        vk_up = WASD_KEYS["up"][0]
        mock_diag.assert_called_once_with(vk_left, vk_up, 0.15)


# ══════════════════════════════════════════════════════════════════════════════
# A6c: hold_key — scancode and postmessage paths
# ══════════════════════════════════════════════════════════════════════════════

class TestHoldKey:
    """Cover hold_key: key held down for duration seconds."""

    @patch('src.input_controller.user32')
    def test_postmessage_hold(self, mock_user32: MagicMock):
        """PostMessage mode sends KEYDOWN, sleeps, KEYUP."""
        mock_user32.IsWindow.return_value = True
        ctrl = _make_ctrl("postmessage")

        with patch('time.sleep') as mock_sleep:
            result = ctrl.hold_key(Key.F1, duration=0.5)

        assert result is True
        calls = mock_user32.PostMessageW.call_args_list
        assert len(calls) >= 2
        # Verify sleep was called with duration
        mock_sleep.assert_any_call(0.5)

    @patch('src.input_controller.user32')
    def test_scancode_hold(self, mock_user32: MagicMock):
        """Scancode mode calls _keybd_event down, sleeps, _keybd_event up."""
        mock_user32.IsWindow.return_value = True
        mock_user32.GetForegroundWindow.return_value = 0xDEAD
        mock_user32.MapVirtualKeyW.return_value = 0x3B
        ctrl = _make_ctrl("scancode")

        with patch('time.sleep') as mock_sleep, \
             patch.object(ctrl, '_keybd_event') as mock_kbd:
            result = ctrl.hold_key(Key.F1, duration=0.5)

        assert result is True
        assert mock_kbd.call_count == 2
        mock_kbd.assert_any_call(0x70, 0x3B, True, False)
        mock_kbd.assert_any_call(0x70, 0x3B, False, False)
        mock_sleep.assert_any_call(0.5)

    @patch('src.input_controller.user32')
    def test_not_connected_returns_false(self, mock_user32: MagicMock):
        mock_user32.IsWindow.return_value = False
        ctrl = _make_ctrl("postmessage")
        ctrl._hwnd = None

        result = ctrl.hold_key(Key.F1, duration=0.5)
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# A6d: shift_click — depot depositing
# ══════════════════════════════════════════════════════════════════════════════

class TestShiftClick:
    """Cover shift_click: Shift+left-click sequence."""

    @patch('src.input_controller.user32')
    def test_shift_click_sends_correct_sequence(self, mock_user32: MagicMock):
        """Shift down → mouse down → mouse up → shift up."""
        mock_user32.IsWindow.return_value = True
        ctrl = _make_ctrl("postmessage")

        with patch('time.sleep'):
            result = ctrl.shift_click(100, 200)

        assert result is True
        calls = mock_user32.PostMessageW.call_args_list
        # Expect 4 PostMessage calls: shift_down, mouse_down, mouse_up, shift_up
        assert len(calls) >= 4
        # WM_KEYDOWN for shift (0x10)
        assert calls[0][0][1] == 0x0100  # WM_KEYDOWN
        assert calls[0][0][2] == 0x10    # VK_SHIFT
        # WM_LBUTTONDOWN
        assert calls[1][0][1] == 0x0201
        # WM_LBUTTONUP
        assert calls[2][0][1] == 0x0202
        # WM_KEYUP for shift
        assert calls[3][0][1] == 0x0101

    @patch('src.input_controller.user32')
    def test_not_connected_returns_false(self, mock_user32: MagicMock):
        mock_user32.IsWindow.return_value = False
        ctrl = _make_ctrl("postmessage")
        ctrl._hwnd = None

        result = ctrl.shift_click(100, 200)
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# A6e: click_human — Bézier mouse movement
# ══════════════════════════════════════════════════════════════════════════════

class TestClickHuman:
    """Cover click_human: Bézier cursor movement + SendInput click."""

    @patch('src.input_controller.user32')
    def test_click_human_calls_bezier_and_click(self, mock_user32: MagicMock):
        """click_human moves cursor via Bézier, then clicks via SendInput."""
        mock_user32.IsWindow.return_value = True
        mock_user32.ClientToScreen.return_value = 1
        ctrl = _make_ctrl("postmessage")

        with patch('time.sleep'), \
             patch('src.mouse_bezier.move_mouse_to') as mock_bezier, \
             patch.object(ctrl, '_send_input_mouse_click') as mock_click:
            result = ctrl.click_human(100, 200)

        assert result is True

    @patch('src.input_controller.user32')
    def test_click_human_not_connected_returns_false(self, mock_user32: MagicMock):
        mock_user32.IsWindow.return_value = False
        ctrl = _make_ctrl("postmessage")
        ctrl._hwnd = None

        result = ctrl.click_human(100, 200)
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# A5d: Jitter — delay randomisation
# ══════════════════════════════════════════════════════════════════════════════

class TestJitter:
    """Cover _jitter: delay randomisation."""

    def test_no_jitter_returns_base(self):
        ctrl = _make_ctrl()
        ctrl.jitter_pct = 0.0
        assert ctrl._jitter(0.05) == 0.05

    def test_with_jitter_returns_different_value(self):
        ctrl = _make_ctrl()
        ctrl.jitter_pct = 0.3
        results = {ctrl._jitter(0.05) for _ in range(20)}
        # With 30% jitter, we should get varied results
        assert len(results) > 1

    def test_jitter_never_negative(self):
        ctrl = _make_ctrl()
        ctrl.jitter_pct = 0.99
        for _ in range(100):
            assert ctrl._jitter(0.01) > 0


# ══════════════════════════════════════════════════════════════════════════════
# Misc: type_text, set_key_delay, set_move_mode
# ══════════════════════════════════════════════════════════════════════════════

class TestTypeText:
    """Cover type_text: WM_CHAR per character."""

    @patch('src.input_controller.user32')
    def test_sends_wm_char_per_character(self, mock_user32: MagicMock):
        mock_user32.IsWindow.return_value = True
        ctrl = _make_ctrl()

        with patch('time.sleep'):
            result = ctrl.type_text("hi")

        assert result is True
        calls = mock_user32.PostMessageW.call_args_list
        assert len(calls) == 2
        assert calls[0][0][1] == 0x0102  # WM_CHAR
        assert calls[0][0][2] == ord('h')
        assert calls[1][0][2] == ord('i')


class TestSetKeyDelay:
    def test_accepts_valid_delay(self):
        ctrl = _make_ctrl()
        ctrl.set_key_delay(0.1)
        assert ctrl.key_delay == 0.1

    def test_rejects_negative_delay(self):
        ctrl = _make_ctrl()
        with pytest.raises(ValueError):
            ctrl.set_key_delay(-0.1)


class TestSetMoveMode:
    def test_accepts_arrow(self):
        ctrl = _make_ctrl()
        ctrl.set_move_mode("arrow")
        assert ctrl.move_mode == "arrow"

    def test_accepts_wasd(self):
        ctrl = _make_ctrl()
        ctrl.set_move_mode("wasd")
        assert ctrl.move_mode == "wasd"

    def test_rejects_invalid_mode(self):
        ctrl = _make_ctrl()
        with pytest.raises(ValueError):
            ctrl.set_move_mode("gamepad")
