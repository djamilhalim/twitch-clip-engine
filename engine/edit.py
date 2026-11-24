"""Schnitt mit ffmpeg: 16:9-Clip → 9:16 (1080×1920) mit Layout, Untertiteln, Titel, Credit, Lautheit."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from . import captions as cap
from .config import Streamer

log = logging.getLogger(__name__)


def probe(video: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout
    j = json.loads(out)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in j["streams"])
    return {
        "w": int(v["width"]),
        "h": int(v["height"]),
        "dauer": float(j["format"].get("duration") or v.get("duration") or 0),
        "audio": has_audio,
    }


def _layout_filter(layout: str, W: int, H: int, facecam: list[float] | None, hochformat: bool) -> str:
    """Erzeugt die Video-Filterkette, Ausgang heißt [v0]."""
    if hochformat:
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[v0]")

    if layout == "crop":
        return f"[0:v]crop=ih*9/16:ih,scale={W}:{H},setsar=1[v0]"

    if layout == "split" and facecam and len(facecam) == 4:
        fx, fy, fw, fh = facecam
        cam_h = int(H * 0.333) // 2 * 2
        game_h = H - cam_h
        return (
            "[0:v]split=2[a][b];"
            f"[a]crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},"
            f"scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[cam];"
            f"[b]crop=ih*{W}/{game_h}:ih,scale={W}:{game_h}[game];"
            "[cam][game]vstack=inputs=2,setsar=1[v0]"
        )

    # Standard „blur“: unscharfer Hintergrund, Vordergrund leicht gezoomt in der Mitte
    fg_h = int(H * 0.40) // 2 * 2  # ≈ 768 px hoch → seitlich ~20 % beschnitten, wirkt größer
    return (
        "[0:v]split=2[bg][fg];"
        f"[bg]scale=-2:{H},crop={W}:{H},boxblur=24:2,eq=brightness=-0.10:saturation=1.15[bgb];"
        f"[fg]scale=-2:{fg_h},crop=min(iw\\,{W}):{fg_h}[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]"
    )


def schneiden(
    quelle: Path,
    ziel: Path,
    streamer: Streamer,
    titel: str | None,
    schnitt_cfg: dict[str, Any],
    hochformat: bool = False,
) -> dict[str, Any]:
    W = int(schnitt_cfg.get("breite", 1080))
    H = int(schnitt_cfg.get("hoehe", 1920))
    fps = int(schnitt_cfg.get("fps", 30))
    info = probe(quelle)
    layout = streamer.layout if streamer.layout in ("blur", "split", "crop") else "blur"
    if layout == "split" and not streamer.facecam:
        log.warning("%s: Layout split ohne facecam-Koordinaten → nutze blur", streamer.login)
        layout = "blur"

    woerter: list[cap.Wort] = []
    if schnitt_cfg.get("untertitel", True) and info["audio"]:
        woerter = cap.transkribieren(quelle, schnitt_cfg.get("whisper_modell", "small"))

    ass = ziel.with_suffix(".ass")
    cap.ass_datei(
        ass, W, H, info["dauer"], woerter,
        titel=titel if schnitt_cfg.get("titel_oben", True) else None,
        credit=f"twitch.tv/{streamer.login}" if schnitt_cfg.get("credit", True) else None,
        schrift=schnitt_cfg.get("schrift", "Montserrat"),
        layout="hochformat" if hochformat else layout,
    )

    ass_pfad = str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = _layout_filter(layout, W, H, streamer.facecam, hochformat)
    vf += f";[v0]subtitles='{ass_pfad}',fps={fps},format=yuv420p[v]"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(quelle)]
    if not info["audio"]:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-filter_complex", vf, "-map", "[v]"]
    if info["audio"]:
        cmd += ["-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"]
    else:
        cmd += ["-map", "1:a", "-shortest"]
    cmd += [
        "-c:v", "libx264", "-preset", schnitt_cfg.get("preset", "veryfast"), "-crf", str(schnitt_cfg.get("crf", 21)),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(ziel),
    ]
    log.debug("ffmpeg: %s", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg fehlgeschlagen: {res.stderr[-800:]}")
    transkript = " ".join(w.text for w in woerter)
    return {"datei": str(ziel), "dauer": info["dauer"], "layout": layout, "transkript": transkript}
