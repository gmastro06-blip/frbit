"""
Tests for src/alert_system.py — LogRotator, AlertSystem, AlertConfig
Fully offline: no real webhooks, file writes tested in tmp dirs.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

from src.alert_system import (
    AlertConfig,
    AlertSystem,
    LogRotationConfig,
    LogRotator,
)


# ─────────────────────────────────────────────────────────────────────────────
# AlertConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestAlertConfig:

    def test_defaults(self):
        cfg = AlertConfig()
        assert cfg.enabled is False
        assert cfg.discord_webhook == ""
        assert cfg.telegram_bot_token == ""
        assert cfg.telegram_chat_id == ""
        assert cfg.cooldown_s == 60.0
        assert isinstance(cfg.events, list)

    def test_validate_ok_with_defaults(self):
        cfg = AlertConfig()
        cfg.validate()  # should not raise

    def test_validate_negative_cooldown_raises(self):
        cfg = AlertConfig(cooldown_s=-1)
        with pytest.raises(ValueError, match="cooldown_s"):
            cfg.validate()

    def test_validate_events_not_list_raises(self):
        cfg = AlertConfig()
        cfg.events = "not_a_list"  # type: ignore
        with pytest.raises(ValueError, match="events must be a list"):
            cfg.validate()

    def test_validate_bad_discord_webhook_raises(self):
        cfg = AlertConfig(discord_webhook="https://example.com/bad")
        with pytest.raises(ValueError, match="discord_webhook"):
            cfg.validate()

    def test_validate_valid_discord_webhook_ok(self):
        cfg = AlertConfig(
            discord_webhook="https://discord.com/api/webhooks/123/abc"
        )
        cfg.validate()  # should not raise

    def test_validate_telegram_token_without_chat_id_raises(self):
        cfg = AlertConfig(telegram_bot_token="123:abc")
        with pytest.raises(ValueError, match="telegram_chat_id"):
            cfg.validate()

    def test_validate_telegram_chat_id_without_token_raises(self):
        cfg = AlertConfig(telegram_chat_id="-1001234567")
        with pytest.raises(ValueError, match="telegram_bot_token"):
            cfg.validate()

    def test_post_init_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-12345")
        cfg = AlertConfig()
        assert cfg.discord_webhook == "https://discord.com/api/webhooks/x/y"
        assert cfg.telegram_bot_token == "123:token"
        assert cfg.telegram_chat_id == "-12345"

    def test_explicit_values_not_overwritten_by_env(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/env/url")
        cfg = AlertConfig(discord_webhook="https://discord.com/api/webhooks/explicit/url")
        # Explicit value should be kept
        assert "explicit" in cfg.discord_webhook


# ─────────────────────────────────────────────────────────────────────────────
# LogRotationConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestLogRotationConfig:

    def test_defaults(self):
        cfg = LogRotationConfig()
        assert cfg.log_dir == "logs"
        assert cfg.max_file_size_mb == 10.0
        assert cfg.max_files == 5


# ─────────────────────────────────────────────────────────────────────────────
# LogRotator
# ─────────────────────────────────────────────────────────────────────────────

class TestLogRotator:

    def test_setup_creates_log_dir(self, tmp_path):
        cfg = LogRotationConfig(log_dir=str(tmp_path / "logs"))
        rotator = LogRotator(config=cfg)
        handler = rotator.setup()
        assert Path(cfg.log_dir).is_dir()
        handler.close()

    def test_setup_returns_handler(self, tmp_path):
        cfg = LogRotationConfig(log_dir=str(tmp_path / "logs"))
        rotator = LogRotator(config=cfg)
        handler = rotator.setup()
        assert isinstance(handler, logging.Handler)
        handler.close()

    def test_is_active_after_setup(self, tmp_path):
        cfg = LogRotationConfig(log_dir=str(tmp_path / "logs"))
        rotator = LogRotator(config=cfg)
        assert rotator.is_active is False
        handler = rotator.setup()
        assert rotator.is_active is True
        handler.close()

    def test_teardown_removes_handler(self, tmp_path):
        cfg = LogRotationConfig(log_dir=str(tmp_path / "logs"))
        rotator = LogRotator(config=cfg)
        rotator.setup()
        rotator.teardown()
        assert rotator.is_active is False

    def test_teardown_without_setup_no_crash(self):
        rotator = LogRotator()
        rotator.teardown()  # should not raise

    def test_config_property(self, tmp_path):
        cfg = LogRotationConfig(log_dir=str(tmp_path / "logs"))
        rotator = LogRotator(config=cfg)
        assert rotator.config is cfg

    def test_default_config_used_when_none(self):
        rotator = LogRotator()
        assert isinstance(rotator._config, LogRotationConfig)


# ─────────────────────────────────────────────────────────────────────────────
# AlertSystem
# ─────────────────────────────────────────────────────────────────────────────

class TestAlertSystem:

    def test_construction_disabled(self):
        alerts = AlertSystem()
        assert alerts.config.enabled is False

    def test_send_returns_false_when_disabled(self):
        alerts = AlertSystem(AlertConfig(enabled=False))
        result = alerts.send("e3", {"info": "death"})
        assert result is False

    def test_send_returns_false_without_channels(self):
        alerts = AlertSystem(AlertConfig(enabled=True))
        result = alerts.send("e3", {})
        assert result is False

    def test_send_respects_cooldown(self):
        cfg = AlertConfig(
            enabled=True,
            discord_webhook="https://discord.com/api/webhooks/123/abc",
            cooldown_s=9999.0,
        )
        alerts = AlertSystem(config=cfg)
        # Force a recent timestamp
        alerts._last_alert_ts["e3"] = time.monotonic()
        result = alerts.send("e3", {})
        assert result is False

    def test_send_submits_discord(self):
        cfg = AlertConfig(
            enabled=True,
            discord_webhook="https://discord.com/api/webhooks/123/abc",
            cooldown_s=0.0,
        )
        alerts = AlertSystem(config=cfg)
        with patch.object(alerts._executor, "submit") as mock_submit:
            result = alerts.send("e3", {"info": "death"})
        assert result is True
        assert mock_submit.called

    def test_send_submits_telegram(self):
        cfg = AlertConfig(
            enabled=True,
            telegram_bot_token="123:ValidToken",
            telegram_chat_id="-12345",
            cooldown_s=0.0,
        )
        alerts = AlertSystem(config=cfg)
        with patch.object(alerts._executor, "submit") as mock_submit:
            result = alerts.send("e3", {"info": "death"})
        assert result is True
        assert mock_submit.called

    def test_subscribe_registers_on_bus(self):
        from src.event_bus import EventBus
        bus = EventBus()
        cfg = AlertConfig(enabled=True, events=["e3", "e18"])
        alerts = AlertSystem(config=cfg)
        alerts.subscribe(bus)
        assert bus.subscriber_count("e3") >= 1
        assert bus.subscriber_count("e18") >= 1

    def test_subscribe_none_bus_no_crash(self):
        alerts = AlertSystem()
        alerts.subscribe(None)  # should not raise

    def test_stop_shuts_down_executor(self):
        alerts = AlertSystem()
        alerts.stop()  # should not raise

    def test_total_sent_initial_zero(self):
        alerts = AlertSystem()
        assert alerts.total_sent == 0

    def test_total_failed_initial_zero(self):
        alerts = AlertSystem()
        assert alerts.total_failed == 0

    def test_stats_snapshot_keys(self):
        alerts = AlertSystem()
        snap = alerts.stats_snapshot()
        assert "enabled" in snap
        assert "total_sent" in snap
        assert "total_failed" in snap
        assert "last_alert_ts" in snap


# ─────────────────────────────────────────────────────────────────────────────
# _format_message
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatMessage:

    def test_format_none_data(self):
        alerts = AlertSystem()
        msg = alerts._format_message("e3", None)
        assert "E3" in msg

    def test_format_dict_data(self):
        alerts = AlertSystem()
        msg = alerts._format_message("kill", {"name": "troll", "coord": None})
        assert "KILL" in msg
        assert "troll" in msg

    def test_format_string_data(self):
        alerts = AlertSystem()
        msg = alerts._format_message("death", "player died")
        assert "DEATH" in msg
        assert "player died" in msg

    def test_format_int_data(self):
        alerts = AlertSystem()
        msg = alerts._format_message("test", 42)
        assert "42" in msg


# ─────────────────────────────────────────────────────────────────────────────
# _send_discord (offline, no real HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestSendDiscord:

    def test_invalid_url_prefix_returns_false(self):
        cfg = AlertConfig(discord_webhook="https://example.com/not-discord")
        alerts = AlertSystem(config=cfg)
        with patch("src.alert_system.time.sleep"):  # skip random sleep
            result = alerts._send_discord("test message")
        assert result is False

    def test_http_error_returns_false(self):
        cfg = AlertConfig(
            discord_webhook="https://discord.com/api/webhooks/123/abc"
        )
        alerts = AlertSystem(config=cfg)
        with patch("src.alert_system.time.sleep"):
            with patch("src.alert_system.urllib.request.urlopen") as mock_open:
                mock_open.side_effect = Exception("connection refused")
                result = alerts._send_discord("test message")
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# _send_telegram (offline, no real HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestSendTelegram:

    def test_empty_token_returns_false(self):
        cfg = AlertConfig()
        alerts = AlertSystem(config=cfg)
        with patch("src.alert_system.time.sleep"):
            result = alerts._send_telegram("test")
        assert result is False

    def test_invalid_token_chars_returns_false(self):
        cfg = AlertConfig(
            telegram_bot_token="123:!invalid!",
            telegram_chat_id="-12345",
        )
        alerts = AlertSystem(config=cfg)
        with patch("src.alert_system.time.sleep"):
            result = alerts._send_telegram("test")
        assert result is False

    def test_http_error_returns_false(self):
        cfg = AlertConfig(
            telegram_bot_token="123:ValidToken",
            telegram_chat_id="-12345",
        )
        alerts = AlertSystem(config=cfg)
        with patch("src.alert_system.time.sleep"):
            with patch("src.alert_system.urllib.request.urlopen") as mock_open:
                mock_open.side_effect = Exception("timeout")
                result = alerts._send_telegram("test")
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# _deliver
# ─────────────────────────────────────────────────────────────────────────────

class TestDeliver:

    def test_deliver_discord_ok_increments_sent(self):
        alerts = AlertSystem()
        with patch.object(alerts, "_send_discord", return_value=True):
            alerts._deliver("discord", "hello")
        assert alerts.total_sent == 1

    def test_deliver_discord_fail_increments_failed(self):
        alerts = AlertSystem()
        with patch.object(alerts, "_send_discord", return_value=False):
            alerts._deliver("discord", "hello")
        assert alerts.total_failed == 1

    def test_deliver_telegram_ok_increments_sent(self):
        alerts = AlertSystem()
        with patch.object(alerts, "_send_telegram", return_value=True):
            alerts._deliver("telegram", "hello")
        assert alerts.total_sent == 1

    def test_deliver_telegram_fail_increments_failed(self):
        alerts = AlertSystem()
        with patch.object(alerts, "_send_telegram", return_value=False):
            alerts._deliver("telegram", "hello")
        assert alerts.total_failed == 1
