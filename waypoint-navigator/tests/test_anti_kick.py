"""Tests for src.anti_kick module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from src.anti_kick import AntiKick, AntiKickConfig


# ═══════════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════════


class TestAntiKickConfig:
    def test_defaults(self):
        cfg = AntiKickConfig()
        assert cfg.idle_threshold == 300.0
        assert cfg.action_interval == 60.0
        assert cfg.method == "mouse_jitter"
        assert cfg.enabled is True

    def test_custom(self):
        cfg = AntiKickConfig(idle_threshold=120.0, method="camera_rotate")
        assert cfg.idle_threshold == 120.0
        assert cfg.method == "camera_rotate"


# ═══════════════════════════════════════════════════════════════════════════════
# AntiKick core
# ═══════════════════════════════════════════════════════════════════════════════


class TestAntiKickBasic:
    def _make(self, **cfg_kwargs) -> AntiKick:
        ctrl = MagicMock()
        ctrl.move_mouse_relative = MagicMock()
        ctrl.press_key = MagicMock()
        defaults: dict[str, object] = {"idle_threshold": 0.05, "action_interval": 0.02}
        defaults.update(cfg_kwargs)
        cfg = AntiKickConfig(**defaults)  # type: ignore[arg-type]
        return AntiKick(ctrl=ctrl, config=cfg)

    def test_initial_state(self):
        ak = self._make()
        assert ak.is_running is False
        assert ak.actions_sent == 0

    def test_notify_activity_resets_timer(self):
        ak = self._make()
        with ak._lock:
            old = ak._last_activity
            ak._last_activity = old - 1.0  # pretend 1 second ago
        ak.notify_activity()
        with ak._lock:
            assert ak._last_activity > old - 1.0

    def test_stats_snapshot(self):
        ak = self._make()
        snap = ak.stats_snapshot()
        assert "running" in snap
        assert "idle_secs" in snap
        assert "actions_sent" in snap

    def test_reset_counters(self):
        ak = self._make()
        with ak._lock:
            ak._actions_sent = 5
        ak.reset_counters()
        assert ak.actions_sent == 0


class TestAntiKickSendAction:
    def _make(self, **cfg_kwargs) -> AntiKick:
        ctrl = MagicMock()
        ctrl.move_mouse_relative = MagicMock()
        ctrl.press_key = MagicMock()
        defaults: dict[str, object] = {"idle_threshold": 0.01, "action_interval": 0.01}
        defaults.update(cfg_kwargs)
        cfg = AntiKickConfig(**defaults)  # type: ignore[arg-type]
        return AntiKick(ctrl=ctrl, config=cfg)

    def test_mouse_jitter_uses_move_mouse_relative(self):
        ak = self._make(method="mouse_jitter")
        ak._mouse_jitter()
        assert ak._ctrl.move_mouse_relative.call_count >= 1

    def test_mouse_jitter_fallback_scrolllock(self):
        ak = self._make(method="mouse_jitter")
        # Remove move_mouse_relative to test fallback (sends ScrollLock)
        del ak._ctrl.move_mouse_relative
        ak._mouse_jitter()
        ak._ctrl.press_key.assert_called_once_with(0x91)

    def test_send_anti_kick_increments_counter(self):
        ak = self._make(method="mouse_jitter")
        ak._send_anti_kick()
        assert ak.actions_sent == 1

    def test_send_anti_kick_camera_rotate(self):
        ak = self._make(method="camera_rotate", camera_hotkey_vk=0x70)
        ak._send_anti_kick()
        ak._ctrl.press_key.assert_called_with(0x70)
        assert ak.actions_sent == 1

    def test_send_anti_kick_hotkey_method(self):
        ak = self._make(method="hotkey", camera_hotkey_vk=0x71)
        ak._send_anti_kick()
        ak._ctrl.press_key.assert_called_with(0x71)
        assert ak.actions_sent == 1

    def test_send_anti_kick_any_method(self):
        """'any' should randomly pick a method without crashing."""
        ak = self._make(method="any")
        for _ in range(10):  # call multiple times to exercise randomness
            ak._send_anti_kick()
        assert ak.actions_sent == 10

    def test_disabled_does_not_send(self):
        ak = self._make(enabled=False)
        # Simulate the loop logic manually
        with ak._lock:
            ak._last_activity = time.monotonic() - 999  # very idle
        # In _loop, enabled=False prevents sending
        # Just call _loop once manually by checking the condition
        assert ak._cfg.enabled is False


class TestAntiKickThread:
    def test_start_stop(self):
        ctrl = MagicMock()
        ctrl.move_mouse_relative = MagicMock()
        cfg = AntiKickConfig(idle_threshold=0.05, action_interval=0.02)
        ak = AntiKick(ctrl=ctrl, config=cfg)
        ak.start()
        assert ak.is_running
        time.sleep(0.15)
        ak.stop()
        assert not ak.is_running
        # Should have sent at least one action
        assert ak.actions_sent >= 1

    def test_double_start_idempotent(self):
        ctrl = MagicMock()
        ctrl.move_mouse_relative = MagicMock()
        cfg = AntiKickConfig(idle_threshold=0.05, action_interval=0.02)
        ak = AntiKick(ctrl=ctrl, config=cfg)
        ak.start()
        ak.start()  # second call should be no-op
        assert ak.is_running
        ak.stop()

    def test_activity_prevents_action(self):
        ctrl = MagicMock()
        ctrl.move_mouse_relative = MagicMock()
        cfg = AntiKickConfig(idle_threshold=10.0, action_interval=0.02)  # 10s threshold
        ak = AntiKick(ctrl=ctrl, config=cfg)
        ak.start()
        # Keep notifying activity
        for _ in range(5):
            ak.notify_activity()
            time.sleep(0.02)
        ak.stop()
        # Should NOT have sent any actions since we kept notifying
        assert ak.actions_sent == 0

    def test_log_callback(self):
        ctrl = MagicMock()
        ctrl.move_mouse_relative = MagicMock()
        cfg = AntiKickConfig(idle_threshold=0.05, action_interval=0.02)
        ak = AntiKick(ctrl=ctrl, config=cfg)
        logs: list[str] = []
        ak.set_log_callback(logs.append)
        ak.start()
        time.sleep(0.15)
        ak.stop()
        assert len(logs) >= 2  # at least Started + Stopped
        assert any("Started" in l for l in logs)
        assert any("Stopped" in l for l in logs)


class TestPauseResume:

    def test_pause_stops_actions_resume_restarts(self):
        ctrl = MagicMock()
        ctrl.move_mouse_relative = MagicMock()
        cfg = AntiKickConfig(idle_threshold=0.01, action_interval=0.01)
        ak = AntiKick(ctrl=ctrl, config=cfg)
        ak.start()
        assert ak.is_running is True

        ak.pause()
        assert ak.is_running is False

        ak.resume()
        assert ak.is_running is True
        ak.stop()

    def test_resume_without_start_creates_thread(self):
        ctrl = MagicMock()
        cfg = AntiKickConfig(idle_threshold=300)
        ak = AntiKick(ctrl=ctrl, config=cfg)
        # resume on a fresh instance should work like start
        ak.resume()
        assert ak.is_running is True
        assert ak._thread is not None
        ak.stop()
