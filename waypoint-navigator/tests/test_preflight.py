"""Tests for src/preflight.py — pre-flight production readiness checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.preflight import (
    CheckResult,
    PreflightReport,
    Severity,
    check_combat_config,
    check_dependencies,
    check_his_config,
    check_hpmp_config,
    check_interception_package,
    check_maps_dir,
    check_minimap_config,
    check_route_file,
    check_templates_dir,
    run_preflight,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CheckResult / PreflightReport model tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckResult:
    def test_str_format(self):
        r = CheckResult("my_check", Severity.PASS, "all good")
        assert "[PASS] my_check: all good" == str(r)

    def test_severity_fail(self):
        r = CheckResult("x", Severity.FAIL, "bad")
        assert r.severity == Severity.FAIL


class TestPreflightReport:
    def test_ok_when_all_pass(self):
        rpt = PreflightReport(results=[
            CheckResult("a", Severity.PASS, "ok"),
            CheckResult("b", Severity.WARN, "meh"),
        ])
        assert rpt.ok is True

    def test_not_ok_when_fail(self):
        rpt = PreflightReport(results=[
            CheckResult("a", Severity.PASS, "ok"),
            CheckResult("b", Severity.FAIL, "bad"),
        ])
        assert rpt.ok is False

    def test_failures_property(self):
        rpt = PreflightReport(results=[
            CheckResult("a", Severity.PASS, "ok"),
            CheckResult("b", Severity.FAIL, "bad"),
            CheckResult("c", Severity.FAIL, "worse"),
        ])
        assert len(rpt.failures) == 2

    def test_warnings_property(self):
        rpt = PreflightReport(results=[
            CheckResult("a", Severity.WARN, "w1"),
            CheckResult("b", Severity.WARN, "w2"),
            CheckResult("c", Severity.PASS, "ok"),
        ])
        assert len(rpt.warnings) == 2

    def test_summary_contains_status(self):
        rpt = PreflightReport(results=[
            CheckResult("a", Severity.PASS, "ok"),
        ])
        s = rpt.summary()
        assert "READY" in s
        assert "1/1 pass" in s

    def test_summary_blocked(self):
        rpt = PreflightReport(results=[
            CheckResult("a", Severity.FAIL, "nope"),
        ])
        s = rpt.summary()
        assert "BLOCKED" in s

    def test_empty_report_ok(self):
        assert PreflightReport().ok is True


# ═══════════════════════════════════════════════════════════════════════════════
# Individual check tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckDependencies:
    def test_pass_in_dev_environment(self):
        r = check_dependencies()
        assert r.severity == Severity.PASS

    def test_fail_when_cv2_missing(self):
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
        def fake_import(name, *args, **kwargs):
            if name == "cv2":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=fake_import):
            r = check_dependencies()
        assert r.severity == Severity.FAIL
        assert "cv2" in r.message


class TestCheckInterceptionPackage:
    def test_fail_when_not_installed(self):
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
        def fake_import(name, *args, **kwargs):
            if name == "interception":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=fake_import):
            r = check_interception_package()
        assert r.severity == Severity.FAIL


class TestCheckHpmpConfig:
    def test_pass_with_valid_config(self, tmp_path: Path):
        cfg = {"hp_roi": [12, 28, 769, 12], "mp_roi": [788, 28, 768, 12]}
        p = tmp_path / "hpmp_config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_hpmp_config()
        assert r.severity == Severity.PASS

    def test_fail_missing_file(self, tmp_path: Path):
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_hpmp_config()
        assert r.severity == Severity.FAIL
        assert "no encontrado" in r.message

    def test_fail_missing_hp_roi(self, tmp_path: Path):
        cfg = {"mp_roi": [1, 2, 3, 4]}
        (tmp_path / "hpmp_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_hpmp_config()
        assert r.severity == Severity.FAIL
        assert "hp_roi" in r.message

    def test_fail_invalid_roi_dimensions(self, tmp_path: Path):
        cfg = {"hp_roi": [0, 0, 0, 0], "mp_roi": [1, 2, 3, 4]}
        (tmp_path / "hpmp_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_hpmp_config()
        assert r.severity == Severity.FAIL

    def test_fail_bad_json(self, tmp_path: Path):
        (tmp_path / "hpmp_config.json").write_text("{bad}", encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_hpmp_config()
        assert r.severity == Severity.FAIL
        assert "JSON" in r.message


class TestCheckMinimapConfig:
    def test_pass_with_valid_roi(self, tmp_path: Path):
        cfg = {"roi": [1753, 30, 107, 109]}
        (tmp_path / "minimap_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_minimap_config()
        assert r.severity == Severity.PASS

    def test_warn_missing_file(self, tmp_path: Path):
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_minimap_config()
        assert r.severity == Severity.WARN

    def test_fail_missing_roi_key(self, tmp_path: Path):
        (tmp_path / "minimap_config.json").write_text("{}", encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_minimap_config()
        assert r.severity == Severity.FAIL


class TestCheckCombatConfig:
    def test_pass_with_valid_config(self, tmp_path: Path):
        cfg = {
            "battle_list_roi": [100, 200, 162, 229],
            "spells": [{"vk": 118, "min_mp": 30, "cooldown": 6.0, "label": "exori ico"}],
        }
        p = tmp_path / "combat.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        r = check_combat_config(str(p))
        assert r.severity == Severity.PASS
        assert "1 spells" in r.message

    def test_warn_missing_file(self, tmp_path: Path):
        r = check_combat_config(str(tmp_path / "nope.json"))
        assert r.severity == Severity.WARN

    def test_fail_missing_battle_list_roi(self, tmp_path: Path):
        cfg = {"spells": []}
        p = tmp_path / "combat.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        r = check_combat_config(str(p))
        assert r.severity == Severity.FAIL

    def test_fail_spell_missing_vk(self, tmp_path: Path):
        cfg = {"battle_list_roi": [1, 2, 3, 4], "spells": [{"label": "bad"}]}
        p = tmp_path / "combat.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        r = check_combat_config(str(p))
        assert r.severity == Severity.FAIL
        assert "vk" in r.message


class TestCheckRouteFile:
    def test_pass_valid_route(self, tmp_path: Path):
        route = [
            {"name": "wp1", "x": 32347, "y": 32226, "z": 7},
            {"name": "wp2", "x": 32369, "y": 32241, "z": 7},
        ]
        p = tmp_path / "route.json"
        p.write_text(json.dumps(route), encoding="utf-8")
        r = check_route_file(str(p))
        assert r.severity == Severity.PASS
        assert "2 waypoints" in r.message

    def test_pass_object_format(self, tmp_path: Path):
        route = {"waypoints": [
            {"name": "a", "x": 1, "y": 2, "z": 7},
            {"name": "b", "x": 3, "y": 4, "z": 7},
            {"name": "c", "x": 5, "y": 6, "z": 7},
        ]}
        p = tmp_path / "route.json"
        p.write_text(json.dumps(route), encoding="utf-8")
        r = check_route_file(str(p))
        assert r.severity == Severity.PASS

    def test_warn_empty_route_file(self):
        r = check_route_file("")
        assert r.severity == Severity.WARN

    def test_fail_missing_file(self, tmp_path: Path):
        r = check_route_file(str(tmp_path / "missing.json"))
        assert r.severity == Severity.FAIL

    def test_fail_too_few_waypoints(self, tmp_path: Path):
        p = tmp_path / "tiny.json"
        p.write_text('[{"name":"solo","x":1,"y":2,"z":7}]', encoding="utf-8")
        r = check_route_file(str(p))
        assert r.severity == Severity.FAIL
        assert "1 waypoint" in r.message

    def test_fail_waypoint_missing_x(self, tmp_path: Path):
        route = [{"name": "a", "y": 2, "z": 7}, {"name": "b", "x": 1, "y": 2, "z": 7}]
        p = tmp_path / "route.json"
        p.write_text(json.dumps(route), encoding="utf-8")
        r = check_route_file(str(p))
        assert r.severity == Severity.FAIL
        assert "'x' o 'y'" in r.message

    def test_fallback_to_routes_dir(self, tmp_path: Path):
        routes_dir = tmp_path / "routes"
        routes_dir.mkdir()
        route = [
            {"name": "a", "x": 1, "y": 2, "z": 7},
            {"name": "b", "x": 3, "y": 4, "z": 7},
        ]
        (routes_dir / "test.json").write_text(json.dumps(route), encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_route_file("test.json")
        assert r.severity == Severity.PASS

    def test_pass_unified_script_format(self, tmp_path: Path):
        """Unified JSON with 'script' key should count movement nodes."""
        route = {
            "name": "test",
            "script": [
                {"kind": "action", "action": "check"},
                {"kind": "node", "x": 100, "y": 200, "z": 7},
                {"kind": "node", "x": 110, "y": 210, "z": 7},
                {"kind": "wait", "secs": 2.0},
                {"kind": "node", "x": 120, "y": 220, "z": 7},
                {"kind": "action", "action": "end"},
            ],
        }
        p = tmp_path / "unified.json"
        p.write_text(json.dumps(route), encoding="utf-8")
        r = check_route_file(str(p))
        assert r.severity == Severity.PASS
        assert "3 waypoints" in r.message


class TestCheckTemplatesDir:
    def test_pass_with_pngs(self, tmp_path: Path):
        tpl = tmp_path / "cache" / "templates"
        tpl.mkdir(parents=True)
        (tpl / "monster.png").write_bytes(b"\x89PNG")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_templates_dir()
        assert r.severity == Severity.PASS

    def test_warn_missing_dir(self, tmp_path: Path):
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_templates_dir()
        assert r.severity == Severity.WARN

    def test_warn_empty_dir(self, tmp_path: Path):
        (tmp_path / "cache" / "templates").mkdir(parents=True)
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_templates_dir()
        assert r.severity == Severity.WARN


class TestCheckHISConfig:
    def test_warn_missing_file(self, tmp_path: Path):
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_his_config()
        assert r.severity == Severity.WARN

    def test_pass_with_valid_yaml(self, tmp_path: Path):
        his_dir = tmp_path / "human_input_system"
        his_dir.mkdir()
        (his_dir / "config.yaml").write_text("timing:\n  base_delay: 0.05\n", encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_his_config()
        assert r.severity == Severity.PASS

    def test_fail_invalid_yaml(self, tmp_path: Path):
        his_dir = tmp_path / "human_input_system"
        his_dir.mkdir()
        (his_dir / "config.yaml").write_text(":\n  bad: [unterminated", encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_his_config()
        # Could be FAIL or WARN depending on PyYAML tolerance
        assert r.severity in (Severity.FAIL, Severity.WARN)


class TestCheckMapsDir:
    def test_pass_with_map_files(self, tmp_path: Path):
        maps = tmp_path / "maps"
        maps.mkdir()
        (maps / "7.png").write_bytes(b"\x89PNG")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_maps_dir()
        assert r.severity == Severity.PASS

    def test_warn_missing_dir(self, tmp_path: Path):
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_maps_dir()
        assert r.severity == Severity.WARN


# ═══════════════════════════════════════════════════════════════════════════════
# run_preflight orchestrator tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunPreflight:
    def test_returns_report(self):
        rpt = run_preflight(skip_driver=True)
        assert isinstance(rpt, PreflightReport)
        assert len(rpt.results) > 0

    def test_skip_driver_omits_driver_check(self):
        rpt = run_preflight(skip_driver=True)
        names = [r.name for r in rpt.results]
        assert "interception_driver" not in names

    def test_log_fn_called(self):
        logs: list[str] = []
        run_preflight(skip_driver=True, log_fn=logs.append)
        assert len(logs) > 0
        assert all(isinstance(l, str) for l in logs)

    def test_config_dependent_checks(self, tmp_path: Path):
        route = [
            {"name": "a", "x": 1, "y": 2, "z": 7},
            {"name": "b", "x": 3, "y": 4, "z": 7},
        ]
        rf = tmp_path / "route.json"
        rf.write_text(json.dumps(route), encoding="utf-8")
        cfg = MagicMock()
        cfg.route_file = str(rf)
        cfg.auto_combat = False
        cfg.combat_config_file = ""
        rpt = run_preflight(cfg, skip_driver=True)
        names = [r.name for r in rpt.results]
        assert "route_file" in names

    def test_combat_config_checked_when_auto_combat(self, tmp_path: Path):
        cfg = MagicMock()
        cfg.route_file = ""
        cfg.auto_combat = True
        cfg.combat_config_file = str(tmp_path / "nope.json")
        rpt = run_preflight(cfg, skip_driver=True)
        names = [r.name for r in rpt.results]
        assert "combat_config" in names


# ═══════════════════════════════════════════════════════════════════════════════
# ROI validation edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestROIValidation:
    def test_negative_x(self, tmp_path: Path):
        cfg = {"hp_roi": [-1, 0, 10, 10], "mp_roi": [0, 0, 10, 10]}
        (tmp_path / "hpmp_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_hpmp_config()
        assert r.severity == Severity.FAIL

    def test_three_element_roi(self, tmp_path: Path):
        cfg = {"roi": [1, 2, 3]}
        (tmp_path / "minimap_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_minimap_config()
        assert r.severity == Severity.FAIL

    def test_string_roi_values(self, tmp_path: Path):
        cfg = {"hp_roi": ["a", "b", "c", "d"], "mp_roi": [1, 2, 3, 4]}
        (tmp_path / "hpmp_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with patch("src.preflight._PROJECT_ROOT", tmp_path):
            r = check_hpmp_config()
        assert r.severity == Severity.FAIL


# ═══════════════════════════════════════════════════════════════════════════════
# Session integration — preflight called on start()
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionPreflightIntegration:
    """Verify that BotSession.start() calls run_preflight (non-dry_run)."""

    @patch("src.session.run_preflight")
    @patch("src.session._build_frame_getter")
    def test_preflight_called_on_start(self, mock_bfg, mock_pf, _mock_preflight):
        from src.session import BotSession, SessionConfig

        mock_bfg.return_value = MagicMock(close=MagicMock())
        # Make preflight return a passing report
        mock_pf.return_value = PreflightReport(results=[
            CheckResult("dummy", Severity.PASS, "ok"),
        ])
        cfg = SessionConfig(start_delay=0.0, input_method="postmessage",
                           dry_run=False)
        loader = MagicMock()
        loader.routes = []
        s = BotSession(config=cfg, loader=loader)
        s.start()
        import time; time.sleep(0.1)
        s.stop()
        mock_pf.assert_called_once()

    @patch("src.session.run_preflight")
    @patch("src.session._build_frame_getter")
    def test_preflight_skipped_in_dry_run(self, mock_bfg, mock_pf, _mock_preflight):
        from src.session import BotSession, SessionConfig

        mock_bfg.return_value = MagicMock(close=MagicMock())
        cfg = SessionConfig(start_delay=0.0, input_method="postmessage",
                           dry_run=True)
        loader = MagicMock()
        loader.routes = []
        s = BotSession(config=cfg, loader=loader)
        s.start()
        import time; time.sleep(0.1)
        s.stop()
        mock_pf.assert_not_called()

    @patch("src.session.run_preflight")
    def test_preflight_fail_aborts_session(self, mock_pf, _mock_preflight):
        from src.session import BotSession, SessionConfig

        mock_pf.return_value = PreflightReport(results=[
            CheckResult("test", Severity.FAIL, "broken"),
        ])
        cfg = SessionConfig(start_delay=0.0, input_method="postmessage",
                           dry_run=False)
        s = BotSession(config=cfg)
        with pytest.raises(RuntimeError, match="Preflight FAILED"):
            s.start()
        assert not s.is_running
