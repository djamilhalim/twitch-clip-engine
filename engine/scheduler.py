"""Posting-Slots: wann darf welches Ziel den nächsten Clip posten?"""
from __future__ import annotations

import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo


def tagesbeginn_ts(zeitzone: str, now: float | None = None) -> float:
    tz = ZoneInfo(zeitzone)
    dt = datetime.fromtimestamp(now or time.time(), tz)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def in_ruhezeit(ruhezeit: list[str] | None, zeitzone: str, now: float | None = None) -> bool:
    if not ruhezeit or len(ruhezeit) != 2:
        return False
    tz = ZoneInfo(zeitzone)
    jetzt = datetime.fromtimestamp(now or time.time(), tz).time()
    von = dtime.fromisoformat(ruhezeit[0])
    bis = dtime.fromisoformat(ruhezeit[1])
    if von <= bis:
        return von <= jetzt < bis
    return jetzt >= von or jetzt < bis  # über Mitternacht


def slot_frei(
    posts_heute: int,
    letzter_post_ts: float,
    max_pro_tag: int,
    min_abstand_min: float,
    ruhezeit: list[str] | None,
    zeitzone: str,
    now: float | None = None,
) -> bool:
    now = now or time.time()
    if in_ruhezeit(ruhezeit, zeitzone, now):
        return False
    if posts_heute >= max_pro_tag:
        return False
    return now - letzter_post_ts >= min_abstand_min * 60


def frische_score(score: float, alter_s: float, halbwertszeit_h: float = 3.0) -> float:
    """Ältere Clips verlieren an Wert – Trends auf TikTok sind schnell."""
    return score * 0.5 ** (alter_s / (halbwertszeit_h * 3600))
