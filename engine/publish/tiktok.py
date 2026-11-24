"""Offizielle TikTok Content Posting API.
- modus "inbox":  Video landet als Entwurf in deiner TikTok-Inbox (Scope video.upload). Kein Audit nötig;
                  du tippst in der App auf „Posten“. Limit: ca. 5 offene Entwürfe pro 24 h.
- modus "direct": direkt veröffentlichen (Scope video.publish). Ohne Audit nur privat (SELF_ONLY).
"""
from __future__ import annotations

import logging
import math
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ..config import env
from ..tokens import TokenStore
from . import Texte

log = logging.getLogger(__name__)
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.upload,video.publish"
MAX_SINGLE = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024


def auth_url() -> str:
    q = {
        "client_key": env("TIKTOK_CLIENT_KEY", required=True),
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": env("TIKTOK_REDIRECT_URI", required=True),
        "state": secrets.token_urlsafe(8),
    }
    return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(q)


def exchange_code(redirected: str, tokens: TokenStore) -> str:
    code = redirected.strip()
    if code.startswith("http"):
        code = parse_qs(urlparse(code).query).get("code", [""])[0]
    r = requests.post(
        f"{API}/oauth/token/",
        data={
            "client_key": env("TIKTOK_CLIENT_KEY", required=True),
            "client_secret": env("TIKTOK_CLIENT_SECRET", required=True),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": env("TIKTOK_REDIRECT_URI", required=True),
        },
        timeout=30,
    )
    j = r.json()
    if "refresh_token" not in j:
        raise RuntimeError(f"TikTok-Token-Tausch fehlgeschlagen: {j}")
    tokens.set("tiktok_refresh", j["refresh_token"])
    return j["refresh_token"]


class TikTokZiel:
    name = "tiktok_api"
    braucht_url = False

    def __init__(self, tokens: TokenStore, modus: str = "inbox"):
        self.tokens = tokens
        self.modus = modus
        self._access: tuple[str, float] | None = None

    def _token(self) -> str:
        if self._access and self._access[1] > time.time() + 60:
            return self._access[0]
        refresh = self.tokens.get("tiktok_refresh", "TIKTOK_REFRESH_TOKEN")
        if not refresh:
            raise RuntimeError("Kein TikTok-Token. Erst `python -m engine auth-tiktok` ausführen.")
        r = requests.post(
            f"{API}/oauth/token/",
            data={
                "client_key": env("TIKTOK_CLIENT_KEY", required=True),
                "client_secret": env("TIKTOK_CLIENT_SECRET", required=True),
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
            timeout=30,
        )
        j = r.json()
        if "access_token" not in j:
            raise RuntimeError(f"TikTok-Refresh fehlgeschlagen: {j}")
        if j.get("refresh_token") and j["refresh_token"] != refresh:
            self.tokens.set("tiktok_refresh", j["refresh_token"])
        self._access = (j["access_token"], time.time() + int(j.get("expires_in", 3600)))
        return self._access[0]

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = requests.post(
            f"{API}{path}",
            json=body,
            headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json; charset=UTF-8"},
            timeout=60,
        )
        j = r.json()
        err = j.get("error", {})
        if err.get("code") not in (None, "ok"):
            raise RuntimeError(f"TikTok {path}: {err.get('code')} – {err.get('message')}")
        return j.get("data", {})

    @staticmethod
    def _chunks(size: int) -> tuple[int, int]:
        if size <= MAX_SINGLE:
            return size, 1
        return CHUNK, math.floor(size / CHUNK)

    def _upload(self, upload_url: str, datei: Path, chunk_size: int, count: int) -> None:
        size = datei.stat().st_size
        with open(datei, "rb") as f:
            for i in range(count):
                start = i * chunk_size
                ende = size - 1 if i == count - 1 else start + chunk_size - 1  # letzter Chunk nimmt den Rest
                f.seek(start)
                data = f.read(ende - start + 1)
                r = requests.put(
                    upload_url,
                    data=data,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{ende}/{size}",
                    },
                    timeout=300,
                )
                if r.status_code not in (200, 201, 206):
                    raise RuntimeError(f"TikTok-Upload Chunk {i}: {r.status_code} {r.text[:200]}")

    def publish(self, datei: Path, url: str | None, clip: dict[str, Any], texte: Texte) -> dict[str, Any]:
        size = datei.stat().st_size
        chunk_size, count = self._chunks(size)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": count}

        if self.modus == "direct":
            info = self._post("/post/publish/creator_info/query/", {})
            optionen = info.get("privacy_level_options", [])
            privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in optionen else "SELF_ONLY"
            data = self._post(
                "/post/publish/video/init/",
                {
                    "post_info": {
                        "title": texte.caption,
                        "privacy_level": privacy,
                        "disable_duet": False,
                        "disable_comment": False,
                        "disable_stitch": False,
                        "video_cover_timestamp_ms": 1000,
                    },
                    "source_info": source,
                },
            )
        else:
            data = self._post("/post/publish/inbox/video/init/", {"source_info": source})

        self._upload(data["upload_url"], datei, chunk_size, count)
        publish_id = data["publish_id"]

        # Status abfragen (bis ~2 min)
        status = "PROCESSING_UPLOAD"
        for _ in range(24):
            time.sleep(5)
            st = self._post("/post/publish/status/fetch/", {"publish_id": publish_id})
            status = st.get("status", status)
            if status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
                return {"ok": True, "id": publish_id, "info": status}
            if status == "FAILED":
                return {"ok": False, "id": publish_id, "info": st.get("fail_reason", "FAILED")}
        return {"ok": True, "id": publish_id, "info": f"noch in Verarbeitung ({status})"}
