"""Clip herunterladen: offiziell (wenn du Editor bist) oder über yt-dlp."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests

from .config import Streamer
from .twitch import Twitch, TwitchError

log = logging.getLogger(__name__)


def _stream_to_file(url: str, ziel: Path) -> Path:
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(ziel, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return ziel


def download_clip(clip: dict[str, Any], streamer: Streamer, tw: Twitch, ordner: Path) -> tuple[Path, bool]:
    """
    Lädt den Clip nach `ordner`. Rückgabe: (Pfad, ist_hochformat).
    Hochformat gibt es nur offiziell bei Editor-Zugang und Dual-Format-Streams.
    """
    ordner.mkdir(parents=True, exist_ok=True)
    ziel = ordner / f"{clip['id']}.mp4"

    if streamer.editor and tw.has_user:
        try:
            urls = tw.clip_download_urls(clip["broadcaster_id"], clip["id"])
            if urls and urls.get("portrait"):
                log.info("Offizieller Download (Hochformat): %s", clip["id"])
                return _stream_to_file(urls["portrait"], ziel), True
            if urls and urls.get("landscape"):
                log.info("Offizieller Download: %s", clip["id"])
                return _stream_to_file(urls["landscape"], ziel), False
        except (TwitchError, requests.RequestException) as e:
            log.warning("Offizieller Download fehlgeschlagen (%s), nutze yt-dlp", e)

    exe = shutil.which("yt-dlp")
    cmd = [exe] if exe else ["python", "-m", "yt_dlp"]
    cmd += [
        "--no-progress",
        "--no-playlist",
        "-f", "best[ext=mp4]/best",
        "-o", str(ziel),
        clip["url"],
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0 or not ziel.exists():
        raise RuntimeError(f"yt-dlp fehlgeschlagen für {clip['url']}: {res.stderr[-500:]}")
    return ziel, False
