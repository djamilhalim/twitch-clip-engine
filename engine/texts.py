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


HOOKS = [
    "{s} rastet komplett aus",
    "Was macht {s} da?! 😂",
    "{s} kann nicht mehr",
    "Damit hat {s} nicht gerechnet",
    "{s} eskaliert komplett",
    "Der Chat ist tot 💀",
    "Das war knapp…",
    "Niemand war bereit dafür",
]


def _schwacher_titel(t: str, streamer: str) -> bool:
    woerter = [w for w in re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", t)]
    if len(t) < 5 or t.lower() == streamer.lower() or len(woerter) < 2:
        return True
    buchstaben = sum(ch.isalpha() for ch in t)
    return buchstaben / max(len(t), 1) < 0.5


def titel_bereinigen(titel: str, streamer: str, clip_id: str = "") -> str:
    t = re.sub(r"(.)\1{3,}", r"\1\1\1", (titel or "").strip())  # XDDDDDDD → XDDD
    t = re.sub(r"\s+", " ", t)
    if _schwacher_titel(t, streamer):
        hook = HOOKS[sum(map(ord, clip_id or streamer)) % len(HOOKS)]
        return hook.format(s=streamer)
    return t[:1].upper() + t[1:]


def caption(clip: dict[str, Any], basis_tags: list[str], spiel: str | None = None) -> str:
    name = clip["broadcaster_name"]
    titel = titel_bereinigen(clip.get("title", ""), name, clip.get("id", ""))
    zeilen = [
        f"{titel} 😂",
        f"🎥 {name} live auf twitch.tv/{clip['broadcaster_name'].lower()}",
        " ".join(hashtags(clip, basis_tags, spiel)),
    ]
    return "\n".join(zeilen)[:MAX_TIKTOK]


def youtube_titel(clip: dict[str, Any]) -> str:
    name = clip["broadcaster_name"]
    titel = titel_bereinigen(clip.get("title", ""), name, clip.get("id", ""))
    roh = f"{titel} | {name} #shorts"
    if len(roh) > MAX_YT_TITEL:
        roh = f"{titel[: MAX_YT_TITEL - len(name) - 14].rstrip()}… | {name} #shorts"
    return roh
