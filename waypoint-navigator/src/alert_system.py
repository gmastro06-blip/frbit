"""
Log rotation + alert system — rotate log files and send webhooks.

Fase 7.4 — Log rotation + alerts (Telegram/Discord webhook).
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("wn.as")


# ── Configuration ────────────────────────────────────────────────────────────
@dataclass
class LogRotationConfig:
    """
    Parameters
    ----------
    log_dir : str
        Directory for log files.
    max_file_size_mb : float
        Maximum log file size before rotation.
    max_files : int
        Maximum number of rotated log files to keep.
    log_format : str
        Log message format string.
    """

    log_dir: str = "logs"
    max_file_size_mb: float = 10.0
    max_files: int = 5
    log_format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


@dataclass
class AlertConfig:
    """
    Parameters
    ----------
    enabled : bool
        Master switch for alerts.
    discord_webhook : str
        Discord webhook URL ("" = disabled).
    telegram_bot_token : str
        Telegram bot token ("" = disabled).
    telegram_chat_id : str
        Telegram chat ID.
    events : List[str]
        Event names to trigger alerts for.
    cooldown_s : float
        Minimum seconds between alerts of the same type.
    """

    enabled: bool = False
    discord_webhook: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    events: List[str] = field(
        default_factory=lambda: [
            "e3",
            "e18",
            "e19",
            "e20",
            "e31",   # stuck_abort: walker gave up navigating
            "e32",   # stuck_permanent_stop: max aborts reached
        ]
    )
    cooldown_s: float = 60.0

    def __post_init__(self) -> None:
        """Load secrets from env vars when fields are empty."""
        if not self.discord_webhook:
            self.discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if not self.telegram_bot_token:
            self.telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self.telegram_chat_id:
            self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    def validate(self) -> None:
        """Raise ``ValueError`` on invalid config values."""
        if self.cooldown_s < 0:
            raise ValueError(f"cooldown_s must be >= 0, got {self.cooldown_s}")
        if not isinstance(self.events, list):
            raise ValueError("events must be a list")
        if self.discord_webhook and not self.discord_webhook.startswith(
            "https://discord.com/api/webhooks/"
        ):
            raise ValueError(
                "discord_webhook must start with 'https://discord.com/api/webhooks/'"
            )
        if self.telegram_bot_token and not self.telegram_chat_id:
            raise ValueError(
                "telegram_chat_id is required when telegram_bot_token is set"
            )
        if self.telegram_chat_id and not self.telegram_bot_token:
            raise ValueError(
                "telegram_bot_token is required when telegram_chat_id is set"
            )


# ── Log Rotation ─────────────────────────────────────────────────────────────
class LogRotator:
    """
    Set up rotating log handler for the waypoint logger.

    Usage::

        rotator = LogRotator(LogRotationConfig(log_dir="logs"))
        rotator.setup()
    """

    def __init__(self, config: Optional[LogRotationConfig] = None) -> None:
        self._config = config or LogRotationConfig()
        self._handler: Optional[logging.Handler] = None

    def setup(self, logger_name: str = "wn") -> logging.Handler:
        """
        Configure a RotatingFileHandler on the given logger.

        Returns the created handler.
        """
        from logging.handlers import RotatingFileHandler

        log_dir = Path(self._config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        log_path = log_dir / "app.log"
        max_bytes = int(self._config.max_file_size_mb * 1024 * 1024)

        handler = RotatingFileHandler(
            str(log_path),
            maxBytes=max_bytes,
            backupCount=self._config.max_files,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(self._config.log_format))
        handler.setLevel(logging.DEBUG)

        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)

        self._handler = handler
        _log.debug("Log rotation configured: %s (max %sMB × %d files)",
                    log_path, self._config.max_file_size_mb, self._config.max_files)
        return handler

    def teardown(self, logger_name: str = "wn") -> None:
        """Remove the rotating handler."""
        if self._handler is not None:
            logger = logging.getLogger(logger_name)
            logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None

    @property
    def config(self) -> LogRotationConfig:
        return self._config

    @property
    def is_active(self) -> bool:
        return self._handler is not None


# ── Alert System ─────────────────────────────────────────────────────────────
class AlertSystem:
    """
    Send alerts via Discord/Telegram webhooks on important events.

    Integrates with EventBus: subscribe to events → send webhook.

    Usage::

        alerts = AlertSystem(AlertConfig(discord_webhook="https://..."))
        alerts.subscribe(event_bus)
        # or manually:
        alerts.send("death", {"character": "Hiyoko San"})
    """

    def __init__(self, config: Optional[AlertConfig] = None) -> None:
        self._config = config or AlertConfig()
        self._last_alert_ts: Dict[str, float] = {}
        self._total_sent: int = 0
        self._total_failed: int = 0
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="wn-alert"
        )

    def subscribe(self, event_bus: Any) -> None:
        """Auto-subscribe to configured events on the event bus."""
        if event_bus is None:
            return
        for event in self._config.events:
            event_bus.subscribe(event, lambda data, ev=event: self.send(ev, data))

    def send(self, event: str, data: Any = None) -> bool:
        """
        Enqueue an alert for the given event (non-blocking).

        Returns True if at least one delivery was submitted.
        Actual HTTP calls run in a background thread pool.
        Respects cooldown per event type.
        """
        if not self._config.enabled:
            return False

        now = time.monotonic()
        with self._lock:
            last = self._last_alert_ts.get(event, 0.0)
            if (now - last) < self._config.cooldown_s:
                return False
            # Reserve slot immediately so concurrent events respect cooldown
            self._last_alert_ts[event] = now

        message = self._format_message(event, data)
        submitted = False

        if self._config.discord_webhook:
            self._executor.submit(self._deliver, "discord", message)
            submitted = True

        if self._config.telegram_bot_token and self._config.telegram_chat_id:
            self._executor.submit(self._deliver, "telegram", message)
            submitted = True

        return submitted

    def _deliver(self, channel: str, message: str) -> None:
        """Background worker: send to one channel and update counters."""
        if channel == "discord":
            ok = self._send_discord(message)
        else:
            ok = self._send_telegram(message)
        with self._lock:
            if ok:
                self._total_sent += 1
            else:
                self._total_failed += 1

    def stop(self) -> None:
        """Shut down the alert thread pool (pending deliveries are cancelled)."""
        self._executor.shutdown(wait=False)

    # ── Formatting ───────────────────────────────────────────────────────
    def _format_message(self, event: str, data: Any) -> str:
        """Format alert message."""
        prefix = f"[Alert] {event.upper()}"
        if data is None:
            return prefix
        if isinstance(data, dict):
            details = ", ".join(f"{k}={v}" for k, v in data.items())
            return f"{prefix}: {details}"
        return f"{prefix}: {data}"

    # ── Discord ──────────────────────────────────────────────────────────
    _DISCORD_PREFIX = "https://discord.com/api/webhooks/"

    def _send_discord(self, message: str) -> bool:
        """Send message to Discord webhook (URL must start with Discord API prefix)."""
        time.sleep(random.uniform(1.0, 5.0))
        url = self._config.discord_webhook
        if not url.startswith(self._DISCORD_PREFIX):
            _log.warning("Discord webhook URL rejected (must start with %s)", self._DISCORD_PREFIX)
            return False
        try:
            payload = json.dumps({"content": message}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return bool(resp.status < 400)
        except Exception:
            _log.debug("Discord webhook failed", exc_info=True)
            return False

    # ── Telegram ─────────────────────────────────────────────────────────
    def _send_telegram(self, message: str) -> bool:
        """Send message via Telegram Bot API."""
        time.sleep(random.uniform(1.0, 5.0))
        try:
            token = self._config.telegram_bot_token
            chat_id = self._config.telegram_chat_id
            if not token or not chat_id:
                return False
            # Validate token looks like a Telegram bot token (digits:alphanumeric)
            if not all(c.isalnum() or c in ":_-" for c in token):
                _log.warning("Telegram bot token contains invalid characters")
                return False
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return bool(resp.status < 400)
        except Exception:
            _log.debug("Telegram bot failed", exc_info=True)
            return False

    # ── Properties ───────────────────────────────────────────────────────
    @property
    def config(self) -> AlertConfig:
        return self._config

    @property
    def total_sent(self) -> int:
        with self._lock:
            return self._total_sent

    @property
    def total_failed(self) -> int:
        with self._lock:
            return self._total_failed

    def stats_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._config.enabled,
                "total_sent": self._total_sent,
                "total_failed": self._total_failed,
                "last_alert_ts": dict(self._last_alert_ts),
            }
