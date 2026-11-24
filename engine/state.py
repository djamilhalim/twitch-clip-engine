"""Persistenter Zustand als JSON-Datei (funktioniert auf Server und in GitHub Actions)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

# Status eines Clips im Lebenszyklus
GEFUNDEN = "gefunden"
GESCHNITTEN = "geschnitten"
FREIGABE = "freigabe"      # wartet auf manuelle Freigabe (keine Erlaubnis)
WARTESCHLANGE = "warteschlange"
GEPOSTET = "gepostet"
FEHLER = "fehler"
VERWORFEN = "verworfen"


class State:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.data: dict[str, Any] = {"clips": {}, "posts": [], "hype": {}, "meta": {}}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                backup = path.with_suffix(".kaputt.json")
                path.rename(backup)

    # ── Speichern (atomar) ──
    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)

    # ── Clips ──
    def has_clip(self, clip_id: str) -> bool:
        return clip_id in self.data["clips"]

    def clip(self, clip_id: str) -> dict[str, Any] | None:
        return self.data["clips"].get(clip_id)

    def upsert_clip(self, clip_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            entry = self.data["clips"].setdefault(clip_id, {"id": clip_id, "seit": time.time()})
            entry.update(fields)
            entry["aktualisiert"] = time.time()
            return entry

    def clips_mit_status(self, *status: str) -> list[dict[str, Any]]:
        return sorted(
            (c for c in self.data["clips"].values() if c.get("status") in status),
            key=lambda c: c.get("score", 0),
            reverse=True,
        )

    def clips_heute_fuer(self, login: str, seit_ts: float) -> int:
        return sum(
            1
            for c in self.data["clips"].values()
            if c.get("streamer") == login
            and c.get("status") in (GESCHNITTEN, WARTESCHLANGE, GEPOSTET, FREIGABE)
            and c.get("seit", 0) >= seit_ts
        )

    # ── Posts ──
    def add_post(self, ziel: str, clip_id: str, ergebnis: dict[str, Any]) -> None:
        with self._lock:
            self.data["posts"].append({"ziel": ziel, "clip": clip_id, "ts": time.time(), **ergebnis})

    def posts_fuer(self, ziel: str, seit_ts: float) -> list[dict[str, Any]]:
        return [p for p in self.data["posts"] if p["ziel"] == ziel and p["ts"] >= seit_ts]

    def letzter_post(self, ziel: str) -> float:
        ts = [p["ts"] for p in self.data["posts"] if p["ziel"] == ziel and p.get("ok")]
        return max(ts) if ts else 0.0

    # ── Aufräumen ──
    def prune(self, max_alter_tage: int = 14) -> None:
        grenze = time.time() - max_alter_tage * 86400
        with self._lock:
            self.data["clips"] = {
                k: v for k, v in self.data["clips"].items() if v.get("aktualisiert", v.get("seit", 0)) >= grenze
            }
            self.data["posts"] = [p for p in self.data["posts"] if p["ts"] >= grenze]
