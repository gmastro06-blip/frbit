from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tools import monitor_dashboard_smoke as smoke


def _make_route(tmp_path: Path) -> Path:
    route_path = tmp_path / "route.json"
    route_path.write_text(
        json.dumps({
            "_meta": {"start_coord": {"x": 32369, "y": 32241, "z": 7}},
        }),
        encoding="utf-8",
    )
    return route_path


class TestBuildSmokeSession:

    def test_marks_session_running(self, tmp_path: Path):
        route_path = _make_route(tmp_path)

        session = smoke.build_smoke_session(str(route_path), uptime_seconds=7.0)

        assert session.is_running is True
        assert session._stats["start_time"] is not None


class TestSnapshotCheck:

    def test_returns_route_and_position(self, tmp_path: Path):
        route_path = _make_route(tmp_path)
        session = smoke.build_smoke_session(str(route_path), uptime_seconds=9.0)

        result = smoke.run_snapshot_check(session)

        assert result["route"] == "route.json"
        assert result["position"] == {"x": 32369, "y": 32241, "z": 7}
        assert isinstance(result["uptime_seconds"], float)


class TestDashboardCheck:

    def test_validates_health_html_and_websocket(self):
        session = MagicMock()
        session.config.route_file = "routes/test_route.json"
        session.monitor_snapshot.return_value = {
            "route": "test_route.json",
            "position": {"x": 1, "y": 2, "z": 7},
            "uptime_seconds": 5.0,
        }
        fake_server = MagicMock()
        fake_server.http_url = "http://localhost:18090"
        fake_server.ws_url = "ws://localhost:18091"

        health_payload = {
            "status": "ok",
            "subsystems": {
                "route": "test_route.json",
                "position": {"x": 1, "y": 2, "z": 7},
            },
        }
        html_payload = f"<html>{fake_server.ws_url}</html>"

        health_response = MagicMock()
        health_response.__enter__.return_value = health_response
        health_response.read.return_value = json.dumps(health_payload).encode("utf-8")
        html_response = MagicMock()
        html_response.__enter__.return_value = html_response
        html_response.read.return_value = html_payload.encode("utf-8")

        with patch(
            "src.dashboard_server.DashboardServer",
            return_value=fake_server,
        ), patch(
            "tools.monitor_dashboard_smoke.urllib.request.urlopen",
            side_effect=[health_response, html_response],
        ), patch(
            "tools.monitor_dashboard_smoke.run_websocket_check",
            return_value={"type": "init", "route": "test_route.json"},
        ):
            result = smoke.run_dashboard_check(session, require_websocket=True)

        fake_server.start.assert_called_once()
        fake_server.stop.assert_called_once()
        assert result["health_status"] == "ok"
        assert result["ws_injected"] is True
        assert result["websocket"]["type"] == "init"


class TestRunSmoke:

    def test_respects_skip_flags(self):
        with patch("tools.monitor_dashboard_smoke.build_smoke_session", return_value=MagicMock()), \
             patch("tools.monitor_dashboard_smoke.run_snapshot_check", return_value={"route": "x"}), \
             patch("tools.monitor_dashboard_smoke.run_live_check") as run_live_check, \
             patch("tools.monitor_dashboard_smoke.run_monitor_check") as run_monitor_check, \
             patch("tools.monitor_dashboard_smoke.run_dashboard_check") as run_dashboard_check:
            result = smoke.run_smoke(
                route_file="routes/x.json",
                uptime_seconds=5.0,
                live=False,
                confirm_live=False,
                frame_source="",
                frame_window="",
                monitor_idx=None,
                capture_attempts=3,
                require_position=False,
                skip_monitor=True,
                skip_dashboard=True,
                skip_websocket=True,
            )

        run_live_check.assert_not_called()
        run_monitor_check.assert_not_called()
        run_dashboard_check.assert_not_called()
        assert result["snapshot"]["route"] == "x"

    def test_requires_confirmation_for_live_mode(self):
        with pytest.raises(smoke.SmokeFailure, match="confirm-live"):
            smoke.run_smoke(
                route_file="routes/x.json",
                uptime_seconds=5.0,
                live=True,
                confirm_live=False,
                frame_source="",
                frame_window="",
                monitor_idx=None,
                capture_attempts=3,
                require_position=False,
                skip_monitor=True,
                skip_dashboard=True,
                skip_websocket=True,
            )

    def test_records_live_position_source(self):
        session = MagicMock()
        live_summary = {
            "source": "mss",
            "capture_window": {"hwnd": 1, "title": "Tibia"},
            "projector_windows": [],
            "tibia_windows": ["Tibia"],
            "frame": {"width": 20, "height": 10, "mean_brightness": 1.0},
            "position": {"x": 1, "y": 2, "z": 7},
            "position_detected": True,
        }

        with patch("tools.monitor_dashboard_smoke.build_smoke_session", return_value=session), \
             patch("tools.monitor_dashboard_smoke.run_live_check", return_value=live_summary), \
             patch("tools.monitor_dashboard_smoke.run_snapshot_check", return_value={"route": "x", "position": {"x": 1, "y": 2, "z": 7}}):
            result = smoke.run_smoke(
                route_file="routes/x.json",
                uptime_seconds=5.0,
                live=True,
                confirm_live=True,
                frame_source="",
                frame_window="",
                monitor_idx=None,
                capture_attempts=3,
                require_position=False,
                skip_monitor=True,
                skip_dashboard=True,
                skip_websocket=True,
            )

        assert result["live"]["source"] == "mss"
        assert result["snapshot"]["position_source"] == "live"


class TestCaptureLiveFrame:

    def test_falls_back_to_mss_after_wgc_failure(self):
        projector = {"hwnd": 123, "title": "Tibia_Fuente - Proyector en ventana"}
        tibia = {"hwnd": 456, "title": "Tibia - Knight"}
        mss_getter = MagicMock(return_value=np.ones((4, 6, 3), dtype=np.uint8))
        mss_getter.close = MagicMock()

        def build_getter(source: str, **_: object):
            if source == "wgc":
                raise RuntimeError("winsdk missing")
            return mss_getter

        with patch(
            "tools.monitor_dashboard_smoke.discover_live_windows",
            return_value={"projector": [projector], "tibia": [tibia]},
        ), patch(
            "src.frame_capture.build_frame_getter",
            side_effect=build_getter,
        ):
            result = smoke.capture_live_frame(
                frame_source="",
                frame_window="",
                monitor_idx=None,
                capture_attempts=1,
            )

        assert result["source"] == "mss"
        assert result["capture_window"]["title"] == projector["title"]
        assert result["projector_windows"] == [projector["title"]]
        assert result["tibia_windows"] == [tibia["title"]]


class TestRunLiveCheck:

    def test_requires_position_when_requested(self):
        frame = np.ones((8, 12, 3), dtype=np.uint8)

        with patch(
            "tools.monitor_dashboard_smoke.capture_live_frame",
            return_value={
                "frame": frame,
                "source": "mss",
                "capture_window": {"hwnd": 1, "title": "Tibia"},
                "projector_windows": [],
                "tibia_windows": ["Tibia"],
            },
        ), patch(
            "tools.monitor_dashboard_smoke.detect_live_position",
            return_value=None,
        ):
            with pytest.raises(smoke.SmokeFailure, match="resolve a minimap position"):
                smoke.run_live_check(
                    MagicMock(),
                    frame_source="",
                    frame_window="",
                    monitor_idx=None,
                    capture_attempts=1,
                    require_position=True,
                )

    def test_returns_frame_metadata_and_position_state(self):
        frame = np.full((10, 20, 3), 5, dtype=np.uint8)

        with patch(
            "tools.monitor_dashboard_smoke.capture_live_frame",
            return_value={
                "frame": frame,
                "source": "mss",
                "capture_window": {"hwnd": 1, "title": "Tibia"},
                "projector_windows": [],
                "tibia_windows": ["Tibia"],
            },
        ), patch(
            "tools.monitor_dashboard_smoke.detect_live_position",
            return_value={"x": 1, "y": 2, "z": 7},
        ):
            result = smoke.run_live_check(
                MagicMock(),
                frame_source="",
                frame_window="",
                monitor_idx=None,
                capture_attempts=1,
                require_position=False,
            )

        assert result["frame"] == {"width": 20, "height": 10, "mean_brightness": 5.0}
        assert result["position"] == {"x": 1, "y": 2, "z": 7}
        assert result["position_detected"] is True


class TestMain:

    def test_returns_zero_on_success(self):
        with patch("tools.monitor_dashboard_smoke.run_smoke", return_value={"snapshot": {"route": "x"}}):
            assert smoke.main(["--skip-monitor", "--skip-dashboard", "--indent", "0"]) == 0

    def test_returns_one_on_smoke_failure(self):
        with patch("tools.monitor_dashboard_smoke.run_smoke", side_effect=smoke.SmokeFailure("boom")):
            assert smoke.main(["--skip-monitor", "--skip-dashboard"]) == 1