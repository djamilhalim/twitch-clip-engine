"""Optionale Benachrichtigung per Discord-Webhook (kostenlos)."""
from __future__ import annotations

import logging

import requests

from .config import env

log = logging.getLogger(__name__)


def melden(text: str) -> None:
    hook = env("NOTIFY_DISCORD_WEBHOOK")
    if not hook:
        return
    try:
        requests.post(hook, json={"content": text[:1900]}, timeout=10)
    except requests.RequestException as e:
        log.debug("Discord-Meldung fehlgeschlagen: %s", e)
