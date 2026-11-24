"""Posting-Texte (Caption, YouTube-Titel, Hashtags) – ohne kostenpflichtige KI."""
from __future__ import annotations

import re
from typing import Any

MAX_TIKTOK = 2200
MAX_YT_TITEL = 100


def _tag(s: str) -> str:
    return "#" + re.sub(r"[^0-9a-zA-ZäöüÄÖÜß]", "", s)


def hashtags(clip: dict[str, Any], basis: list[str], spiel: str | None) -> list[str]:
    tags = [_tag(clip["broadcaster_name"])]
    if spiel and spiel.lower() not in ("just chatting", "irl"):
        tags.append(_tag(spiel))
    for t in basis:
        if t not in tags:
            tags.append(t)
    return tags[:8]


def titel_bereinigen(titel: str, streamer: str) -> str:
    t = (titel or "").strip()
    # Viele Community-Clips heißen einfach wie der Stream oder sind leer
    if len(t) < 4 or t.lower() == streamer.lower():
        return f"{streamer} rastet komplett aus"
    return t[:1].upper() + t[1:]


def caption(clip: dict[str, Any], basis_tags: list[str], spiel: str | None = None) -> str:
    name = clip["broadcaster_name"]
    titel = titel_bereinigen(clip.get("title", ""), name)
    zeilen = [
        f"{titel} 😂",
        f"🎥 {name} live auf twitch.tv/{clip['broadcaster_name'].lower()}",
        " ".join(hashtags(clip, basis_tags, spiel)),
    ]
    return "\n".join(zeilen)[:MAX_TIKTOK]


def youtube_titel(clip: dict[str, Any]) -> str:
    name = clip["broadcaster_name"]
    titel = titel_bereinigen(clip.get("title", ""), name)
    roh = f"{titel} | {name} #shorts"
    if len(roh) > MAX_YT_TITEL:
        roh = f"{titel[: MAX_YT_TITEL - len(name) - 14].rstrip()}… | {name} #shorts"
    return roh
