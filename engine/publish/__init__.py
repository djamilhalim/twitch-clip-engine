"""Veröffentlichungs-Ziele. Jedes Ziel hat publish(datei, url, clip, texte) -> dict(ok, id, info)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Texte:
    caption: str        # TikTok-Caption inkl. Hashtags
    yt_titel: str
    yt_beschreibung: str


class Ziel(Protocol):
    name: str
    braucht_url: bool

    def publish(self, datei: Path, url: str | None, clip: dict[str, Any], texte: Texte) -> dict[str, Any]: ...


def baue_ziele(namen: list[str], cfg, tokens) -> list[Ziel]:
    from .buffer import BufferZiel
    from .tiktok import TikTokZiel
    from .youtube import YouTubeZiel

    out: list[Ziel] = []
    for n in namen:
        if n == "buffer_tiktok":
            out.append(BufferZiel("buffer_tiktok", "BUFFER_TIKTOK_CHANNEL_ID", service="tiktok"))
        elif n == "buffer_youtube":
            out.append(BufferZiel("buffer_youtube", "BUFFER_YOUTUBE_CHANNEL_ID", service="youtube",
                                  yt_kategorie=str(cfg.get("posten.youtube_kategorie", 20))))
        elif n == "tiktok_api":
            out.append(TikTokZiel(tokens, modus=cfg.get("posten.tiktok_api_modus", "inbox")))
        elif n == "youtube_api":
            out.append(YouTubeZiel(tokens, kategorie=str(cfg.get("posten.youtube_kategorie", 20))))
        else:
            raise ValueError(f"Unbekanntes Ziel: {n}")
    return out
