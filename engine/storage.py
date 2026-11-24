"""Öffentliche Video-URL bereitstellen (Buffer lädt das Video von einer URL). Standard: Supabase Storage."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from .config import env

log = logging.getLogger(__name__)


class SupabaseStorage:
    def __init__(self, bucket: str):
        self.url = env("SUPABASE_URL", required=True).rstrip("/")
        self.key = env("SUPABASE_SERVICE_KEY", required=True)
        self.bucket = bucket
        # Neue Supabase-Keys (sb_secret_…) gehören nur in den apikey-Header, alte JWT-Keys zusätzlich als Bearer
        self.h = {"apikey": self.key}
        if self.key.startswith("eyJ"):
            self.h["Authorization"] = f"Bearer {self.key}"

    def ensure_bucket(self) -> None:
        r = requests.get(f"{self.url}/storage/v1/bucket/{self.bucket}", headers=self.h, timeout=20)
        if r.status_code == 200:
            return
        r = requests.post(
            f"{self.url}/storage/v1/bucket",
            headers=self.h,
            json={"id": self.bucket, "name": self.bucket, "public": True},
            timeout=20,
        )
        if r.status_code not in (200, 201, 409):
            raise RuntimeError(f"Bucket anlegen fehlgeschlagen: {r.text}")

    def upload(self, datei: Path, name: str) -> str:
        with open(datei, "rb") as f:
            r = requests.post(
                f"{self.url}/storage/v1/object/{self.bucket}/{name}",
                headers={**self.h, "Content-Type": "video/mp4", "x-upsert": "true"},
                data=f,
                timeout=300,
            )
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase-Upload fehlgeschlagen: {r.status_code} {r.text[:300]}")
        return f"{self.url}/storage/v1/object/public/{self.bucket}/{name}"

    def delete_older_than(self, stunden: float) -> int:
        r = requests.post(
            f"{self.url}/storage/v1/object/list/{self.bucket}",
            headers=self.h,
            json={"prefix": "", "limit": 1000, "sortBy": {"column": "created_at", "order": "asc"}},
            timeout=30,
        )
        if r.status_code >= 400:
            return 0
        grenze = time.time() - stunden * 3600
        alt = []
        for o in r.json():
            ts = o.get("created_at")
            if not ts or not o.get("id"):
                continue
            from datetime import datetime

            if datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() < grenze:
                alt.append(o["name"])
        if alt:
            requests.delete(
                f"{self.url}/storage/v1/object/{self.bucket}", headers=self.h, json={"prefixes": alt}, timeout=30
            )
            log.info("Speicher aufgeräumt: %d alte Videos gelöscht", len(alt))
        return len(alt)
