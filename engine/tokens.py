"""Kleine Ablage für rotierende Refresh-Tokens (data/tokens.json, nur für den Besitzer lesbar)."""
from __future__ import annotations

import json
from pathlib import Path

from .config import env


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def get(self, key: str, env_name: str | None = None) -> str | None:
        return self._read().get(key) or (env(env_name) if env_name else None)

    def set(self, key: str, value: str) -> None:
        data = self._read()
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))
        self.path.chmod(0o600)
