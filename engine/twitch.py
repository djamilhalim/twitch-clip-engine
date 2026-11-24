"""Twitch Helix API (kostenlos). App-Token für Lesen, optional User-Token für Clip-Erstellung/Download."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .config import env

log = logging.getLogger(__name__)

HELIX = "https://api.twitch.tv/helix"
OAUTH = "https://id.twitch.tv/oauth2"
USER_SCOPES = ["clips:edit", "editor:manage:clips"]
REDIRECT_URI = "http://localhost:3000"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TwitchError(RuntimeError):
    pass


class Twitch:
    def __init__(self, token_file: Path, session: requests.Session | None = None):
        self.client_id = env("TWITCH_CLIENT_ID", required=True)
        self.client_secret = env("TWITCH_CLIENT_SECRET", required=True)
        self.s = session or requests.Session()
        self.token_file = token_file
        self._app_token: tuple[str, float] | None = None
        self._user_token: tuple[str, float] | None = None
        self._user_id: str | None = None
        self._id_cache: dict[str, str] = {}

    # ── Tokens ──────────────────────────────────────────────────────────
    def app_token(self) -> str:
        if self._app_token and self._app_token[1] > time.time() + 60:
            return self._app_token[0]
        r = self.s.post(
            f"{OAUTH}/token",
            data={"client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "client_credentials"},
            timeout=20,
        )
        r.raise_for_status()
        j = r.json()
        self._app_token = (j["access_token"], time.time() + j.get("expires_in", 3600))
        return self._app_token[0]

    def _stored_refresh(self) -> str | None:
        if self.token_file.exists():
            try:
                return json.loads(self.token_file.read_text()).get("twitch_refresh")
            except json.JSONDecodeError:
                pass
        return env("TWITCH_USER_REFRESH_TOKEN")

    def _store_refresh(self, token: str) -> None:
        data = {}
        if self.token_file.exists():
            try:
                data = json.loads(self.token_file.read_text())
            except json.JSONDecodeError:
                data = {}
        data["twitch_refresh"] = token
        self.token_file.write_text(json.dumps(data))
        self.token_file.chmod(0o600)

    @property
    def has_user(self) -> bool:
        return bool(self._stored_refresh())

    def user_token(self) -> str:
        if self._user_token and self._user_token[1] > time.time() + 60:
            return self._user_token[0]
        refresh = self._stored_refresh()
        if not refresh:
            raise TwitchError("Kein Twitch-User-Token. Erst `python -m engine auth-twitch` ausführen.")
        r = self.s.post(
            f"{OAUTH}/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
            timeout=20,
        )
        if r.status_code >= 400:
            raise TwitchError(f"User-Token-Refresh fehlgeschlagen: {r.text}")
        j = r.json()
        self._user_token = (j["access_token"], time.time() + j.get("expires_in", 3600))
        if j.get("refresh_token") and j["refresh_token"] != refresh:
            self._store_refresh(j["refresh_token"])
        return self._user_token[0]

    def user_id(self) -> str:
        if not self._user_id:
            data = self._get("/users", {}, user=True)
            self._user_id = data[0]["id"]
        return self._user_id

    # ── Auth-Hilfe (einmalig, interaktiv) ───────────────────────────────
    def auth_url(self) -> str:
        q = {
            "client_id": self.client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(USER_SCOPES),
        }
        return f"{OAUTH}/authorize?{urlencode(q)}"

    def exchange_code(self, redirected_url_or_code: str) -> str:
        code = redirected_url_or_code.strip()
        if code.startswith("http"):
            code = parse_qs(urlparse(code).query).get("code", [""])[0]
        r = self.s.post(
            f"{OAUTH}/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            },
            timeout=20,
        )
        if r.status_code >= 400:
            raise TwitchError(r.text)
        refresh = r.json()["refresh_token"]
        self._store_refresh(refresh)
        return refresh

    # ── HTTP ────────────────────────────────────────────────────────────
    def _headers(self, user: bool) -> dict[str, str]:
        token = self.user_token() if user else self.app_token()
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, params: Any, user: bool = False) -> dict[str, Any]:
        for versuch in range(4):
            r = self.s.request(method, f"{HELIX}{path}", params=params, headers=self._headers(user), timeout=30)
            if r.status_code == 401 and versuch == 0:
                if user:
                    self._user_token = None
                else:
                    self._app_token = None
                continue
            if r.status_code == 429:
                reset = float(r.headers.get("Ratelimit-Reset", time.time() + 5))
                time.sleep(max(1.0, reset - time.time()))
                continue
            if r.status_code >= 500:
                time.sleep(2 * (versuch + 1))
                continue
            if r.status_code >= 400:
                raise TwitchError(f"{method} {path} → {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}
        raise TwitchError(f"{method} {path}: zu viele Fehlversuche")

    def _get(self, path: str, params: Any, user: bool = False) -> list[dict[str, Any]]:
        return self._request("GET", path, params, user).get("data", [])

    # ── Endpunkte ───────────────────────────────────────────────────────
    def users(self, logins: Iterable[str]) -> list[dict[str, Any]]:
        logins = [l.lower() for l in logins]
        out: list[dict[str, Any]] = []
        for i in range(0, len(logins), 100):
            batch = logins[i : i + 100]
            out += self._get("/users", [("login", l) for l in batch])
        for u in out:
            self._id_cache[u["login"]] = u["id"]
        return out

    def broadcaster_id(self, login: str) -> str | None:
        login = login.lower()
        if login not in self._id_cache:
            self.users([login])
        return self._id_cache.get(login)

    def live_streams(self, logins: Iterable[str]) -> list[dict[str, Any]]:
        logins = list(logins)
        out: list[dict[str, Any]] = []
        for i in range(0, len(logins), 100):
            out += self._get("/streams", [("user_login", l) for l in logins[i : i + 100]] + [("first", "100")])
        return out

    def top_streams(self, language: str = "de", count: int = 20) -> list[dict[str, Any]]:
        """Größte Live-Streams einer Sprache, absteigend nach Zuschauern."""
        return self._get("/streams", {"language": language, "first": min(100, count), "type": "live"})

    def clips(self, broadcaster_id: str, since_minutes: int, first: int = 50) -> list[dict[str, Any]]:
        start = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        return self._get(
            "/clips",
            {"broadcaster_id": broadcaster_id, "started_at": iso(start), "ended_at": iso(datetime.now(timezone.utc)), "first": first},
        )

    def clip_by_id(self, clip_id: str) -> dict[str, Any] | None:
        data = self._get("/clips", {"id": clip_id})
        return data[0] if data else None

    def create_clip(self, broadcaster_id: str, title: str | None = None, duration: float = 30) -> str:
        """Erstellt einen Clip vom laufenden Stream (über dein Konto). Gibt die Clip-ID zurück."""
        params: dict[str, Any] = {"broadcaster_id": broadcaster_id, "duration": max(5, min(60, duration))}
        if title:
            params["title"] = title[:100]
        data = self._request("POST", "/clips", params, user=True).get("data", [])
        if not data:
            raise TwitchError("Create Clip lieferte keine ID")
        return data[0]["id"]

    def wait_for_clip(self, clip_id: str, timeout_s: int = 60) -> dict[str, Any] | None:
        ende = time.time() + timeout_s
        while time.time() < ende:
            c = self.clip_by_id(clip_id)
            if c:
                return c
            time.sleep(5)
        return None

    def clip_download_urls(self, broadcaster_id: str, clip_id: str) -> dict[str, str | None] | None:
        """Offizieller Download – nur wenn dein Konto Editor beim Streamer ist."""
        data = self._get(
            "/clips/downloads",
            {"editor_id": self.user_id(), "broadcaster_id": broadcaster_id, "clip_id": clip_id},
            user=True,
        )
        if not data:
            return None
        return {"landscape": data[0].get("landscape_download_url"), "portrait": data[0].get("portrait_download_url")}
