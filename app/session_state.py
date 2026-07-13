"""Tracks whether the SSO session is alive and pauses monitoring when dead.

When the session expires the monitor fires its alert hooks exactly once
(Phase 2 registers a WhatsApp sender here) and processing stays paused.
Recovery is automatic: the blog client reloads the cookie whenever
storage_state.json changes, so the next cycle after a manual re-auth
succeeds and the monitor marks the session alive again.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)

AlertHook = Callable[[str], None]


@dataclass
class SessionMonitor:
    alive: bool = True
    paused: bool = False
    last_ok_at: datetime | None = None
    expired_at: datetime | None = None
    last_error: str = ""
    alert_hooks: list[AlertHook] = field(default_factory=list)
    _alert_sent: bool = False

    def register_alert_hook(self, hook: AlertHook) -> None:
        self.alert_hooks.append(hook)

    def mark_ok(self) -> None:
        if not self.alive:
            logger.info("session recovered, resuming monitoring")
        self.alive = True
        self.paused = False
        self._alert_sent = False
        self.last_ok_at = datetime.now(timezone.utc)
        self.last_error = ""

    def mark_expired(self, detail: str) -> None:
        self.alive = False
        self.paused = True
        self.expired_at = datetime.now(timezone.utc)
        self.last_error = detail
        logger.error("session expired, monitoring paused: %s", detail)
        if not self._alert_sent:
            message = (
                "Session expired, manual re-login required. "
                f"Time: {self.expired_at.isoformat()} Detail: {detail} "
                "Run scripts/reauth.py to restore the session."
            )
            for hook in self.alert_hooks:
                try:
                    hook(message)
                except Exception:
                    logger.exception("alert hook failed")
            self._alert_sent = True

    def status(self) -> dict:
        return {
            "session_alive": self.alive,
            "paused": self.paused,
            "last_ok_at": self.last_ok_at.isoformat() if self.last_ok_at else None,
            "expired_at": self.expired_at.isoformat() if self.expired_at else None,
            "last_error": self.last_error,
        }
