"""Untertitel: faster-whisper (lokal, kostenlos) → ASS mit Wort-Hervorhebung, plus Titel/Credit-Overlays."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Wort:
    start: float
    ende: float
    text: str


@lru_cache(maxsize=2)
def _modell(name: str):
    from faster_whisper import WhisperModel  # import erst bei Bedarf (groß)

    log.info("Lade Whisper-Modell %s …", name)
    return WhisperModel(name, device="cpu", compute_type="int8")


def _audio_16k(video: Path):
    """Audio per ffmpeg als 16-kHz-Mono-Float-Array (umgeht PyAV-Versionsprobleme in faster-whisper)."""
    import subprocess

    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(video), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def transkribieren(video: Path, modell: str = "small", sprache: str = "de") -> list[Wort]:
    try:
        m = _modell(modell)
        segments, _info = m.transcribe(
            _audio_16k(video), language=sprache, word_timestamps=True, vad_filter=True, beam_size=1
        )
        woerter: list[Wort] = []
        for seg in segments:
            for w in seg.words or []:
                t = w.word.strip()
                if t:
                    woerter.append(Wort(float(w.start), float(w.end), t))
        return woerter
    except ImportError:
        log.warning("faster-whisper nicht installiert – keine Untertitel")
    except Exception as e:  # z. B. Modell-Download fehlgeschlagen: Clip trotzdem ohne Untertitel schneiden
        log.warning("Untertitel fehlgeschlagen (%s) – Clip ohne Untertitel", e)
    return []


# ── ASS-Erzeugung ───────────────────────────────────────────────────────

def _ts(sek: float) -> str:
    sek = max(0.0, sek)
    h = int(sek // 3600)
    m = int(sek % 3600 // 60)
    s = sek % 60
    return f"{h}:{m:02d}:{s:05.2f}"


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")


def _esc(text: str) -> str:
    text = _EMOJI.sub("", text)  # Emojis fehlen in den meisten Schriften → Kästchen vermeiden
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def gruppieren(woerter: list[Wort], max_woerter: int = 3, max_dauer: float = 1.4) -> list[list[Wort]]:
    gruppen: list[list[Wort]] = []
    akt: list[Wort] = []
    for w in woerter:
        neu_satz = akt and (w.start - akt[-1].ende > 0.6)
        if akt and (len(akt) >= max_woerter or w.ende - akt[0].start > max_dauer or neu_satz):
            gruppen.append(akt)
            akt = []
        akt.append(w)
        if w.text.endswith((".", "!", "?")):
            gruppen.append(akt)
            akt = []
    if akt:
        gruppen.append(akt)
    return gruppen


def ass_datei(
    ziel: Path,
    breite: int,
    hoehe: int,
    dauer: float,
    woerter: list[Wort],
    titel: str | None,
    credit: str | None,
    schrift: str,
    layout: str,
) -> Path:
    # Positionen: TikTok-UI verdeckt rechts die Buttons und unten ~22 % (Caption, Musik)
    untertitel_y = int(hoehe * (0.73 if layout == "blur" else 0.63))
    if layout == "split":
        titel_y, titel_bis = int(hoehe * 0.335), min(dauer, 3.5)   # an der Naht, nur kurz
        credit_y = int(hoehe * 0.035)
    else:
        titel_y, titel_bis = int(hoehe * 0.17), dauer
        credit_y = int(hoehe * 0.235)

    fs_sub = int(hoehe * 0.047)
    fs_titel = int(hoehe * 0.034)
    fs_credit = int(hoehe * 0.021)
    cx = breite // 2

    kopf = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {breite}
PlayResY: {hoehe}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{schrift},{fs_sub},&H00FFFFFF,&H0000E5FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,5,60,60,0,1
Style: Titel,{schrift},{fs_titel},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,3,14,0,5,70,70,0,1
Style: Credit,{schrift},{fs_credit},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,3,1,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    zeilen: list[str] = []
    if titel:
        t = _esc(titel)
        if len(t) > 60:
            t = t[:57].rstrip() + "…"
        zeilen.append(f"Dialogue: 2,{_ts(0)},{_ts(titel_bis)},Titel,,0,0,0,,{{\\pos({cx},{titel_y})\\fad(150,200)}}{t}")
    if credit:
        zeilen.append(f"Dialogue: 2,{_ts(0)},{_ts(dauer)},Credit,,0,0,0,,{{\\pos({cx},{credit_y})}}{_esc(credit)}")

    gelb = "&H0000E5FF&"
    weiss = "&H00FFFFFF&"
    for gruppe in gruppieren(woerter):
        for i, w in enumerate(gruppe):
            start = w.start
            ende = gruppe[i + 1].start if i + 1 < len(gruppe) else w.ende + 0.15
            teile = []
            for j, x in enumerate(gruppe):
                farbe = gelb if j == i else weiss
                teile.append(f"{{\\c{farbe}}}{_esc(x.text).upper()}")
            pop = "\\fscx108\\fscy108\\t(0,80,\\fscx100\\fscy100)" if i == 0 else ""
            zeilen.append(
                f"Dialogue: 1,{_ts(start)},{_ts(ende)},Sub,,0,0,0,,{{\\pos({cx},{untertitel_y}){pop}}}" + " ".join(teile)
            )

    ziel.write_text(kopf + "\n".join(zeilen) + "\n", encoding="utf-8")
    return ziel
