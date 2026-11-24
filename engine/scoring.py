"""Clip-Auswahl: Welche Community-Clips sind gerade „heiß“?"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .twitch import parse_iso


def alter_minuten(clip: dict[str, Any], now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return max(1.0, (now - parse_iso(clip["created_at"])).total_seconds() / 60)


def score(clip: dict[str, Any], zuschauer: int | None = None, now: datetime | None = None) -> float:
    """
    Views pro Minute seit Erstellung, normiert auf die Größe des Streams.
    Ein Clip mit 300 Views nach 10 min bei 5k Zuschauern ist stärker als 300 Views bei 60k.
    """
    vpm = clip.get("view_count", 0) / alter_minuten(clip, now)
    groesse = math.log10(max(zuschauer or 10_000, 1000))  # 3 … ~5
    return round(vpm / (groesse / 4), 3)


def auswaehlen(
    clips: list[dict[str, Any]],
    cfg_auswahl: dict[str, Any],
    bekannt: set[str],
    zuschauer: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    min_views = cfg_auswahl.get("min_views", 150)
    min_vpm = cfg_auswahl.get("min_views_pro_minute", 4)
    max_d = cfg_auswahl.get("max_dauer_s", 60)
    min_d = cfg_auswahl.get("min_dauer_s", 8)
    block = [w.lower() for w in cfg_auswahl.get("blockwoerter", [])]

    gewaehlt: list[dict[str, Any]] = []
    for c in clips:
        if c["id"] in bekannt:
            continue
        if not (min_d <= c.get("duration", 0) <= max_d):
            continue
        if c.get("view_count", 0) < min_views:
            continue
        titel = (c.get("title") or "").lower()
        if any(w in titel for w in block):
            continue
        vpm = c.get("view_count", 0) / alter_minuten(c, now)
        if vpm < min_vpm:
            continue
        c = dict(c)
        c["_score"] = score(c, zuschauer, now)
        gewaehlt.append(c)

    gewaehlt.sort(key=lambda c: c["_score"], reverse=True)
    return _dedupe_ueberlappend(gewaehlt)


def _dedupe_ueberlappend(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mehrere Zuschauer clippen oft denselben Moment. Behalte pro VOD-Zeitpunkt nur den besten."""
    out: list[dict[str, Any]] = []
    for c in clips:
        off = c.get("vod_offset")
        vid = c.get("video_id")
        doppelt = False
        if off is not None and vid:
            for o in out:
                if o.get("video_id") == vid and o.get("vod_offset") is not None and abs(o["vod_offset"] - off) < 40:
                    doppelt = True
                    break
        else:
            # Fallback: gleicher Ersteller-Zeitpunkt ±60 s
            t = parse_iso(c["created_at"])
            for o in out:
                if o["broadcaster_id"] == c["broadcaster_id"] and abs((parse_iso(o["created_at"]) - t).total_seconds()) < 60:
                    doppelt = True
                    break
        if not doppelt:
            out.append(c)
    return out
