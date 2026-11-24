"""Buffer GraphQL API (https://developers.buffer.com). Buffer ist bei TikTok und YouTube zugelassen,
dadurch sind Posts sofort öffentlich – ohne eigenes App-Audit. API-Zugang ist in jedem Plan enthalten."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

from ..config import env
from . import Texte

log = logging.getLogger(__name__)
API = "https://api.buffer.com"

CREATE = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id } }
    ... on MutationError { message }
  }
}
"""


def gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    key = env("BUFFER_API_KEY", required=True)
    r = requests.post(
        API,
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Buffer {r.status_code}: {r.text[:400]}")
    j = r.json()
    if j.get("errors"):
        raise RuntimeError(f"Buffer: {j['errors']}")
    return j["data"]


def kanaele_auflisten() -> list[dict[str, Any]]:
    orgs = gql("query { account { organizations { id name } } }")["account"]["organizations"]
    out = []
    for o in orgs:
        chans = gql(
            "query { channels(input: {organizationId: %s}) { id service name } }" % json.dumps(o["id"])
        )["channels"]
        for c in chans:
            out.append({**c, "organisation": o["name"]})
    return out


class BufferZiel:
    braucht_url = True

    def __init__(self, name: str, channel_env: str, service: str, yt_kategorie: str = "20"):
        self.name = name
        self.channel_env = channel_env
        self.service = service
        self.yt_kategorie = yt_kategorie

    def publish(self, datei: Path, url: str | None, clip: dict[str, Any], texte: Texte) -> dict[str, Any]:
        if not url:
            raise RuntimeError("Buffer braucht eine öffentliche Video-URL (speicher.anbieter: supabase)")
        channel = env(self.channel_env, required=True)
        inp: dict[str, Any] = {
            "channelId": channel,
            "schedulingType": "automatic",
            "mode": "shareNow",
            "assets": [{"video": {"url": url, "metadata": {"thumbnailOffset": 1000}}}],
        }
        if self.service == "youtube":
            inp["text"] = texte.yt_beschreibung
            inp["metadata"] = {
                "youtube": {
                    "title": texte.yt_titel,
                    "categoryId": self.yt_kategorie,
                    "privacy": "public",
                    "madeForKids": False,
                    "notifySubscribers": False,
                }
            }
        else:
            inp["text"] = texte.caption
        data = gql(CREATE, {"input": inp})["createPost"]
        if "message" in data:
            return {"ok": False, "info": data["message"]}
        return {"ok": True, "id": data["post"]["id"]}
