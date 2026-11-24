"""Welche Streamer werden beobachtet – feste Liste und/oder größte deutsche Live-Streams."""
from __future__ import annotations

import logging
from typing import Any

from .config import Config, Streamer
from .twitch import Twitch

log = logging.getLogger(__name__)


def live_ziele(cfg: Config, tw: Twitch) -> list[tuple[Streamer, dict[str, Any]]]:
    """Gibt (Streamer, Stream-Info) für alle aktuell live beobachteten Kanäle zurück."""
    logins = [s.login for s in cfg.streamer]
    live: dict[str, dict[str, Any]] = {}
    if logins:
        for st in tw.live_streams(logins):
            live[st["user_login"].lower()] = st

    if cfg.get("quelle.modus") == "top_de":
        anzahl = int(cfg.get("quelle.top_de_anzahl", 15))
        min_z = int(cfg.get("quelle.top_de_min_zuschauer", 3000))
        zusaetzlich = 0
        for st in tw.top_streams("de", count=max(anzahl, 20)):
            login = st["user_login"].lower()
            if st.get("viewer_count", 0) < min_z or login in live or login in cfg.ausgeschlossen:
                continue
            if zusaetzlich >= anzahl:
                break
            zusaetzlich += 1
            live[login] = st
            if not cfg.streamer_by_login(login):
                cfg.streamer.append(Streamer(login=login, entdeckt=True))
                log.info("Entdeckt: %s (%s Zuschauer)", login, st.get("viewer_count"))

    out = []
    for login, st in live.items():
        s = cfg.streamer_by_login(login)
        if s:
            out.append((s, st))
    out.sort(key=lambda x: x[1].get("viewer_count", 0), reverse=True)
    return out
