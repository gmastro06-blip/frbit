"""Tests for src.death_handler and src.reconnect_handler."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.death_handler import DeathHandler, DeathConfig
from src.reconnect_handler import ReconnectHandler, ReconnectConfig


# ═══════════════════════════════════════════════════════════════════════════════
# DeathHandler tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeathConfig:
    def test_defaults(self):
        cfg = DeathConfig()
        assert cfg.check_interval == 1.0
        assert cfg.respawn_delay == 3.0
        assert cfg.re_equip_hotkeys == []
        assert cfg.max_deaths == 0
        assert cfg.navigation_timeout_s == 45.0

    def test_custom(self):
        cfg = DeathConfig(respawn_delay=5.0, max_deaths=3, re_equip_hotkeys=[0x70, 0x71])
        assert cfg.respawn_delay == 5.0
        assert cfg.max_deaths == 3
        assert len(cfg.re_equip_hotkeys) == 2


class TestDeathHandlerDetection:
    def _make_handler(self) -> DeathHandler:
        ctrl = MagicMock()
        ctrl.press_key = MagicMock(return_value=True)
        return DeathHandler(ctrl=ctrl, config=DeathConfig())

    def test_heuristic_no_death_on_normal_frame(self):
        dh = self._make_handler()
        # Normal game frame — mostly green/brown
        frame = np.full((100, 100, 3), [50, 120, 60], dtype=np.uint8)
        assert dh.check_now(frame) is False

    def test_heuristic_detects_red_death_screen(self):
        dh = self._make_handler()
        # Simulate red-tinted death overlay
        frame = np.full((100, 100, 3), [20, 20, 180], dtype=np.uint8)  # BGR: low B, low G, high R
        assert dh.check_now(frame) is True

    def test_heuristic_partial_red_not_enough(self):
        dh = self._make_handler()
        frame = np.full((100, 100, 3), [50, 50, 50], dtype=np.uint8)
        # Make only 5% red — below threshold
        frame[:5, :, :] = [10, 10, 200]
        assert dh.check_now(frame) is False


class TestDeathHandlerTick:
    def _make_handler(self, **cfg_kwargs) -> DeathHandler:
        ctrl = MagicMock()
        ctrl.press_key = MagicMock(return_value=True)
        # Use confirm_frames=1 in tests so single _tick() calls trigger death
        cfg_kwargs.setdefault("confirm_frames", 1)
        cfg = DeathConfig(respawn_delay=0.01, **cfg_kwargs)
        return DeathHandler(ctrl=ctrl, config=cfg)

    def test_tick_does_nothing_without_frame_getter(self):
        dh = self._make_handler()
        dh._tick()  # should not raise
        assert dh.deaths == 0

    def test_tick_detects_death_and_presses_ok(self):
        dh = self._make_handler()
        death_frame = np.full((100, 100, 3), [10, 10, 180], dtype=np.uint8)
        dh.set_frame_getter(lambda: death_frame)
        bus = MagicMock()
        dh.set_event_bus(bus)

        dh._tick()

        assert dh.deaths == 1
        # Should have pressed Enter (OK)
        dh._ctrl.press_key.assert_called()
        # EventBus should have e9 and e12 events
        events = [c[0][0] for c in bus.emit.call_args_list]
        assert "e9" in events

    def test_max_deaths_stops_handler(self):
        dh = self._make_handler(max_deaths=1)
        death_frame = np.full((100, 100, 3), [10, 10, 180], dtype=np.uint8)
        dh.set_frame_getter(lambda: death_frame)

        dh._running = True
        dh._tick()

        assert dh.deaths == 1
        assert dh._running is False  # should have stopped

    def test_on_death_callback(self):
        dh = self._make_handler()
        death_frame = np.full((100, 100, 3), [10, 10, 180], dtype=np.uint8)
        dh.set_frame_getter(lambda: death_frame)

        cb = MagicMock()
        dh.set_on_death(cb)
        dh._tick()
        cb.assert_called_once()

    def test_re_equip_hotkeys(self):
        dh = self._make_handler(re_equip_hotkeys=[0x70, 0x71])
        death_frame = np.full((100, 100, 3), [10, 10, 180], dtype=np.uint8)
        dh.set_frame_getter(lambda: death_frame)

        dh._tick()

        # Should have pressed: OK + F1 + F2
        assert dh._ctrl.press_key.call_count >= 3

    def test_tick_preserves_pending_position_when_confirm_frame_loses_it(self):
        dh = self._make_handler(confirm_frames=2)
        death_frame = np.full((100, 100, 3), [10, 10, 180], dtype=np.uint8)
        dh.set_frame_getter(lambda: death_frame)
        first_position = MagicMock(name="first_position")
        positions = [first_position, None]
        dh.set_position_getter(lambda: positions.pop(0))

        dh._tick()
        dh._tick()

        assert dh.death_position is first_position

    def test_unknown_death_position_does_not_pause_twice(self):
        dh = self._make_handler()
        death_frame = np.full((100, 100, 3), [10, 10, 180], dtype=np.uint8)
        dh.set_frame_getter(lambda: death_frame)
        pause_fn = MagicMock()
        resume_fn = MagicMock()
        dh.set_pause_fn(pause_fn)
        dh.set_resume_fn(resume_fn)
        dh.set_position_getter(lambda: None)
        dh.set_navigate_fn(MagicMock(return_value=True))

        dh._tick()

        pause_fn.assert_called_once()
        resume_fn.assert_called_once()


class TestDeathHandlerThread:
    def test_start_stop(self):
        ctrl = MagicMock()
        dh = DeathHandler(ctrl=ctrl, config=DeathConfig(check_interval=0.05))
        dh.start()
        assert dh.is_running
        time.sleep(0.1)
        dh.stop()
        assert not dh.is_running

    def test_stats_snapshot(self):
        ctrl = MagicMock()
        dh = DeathHandler(ctrl=ctrl)
        snap = dh.stats_snapshot()
        assert "running" in snap
        assert "deaths" in snap


# ═══════════════════════════════════════════════════════════════════════════════
# ReconnectHandler tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestReconnectConfig:
    def test_defaults(self):
        cfg = ReconnectConfig()
        assert cfg.check_interval == 5.0
        assert cfg.reconnect_delay == 10.0
        assert cfg.max_retries == 5
        assert cfg.server_save_hours == [10.0]

    def test_custom(self):
        cfg = ReconnectConfig(max_retries=3, server_save_hours=[10.0, 22.0])
        assert cfg.max_retries == 3
        assert len(cfg.server_save_hours) == 2


class TestReconnectHandlerDetection:
    def _make_handler(self) -> ReconnectHandler:
        ctrl = MagicMock()
        return ReconnectHandler(ctrl=ctrl, config=ReconnectConfig())

    def test_heuristic_normal_frame_not_login(self):
        rh = self._make_handler()
        frame = np.full((100, 100, 3), [100, 120, 100], dtype=np.uint8)
        assert rh.check_now(frame) is False

    def test_heuristic_detects_dark_login_screen(self):
        rh = self._make_handler()
        # Mostly black frame (login screen)
        frame = np.full((100, 100, 3), [5, 5, 5], dtype=np.uint8)
        assert rh.check_now(frame) is True

    def test_heuristic_partial_dark_not_enough(self):
        rh = self._make_handler()
        frame = np.full((100, 100, 3), [100, 100, 100], dtype=np.uint8)
        # Only 10% dark
        frame[:10, :, :] = [5, 5, 5]
        assert rh.check_now(frame) is False


class TestReconnectHandlerTick:
    def _make_handler(self, login_success: bool = True, **cfg_kwargs) -> ReconnectHandler:
        ctrl = MagicMock()
        defaults: dict[str, object] = {"reconnect_delay": 0.01, "retry_delay": 0.01, "check_interval": 0.05}
        defaults.update(cfg_kwargs)
        cfg = ReconnectConfig(**defaults)  # type: ignore[arg-type]
        rh = ReconnectHandler(ctrl=ctrl, config=cfg)
        rh.set_login_fn(lambda: login_success)
        return rh

    def test_tick_does_nothing_without_frame_getter(self):
        rh = self._make_handler()
        rh._tick()
        assert rh.disconnects == 0

    def test_tick_does_nothing_on_connected(self):
        rh = self._make_handler()
        frame = np.full((100, 100, 3), [100, 120, 100], dtype=np.uint8)
        rh.set_frame_getter(lambda: frame)

        rh._tick()
        assert rh.disconnects == 0

    def test_tick_detects_disconnect_and_reconnects(self):
        rh = self._make_handler(login_success=True)
        dark_frame = np.full((100, 100, 3), [5, 5, 5], dtype=np.uint8)
        rh.set_frame_getter(lambda: dark_frame)
        bus = MagicMock()
        rh.set_event_bus(bus)
        rh._running = True

        # H8-fix: need 3 consecutive login-screen frames to confirm disconnect
        rh._tick()
        rh._tick()
        rh._tick()

        assert rh.disconnects == 1
        assert rh.reconnects == 1
        events = [c[0][0] for c in bus.emit.call_args_list]
        assert "e21" in events
        assert "e22" in events

    def test_tick_login_failure_emits_reconnect_failed(self):
        ctrl = MagicMock()
        cfg = ReconnectConfig(reconnect_delay=0.0, retry_delay=0.0, max_retries=2, max_backoff=0.0)
        rh = ReconnectHandler(ctrl=ctrl, config=cfg)
        dark_frame = np.full((100, 100, 3), [5, 5, 5], dtype=np.uint8)
        rh.set_frame_getter(lambda: dark_frame)
        bus = MagicMock()
        rh.set_event_bus(bus)
        rh._running = True

        # login fails then stops handler after initial batch + 1 backoff attempt
        call_count = 0
        def failing_login() -> bool:
            nonlocal call_count
            call_count += 1
            if call_count > 2:  # stop after initial batch
                rh._running = False
            return False
        rh.set_login_fn(failing_login)

        # H8-fix: need 3 consecutive login-screen frames to confirm disconnect
        rh._tick()
        rh._tick()
        rh._tick()

        assert rh.disconnects == 1
        assert rh.reconnects == 0
        events = [c[0][0] for c in bus.emit.call_args_list]
        assert "e23" in events

    def test_no_login_fn_fails_gracefully(self):
        ctrl = MagicMock()
        cfg = ReconnectConfig(reconnect_delay=0.01, retry_delay=0.01, max_retries=1, max_backoff=0.0)
        rh = ReconnectHandler(ctrl=ctrl, config=cfg)
        dark_frame = np.full((100, 100, 3), [5, 5, 5], dtype=np.uint8)
        rh.set_frame_getter(lambda: dark_frame)
        rh._running = True

        # No login_fn → all attempts fail → stop after first backoff
        orig_attempt = rh._attempt_login
        call_count = 0
        def counted_attempt() -> bool:
            nonlocal call_count
            call_count += 1
            if call_count > 1:  # stop after initial attempt
                rh._running = False
            return orig_attempt()
        rh._attempt_login = counted_attempt  # type: ignore[assignment]

        # H8-fix: need 3 consecutive login-screen frames to confirm disconnect
        rh._tick()
        rh._tick()
        rh._tick()
        assert rh.disconnects == 1
        assert rh.reconnects == 0


class TestReconnectHandlerThread:
    def test_start_stop(self):
        ctrl = MagicMock()
        rh = ReconnectHandler(ctrl=ctrl, config=ReconnectConfig(check_interval=0.05))
        rh.start()
        assert rh.is_running
        time.sleep(0.1)
        rh.stop()
        assert not rh.is_running

    def test_stats_snapshot(self):
        ctrl = MagicMock()
        rh = ReconnectHandler(ctrl=ctrl)
        snap = rh.stats_snapshot()
        assert "disconnects" in snap
        assert "reconnects" in snap
        assert "consecutive_failures" in snap


class TestServerSaveWindow:
    def test_outside_window(self):
        ctrl = MagicMock()
        cfg = ReconnectConfig(server_save_hours=[3.0])  # 03:00
        rh = ReconnectHandler(ctrl=ctrl, config=cfg)
        # Mock current time far from 03:00
        import datetime
        with patch("src.reconnect_handler.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 12, 0)
            assert rh._is_server_save_window() is False

    def test_inside_window(self):
        ctrl = MagicMock()
        cfg = ReconnectConfig(server_save_hours=[10.0])  # 10:00
        rh = ReconnectHandler(ctrl=ctrl, config=cfg)
        import datetime
        with patch("src.reconnect_handler.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 10, 1)
            assert rh._is_server_save_window() is True


# ═══════════════════════════════════════════════════════════════════════════════
# T3 — Pause / resume subsystems during death & reconnect recovery
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeathHandlerPauseResume:
    """Verify that DeathHandler calls pause_fn before recovery and resume_fn after."""

    def _make_handler(self, *, confirm_frames: int = 1, respawn_delay: float = 0.0) -> DeathHandler:
        ctrl = MagicMock()
        ctrl.press_key = MagicMock(return_value=True)
        cfg = DeathConfig(confirm_frames=confirm_frames, respawn_delay=respawn_delay)
        return DeathHandler(ctrl=ctrl, config=cfg)

    def _red_frame(self) -> np.ndarray:
        """Mostly-red frame that triggers heuristic death detection."""
        return np.full((100, 100, 3), [20, 20, 180], dtype=np.uint8)

    def _normal_frame(self) -> np.ndarray:
        return np.full((100, 100, 3), [50, 120, 60], dtype=np.uint8)

    def test_pause_called_before_recovery(self):
        dh = self._make_handler()
        pause_fn = MagicMock()
        resume_fn = MagicMock()
        dh.set_pause_fn(pause_fn)
        dh.set_resume_fn(resume_fn)

        # Frame getter returns red (death), then normal (dismissed)
        frames = iter([self._red_frame(), self._normal_frame()])
        dh.set_frame_getter(lambda: next(frames, self._normal_frame()))

        dh._tick()  # triggers death → recovery

        pause_fn.assert_called_once()
        resume_fn.assert_called_once()

    def test_pause_order_is_pause_then_resume(self):
        dh = self._make_handler()
        call_order: list = []
        dh.set_pause_fn(lambda: call_order.append("pause"))
        dh.set_resume_fn(lambda: call_order.append("resume"))

        frames = iter([self._red_frame(), self._normal_frame()])
        dh.set_frame_getter(lambda: next(frames, self._normal_frame()))

        dh._tick()

        assert call_order == ["pause", "resume"]

    def test_no_pause_fn_no_error(self):
        """Handler works fine without pause/resume callbacks (backwards compat)."""
        dh = self._make_handler()
        frames = iter([self._red_frame(), self._normal_frame()])
        dh.set_frame_getter(lambda: next(frames, self._normal_frame()))

        dh._tick()  # should not raise
        assert dh.deaths == 1

    def test_max_deaths_skips_recovery_and_resume(self):
        """When max_deaths reached, no recovery happens so no pause/resume."""
        ctrl = MagicMock()
        cfg = DeathConfig(max_deaths=1, confirm_frames=1, respawn_delay=0.0)
        dh = DeathHandler(ctrl=ctrl, config=cfg)
        pause_fn = MagicMock()
        resume_fn = MagicMock()
        dh.set_pause_fn(pause_fn)
        dh.set_resume_fn(resume_fn)

        dh.set_frame_getter(lambda: self._red_frame())
        dh._tick()  # death #1 → max reached → stop, no recovery

        pause_fn.assert_not_called()
        resume_fn.assert_not_called()

    def test_pause_fn_exception_does_not_prevent_recovery(self):
        dh = self._make_handler()
        dh.set_pause_fn(MagicMock(side_effect=RuntimeError("boom")))
        resume_fn = MagicMock()
        dh.set_resume_fn(resume_fn)

        frames = iter([self._red_frame(), self._normal_frame()])
        dh.set_frame_getter(lambda: next(frames, self._normal_frame()))

        dh._tick()  # should not raise despite pause_fn error
        assert dh.deaths == 1
        resume_fn.assert_called_once()


class TestReconnectHandlerPauseResume:
    """Verify that ReconnectHandler calls pause_fn/resume_fn around reconnection."""

    def _dark_frame(self) -> np.ndarray:
        """Dark login screen frame that triggers heuristic detection."""
        return np.full((100, 100, 3), [10, 10, 10], dtype=np.uint8)

    def _normal_frame(self) -> np.ndarray:
        return np.full((100, 100, 3), [50, 120, 60], dtype=np.uint8)

    def _make_handler(self, *, max_retries: int = 3) -> ReconnectHandler:
        ctrl = MagicMock()
        cfg = ReconnectConfig(
            max_retries=max_retries,
            reconnect_delay=0.0,
            retry_delay=0.0,
        )
        rh = ReconnectHandler(ctrl=ctrl, config=cfg)
        rh._running = True  # simulate started state (avoids background thread)
        return rh

    def test_pause_on_disconnect_resume_on_reconnect(self):
        rh = self._make_handler()
        pause_fn = MagicMock()
        resume_fn = MagicMock()
        rh.set_pause_fn(pause_fn)
        rh.set_resume_fn(resume_fn)
        rh.set_login_fn(lambda: True)

        # Need 3 consecutive dark frames to confirm disconnect
        dark = self._dark_frame()
        call_count = 0
        def frame_getter():
            nonlocal call_count
            call_count += 1
            return dark
        rh.set_frame_getter(frame_getter)

        # Pump 3 ticks to confirm disconnect (streak=1, streak=2, streak=3→act)
        rh._tick()  # streak=1
        rh._tick()  # streak=2
        rh._tick()  # streak=3 → disconnect confirmed → pause → reconnect → resume

        pause_fn.assert_called_once()
        resume_fn.assert_called_once()

    def test_pause_order_is_pause_then_resume(self):
        rh = self._make_handler()
        call_order: list = []
        rh.set_pause_fn(lambda: call_order.append("pause"))
        rh.set_resume_fn(lambda: call_order.append("resume"))
        rh.set_login_fn(lambda: True)

        dark = self._dark_frame()
        rh.set_frame_getter(lambda: dark)

        rh._tick()
        rh._tick()
        rh._tick()

        assert call_order == ["pause", "resume"]

    def test_resume_called_after_stop_when_retries_fail(self):
        """T4: With infinite retry, resume happens when handler is stopped."""
        rh = self._make_handler(max_retries=2)
        pause_fn = MagicMock()
        resume_fn = MagicMock()
        rh.set_pause_fn(pause_fn)
        rh.set_resume_fn(resume_fn)
        # login fails initially, then stop is called (simulated by _running=False)
        call_count = 0
        def failing_login() -> bool:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:  # after initial 2 + 1 backoff attempt
                rh._running = False
            return False
        rh.set_login_fn(failing_login)

        dark = self._dark_frame()
        rh.set_frame_getter(lambda: dark)

        rh._tick()
        rh._tick()
        rh._tick()  # disconnect confirmed → initial retries fail → backoff → stop

        pause_fn.assert_called_once()
        resume_fn.assert_called_once()

    def test_no_pause_fn_no_error(self):
        """Backwards compatibility — no callbacks set."""
        rh = self._make_handler()
        rh.set_login_fn(lambda: True)
        rh.set_frame_getter(lambda: self._dark_frame())

        rh._tick()
        rh._tick()
        rh._tick()  # should not raise

        assert rh.reconnects == 1

    def test_pause_fn_exception_does_not_prevent_reconnect(self):
        rh = self._make_handler()
        rh.set_pause_fn(MagicMock(side_effect=RuntimeError("boom")))
        resume_fn = MagicMock()
        rh.set_resume_fn(resume_fn)
        rh.set_login_fn(lambda: True)
        rh.set_frame_getter(lambda: self._dark_frame())

        rh._tick()
        rh._tick()
        rh._tick()

        assert rh.reconnects == 1
        resume_fn.assert_called_once()


class TestReconnectBackoff:
    """T4: Verify exponential backoff infinite retry."""

    def _dark_frame(self) -> np.ndarray:
        return np.full((100, 100, 3), [10, 10, 10], dtype=np.uint8)

    def test_backoff_succeeds_after_initial_failures(self):
        ctrl = MagicMock()
        cfg = ReconnectConfig(
            max_retries=2, reconnect_delay=0.0, retry_delay=0.0, max_backoff=0.0,
        )
        rh = ReconnectHandler(ctrl=ctrl, config=cfg)
        rh._running = True

        # Fail first 3 attempts, succeed on 4th (initial 2 + backoff 1 + backoff 2)
        attempt_count = 0
        def login_fn() -> bool:
            nonlocal attempt_count
            attempt_count += 1
            return attempt_count >= 4
        rh.set_login_fn(login_fn)
        rh.set_frame_getter(lambda: self._dark_frame())

        rh._tick()
        rh._tick()
        rh._tick()  # confirm disconnect → 2 initial fail → backoff attempts

        assert rh.reconnects == 1
        assert attempt_count == 4

    def test_reconnect_failed_event_emitted_once(self):
        ctrl = MagicMock()
        bus = MagicMock()
        cfg = ReconnectConfig(
            max_retries=1, reconnect_delay=0.0, retry_delay=0.0, max_backoff=0.0,
        )
        rh = ReconnectHandler(ctrl=ctrl, config=cfg)
        rh._running = True
        rh.set_event_bus(bus)

        # Fail once (initial), then succeed on backoff attempt
        call_count = 0
        def login_fn() -> bool:
            nonlocal call_count
            call_count += 1
            return call_count >= 2  # succeed on 2nd attempt (first backoff)
        rh.set_login_fn(login_fn)
        rh.set_frame_getter(lambda: self._dark_frame())

        rh._tick()
        rh._tick()
        rh._tick()

        # reconnect_failed should have been emitted once after initial batch
        failed_calls = [c for c in bus.emit.call_args_list if c[0][0] == "e23"]
        assert len(failed_calls) == 1
        # And then reconnected should also have been emitted
        success_calls = [c for c in bus.emit.call_args_list if c[0][0] == "e22"]
        assert len(success_calls) == 1

    def test_max_backoff_config(self):
        cfg = ReconnectConfig(max_backoff=120.0)
        assert cfg.max_backoff == 120.0

    def test_default_max_backoff(self):
        cfg = ReconnectConfig()
        assert cfg.max_backoff == 300.0
