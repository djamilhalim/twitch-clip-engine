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


def _min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def zeitfenster_slot(
    zeitplan: list[dict],
    post_zeiten: list[float],
    min_abstand_min: float,
    zeitzone: str,
    now: float | None = None,
) -> tuple[bool, str]:
    """
    Zeitplan = Liste von Fenstern {von, bis, posts}. Pro Fenster werden die Posts gleichmäßig verteilt:
    erlaubt ist ein Post, wenn im Fenster bisher weniger gepostet wurde als „Anteil der vergangenen
    Fensterzeit × Kontingent“ (+1), und der Mindestabstand eingehalten ist. Außerhalb aller Fenster: Pause.
    """
    now = now or time.time()
    tz = ZoneInfo(zeitzone)
    jetzt = datetime.fromtimestamp(now, tz)
    minute = jetzt.hour * 60 + jetzt.minute
    tag0 = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
    for f in zeitplan:
        von, bis = _min(f["von"]), _min(f["bis"]) if f["bis"] != "24:00" else 1440
        if not (von <= minute < bis):
            continue
        kontingent = int(f.get("posts", 0))
        start_ts = tag0.timestamp() + von * 60
        im_fenster = sum(1 for t in post_zeiten if t >= start_ts)
        if im_fenster >= kontingent:
            return False, f"Fenster {f['von']}–{f['bis']} voll ({im_fenster}/{kontingent})"
        anteil = (minute - von) / max(bis - von, 1)
        if im_fenster > anteil * kontingent:
            return False, f"Fenster {f['von']}–{f['bis']}: verteilt ({im_fenster}/{kontingent})"
        letzter = max(post_zeiten) if post_zeiten else 0
        if now - letzter < min_abstand_min * 60:
            return False, "Mindestabstand"
        return True, f"Fenster {f['von']}–{f['bis']} ({im_fenster + 1}/{kontingent})"
    return False, "außerhalb der Posting-Fenster"
