from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


def _load_live_test_runner_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "live_test_runner.py"
    spec = importlib.util.spec_from_file_location("live_test_runner", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestIsStartPosCompatible:
    def test_accepts_same_start(self):
        module = _load_live_test_runner_module()
        assert module._is_start_pos_compatible("32368,32234,7", "32368,32234,7") is True

    def test_accepts_small_manhattan_offset(self):
        module = _load_live_test_runner_module()
        assert module._is_start_pos_compatible("32368,32234,7", "32369,32237,7") is True

    def test_rejects_large_manhattan_offset(self):
        module = _load_live_test_runner_module()
        assert module._is_start_pos_compatible("32368,32234,7", "32341,32211,7") is False

    def test_rejects_floor_mismatch(self):
        module = _load_live_test_runner_module()
        assert module._is_start_pos_compatible("32368,32234,7", "32368,32234,6") is False


class TestOverallExitCode:
    def test_returns_zero_when_all_pass(self):
        module = _load_live_test_runner_module()
        results = [{"exit_code": 0}, {"exit_code": 0}]
        assert module._overall_exit_code(results) == 0

    def test_returns_first_nonzero_code(self):
        module = _load_live_test_runner_module()
        results = [{"exit_code": 0}, {"exit_code": 2}, {"exit_code": 1}]
        assert module._overall_exit_code(results) == 2


class TestT1CheckpointHandling:
    def test_post_run_failure_detects_movement_failed_checkpoint(self, tmp_path):
        module = _load_live_test_runner_module()
        checkpoint_path = tmp_path / "session_checkpoint.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "route_file": "routes\\mi_ruta.json",
                    "extra": {
                        "route_mode": "script",
                        "script_stop_reason": "movement_failed",
                        "script_resume_instruction_index": 6,
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.object(module, "CHECKPOINT_PATH", checkpoint_path):
            assert module._t1_post_run_failure("routes/mi_ruta.json") == (
                3,
                "Route did not complete: ScriptExecutor stopped with movement_failed and left a resumable checkpoint at instruction [6]",
            )

    def test_post_run_failure_detects_resolver_degraded_checkpoint(self, tmp_path):
        module = _load_live_test_runner_module()
        checkpoint_path = tmp_path / "session_checkpoint.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "route_file": "routes\\mi_ruta.json",
                    "extra": {
                        "route_mode": "script",
                        "script_stop_reason": "resolver_degraded",
                        "script_resume_instruction_index": 6,
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.object(module, "CHECKPOINT_PATH", checkpoint_path):
            assert module._t1_post_run_failure("routes/mi_ruta.json") == (
                4,
                "Route entered sustained position-resolver loss after blockage and left a resumable checkpoint at instruction [6]",
            )

    def test_post_run_failure_ignores_missing_checkpoint(self, tmp_path):
        module = _load_live_test_runner_module()
        with patch.object(module, "CHECKPOINT_PATH", tmp_path / "session_checkpoint.json"):
            assert module._t1_post_run_failure("routes/mi_ruta.json") is None

    def test_clear_session_checkpoint_deletes_file(self, tmp_path):
        module = _load_live_test_runner_module()
        checkpoint_path = tmp_path / "session_checkpoint.json"
        checkpoint_path.write_text("{}", encoding="utf-8")

        with patch.object(module, "CHECKPOINT_PATH", checkpoint_path):
            module._clear_session_checkpoint()

        assert checkpoint_path.exists() is False