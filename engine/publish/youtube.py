"""YouTube Data API v3 – Upload als Short (resumable). Achtung: Uploads aus nicht geprüften
Google-Projekten bleiben privat, bis das Projekt das YouTube-API-Audit bestanden hat."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ..config import env
from ..tokens import TokenStore
from . import Texte

log = logging.getLogger(__name__)
REDIRECT = "http://localhost:8080"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def auth_url() -> str:
    q = {
        "client_id": env("YOUTUBE_CLIENT_ID", required=True),
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(q)


def exchange_code(redirected: str, tokens: TokenStore) -> str:
    code = redirected.strip()
    if code.startswith("http"):
        code = parse_qs(urlparse(code).query).get("code", [""])[0]
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": env("YOUTUBE_CLIENT_ID", required=True),
            "client_secret": env("YOUTUBE_CLIENT_SECRET", required=True),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT,
        },
        timeout=30,
    )
    j = r.json()
    if "refresh_token" not in j:
        raise RuntimeError(f"Google-Token-Tausch fehlgeschlagen: {j}")
    tokens.set("youtube_refresh", j["refresh_token"])
    return j["refresh_token"]


class YouTubeZiel:
    name = "youtube_api"
    braucht_url = False

    def __init__(self, tokens: TokenStore, kategorie: str = "20"):
        self.tokens = tokens
        self.kategorie = kategorie
        self._access: tuple[str, float] | None = None

    def _token(self) -> str:
        if self._access and self._access[1] > time.time() + 60:
            return self._access[0]
        refresh = self.tokens.get("youtube_refresh", "YOUTUBE_REFRESH_TOKEN")
        if not refresh:
            raise RuntimeError("Kein YouTube-Token. Erst `python -m engine auth-youtube` ausführen.")
        r = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": env("YOUTUBE_CLIENT_ID", required=True),
                "client_secret": env("YOUTUBE_CLIENT_SECRET", required=True),
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        j = r.json()
        if "access_token" not in j:
            raise RuntimeError(f"Google-Refresh fehlgeschlagen: {j}")
        self._access = (j["access_token"], time.time() + int(j.get("expires_in", 3600)))
        return self._access[0]

    def publish(self, datei: Path, url: str | None, clip: dict[str, Any], texte: Texte) -> dict[str, Any]:
        meta = {
            "snippet": {
                "title": texte.yt_titel,
                "description": texte.yt_beschreibung,
                "categoryId": self.kategorie,
                "defaultLanguage": "de",
                "defaultAudioLanguage": "de",
                "tags": [clip["broadcaster_name"], "twitch", "clips", "deutsch"],
            },
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }
        h = {"Authorization": f"Bearer {self._token()}"}
        init = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
            headers={**h, "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(datei.stat().st_size)},
            data=json.dumps(meta),
            timeout=60,
        )
        if init.status_code >= 400:
            return {"ok": False, "info": f"Init {init.status_code}: {init.text[:300]}"}
        with open(datei, "rb") as f:
            r = requests.put(init.headers["Location"], headers={**h, "Content-Type": "video/mp4"}, data=f, timeout=600)
        if r.status_code >= 400:
            return {"ok": False, "info": f"Upload {r.status_code}: {r.text[:300]}"}
        j = r.json()
        privat = j.get("status", {}).get("privacyStatus")
        info = "öffentlich" if privat == "public" else f"Status: {privat} (Projekt evtl. nicht geprüft → privat)"
        return {"ok": True, "id": j.get("id"), "info": info}
