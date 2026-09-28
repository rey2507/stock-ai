"""Paper-Trader — environment configuration.

Loads ``.env`` from the project root once at import time and exposes
typed accessors. Secrets are never logged or echoed into API responses —
``broker_status()`` reports only booleans.

Currently consumed by:
- Angel One SmartAPI integration (Stage: broker connect) —
  ANGEL_API_KEY / ANGEL_CLIENT_CODE / ANGEL_PIN / ANGEL_TOTP_SECRET.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _get(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


class AngelCredentials:
    """Angel One SmartAPI credentials from .env (all-or-nothing)."""

    def __init__(self) -> None:
        self.api_key = _get("ANGEL_API_KEY")
        self.client_code = _get("ANGEL_CLIENT_CODE")
        self.pin = _get("ANGEL_PIN") or _get("ANGEL_PASSWORD")
        self.totp_secret = _get("ANGEL_TOTP_SECRET")

    @property
    def complete(self) -> bool:
        return all([self.api_key, self.client_code, self.pin, self.totp_secret])

    def missing(self) -> list[str]:
        labels = {
            "api_key": "ANGEL_API_KEY",
            "client_code": "ANGEL_CLIENT_CODE",
            "pin": "ANGEL_PIN",
            "totp_secret": "ANGEL_TOTP_SECRET",
        }
        return [env for field, env in labels.items() if not getattr(self, field)]

    def status(self) -> dict:
        """Safe-for-API status: booleans only, no secret values."""
        return {
            "configured": self.complete,
            "missing": self.missing(),
        }


ANGEL = AngelCredentials()
