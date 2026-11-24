"""Konfiguration laden (config.yaml + .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


@dataclass
class Streamer:
    login: str
    erlaubnis: bool = False
    editor: bool = False
    layout: str = "blur"
    facecam: list[float] | None = None
    entdeckt: bool = False  # automatisch über top_de hinzugefügt

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Streamer":
        return cls(
            login=str(d["login"]).lower().strip(),
            erlaubnis=bool(d.get("erlaubnis", False)),
            editor=bool(d.get("editor", False)),
            layout=d.get("layout", "blur"),
            facecam=d.get("facecam"),
        )


@dataclass
class Config:
    raw: dict[str, Any]
    streamer: list[Streamer] = field(default_factory=list)
    root: Path = Path(".")

    def get(self, path: str, default: Any = None) -> Any:
        """Punkt-Pfad lesen, z. B. cfg.get('auswahl.min_views')."""
        node: Any = self.raw
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def data_dir(self) -> Path:
        p = Path(self.get("laufzeit.datenordner", "./data"))
        if not p.is_absolute():
            p = self.root / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def streamer_by_login(self, login: str) -> Streamer | None:
        login = login.lower()
        return next((s for s in self.streamer if s.login == login), None)

    @property
    def ausgeschlossen(self) -> set[str]:
        return {str(x).lower() for x in self.get("ausschliessen", []) or []}

    def darf_auto_posten(self, login: str) -> bool:
        if not self.get("nur_mit_erlaubnis", True):
            return True
        s = self.streamer_by_login(login)
        return bool(s and s.erlaubnis)


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if val is not None:
        val = val.strip() or default
    if required and not val:
        raise RuntimeError(f"Umgebungsvariable {name} fehlt (siehe .env.example)")
    return val


def load_config(path: str | os.PathLike = "config.yaml") -> Config:
    path = Path(path)
    root = path.resolve().parent
    if load_dotenv:
        load_dotenv(root / ".env")
    if not path.exists():
        raise FileNotFoundError(f"{path} nicht gefunden – kopiere config.example.yaml nach config.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    aus = {str(x).lower() for x in raw.get("ausschliessen", []) or []}
    streamer = [Streamer.from_dict(s) for s in raw.get("streamer", []) or []]
    streamer = [s for s in streamer if s.login not in aus]
    return Config(raw=raw, streamer=streamer, root=root)
