"""Streamer-Analyse: Wie viral gehen die Clips? Läuft in GitHub Actions (Twitch-API), Ergebnis → analysis/ergebnis.md

Eingabe: analysis/kandidaten.txt – eine Zeile pro Suchbegriff (Name, wie man ihn kennt).
Für jeden Begriff wird der passendste Twitch-Kanal gesucht und die Clips der letzten 30 Tage ausgewertet.
"""
from __future__ import annotations

import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.twitch import Twitch  # noqa: E402

TAGE = 30


def suche(tw: Twitch, begriff: str) -> list[dict]:
    return tw._get("/search/channels", {"query": begriff, "first": 5})


def clips_30d(tw: Twitch, bid: str) -> list[dict]:
    from datetime import timedelta

    from engine.twitch import iso

    start = datetime.now(timezone.utc) - timedelta(days=TAGE)
    params = {"broadcaster_id": bid, "started_at": iso(start), "ended_at": iso(datetime.now(timezone.utc)), "first": 100}
    out: list[dict] = []
    for _ in range(3):
        j = tw._request("GET", "/clips", params)
        out += j.get("data", [])
        cur = (j.get("pagination") or {}).get("cursor")
        if not cur:
            break
        params["after"] = cur
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    begriffe = [l.strip() for l in (root / "analysis/kandidaten.txt").read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")]
    tw = Twitch(root / "data/tokens.json")
    spiele: dict[str, str] = {}
    zeilen = []
    details = []
    for b in begriffe:
        try:
            treffer = suche(tw, b)
        except Exception as e:  # noqa: BLE001
            zeilen.append((0, f"| {b} | – | Fehler: {e} | | | | | |"))
            continue
        if not treffer:
            zeilen.append((0, f"| {b} | nicht auf Twitch gefunden | | | | | | |"))
            continue
        # bester Treffer: exakter Name bevorzugt, sonst erster
        t = next((x for x in treffer if x["broadcaster_login"].lower() == b.lower().replace(" ", "")), treffer[0])
        login, bid = t["broadcaster_login"], t["id"]
        clips = clips_30d(tw, bid)
        views = [c["view_count"] for c in clips]
        for c in clips:
            gid = c.get("game_id") or ""
            if gid and gid not in spiele:
                spiele[gid] = gid
        summe = sum(views)
        top = sorted(clips, key=lambda c: c["view_count"], reverse=True)[:3]
        kats = Counter(c.get("game_id") or "?" for c in clips)
        gta = kats.get("32982", 0)  # GTA V
        median = int(statistics.median(views)) if views else 0
        zeilen.append((summe, f"| {b} | {login} | {t.get('broadcaster_language','')} | {len(clips)} | {summe:,} | {median} | "
                              f"{top[0]['view_count'] if top else 0:,} | {gta} | KATS:{','.join(f'{k}={v}' for k, v in kats.most_common(2))} | {'🔴 ' + t.get('game_name','') if t.get('is_live') else t.get('game_name','')} |"))
        andere = ", ".join(f"{x['broadcaster_login']}" for x in treffer[:5] if x["broadcaster_login"] != login)
        details.append(f"### {b} → `{login}`\n- Weitere Suchtreffer: {andere or '–'}\n" + "\n".join(
            f"- {c['view_count']:,} Views · {c['created_at'][:10]} · {c.get('title','')[:70]}" for c in top))

    # Spielnamen nachschlagen
    try:
        ids = list(spiele)[:100]
        namen = {g["id"]: g["name"] for g in tw._get("/games", [("id", i) for i in ids])} if ids else {}
    except Exception:  # noqa: BLE001
        namen = {}

    import re

    def kat_namen(z: str) -> str:
        return re.sub(r"KATS:([^|]*)", lambda m: ", ".join(
            f"{namen.get(p.split('=')[0], p.split('=')[0])} ({p.split('=')[1]})" for p in m.group(1).strip().split(",") if "=" in p), z)

    zeilen = [(v, kat_namen(z)) for v, z in zeilen]
    zeilen.sort(key=lambda x: x[0], reverse=True)
    md = [f"# Streamer-Analyse ({datetime.now(timezone.utc):%d.%m.%Y %H:%M} UTC)",
          f"Clips der letzten {TAGE} Tage (max. 300 meistgesehene je Kanal). Sortiert nach Summe der Clip-Views.", "",
          "| Suchbegriff | Twitch-Login | Sprache | Clips | Views gesamt | Median | Top-Clip | davon GTA V | Häufigste Kategorien | Aktuell/Zuletzt |",
          "|---|---|---|---|---|---|---|---|---|---|"] + [z for _, z in zeilen] + ["", "## Top-Clips je Kanal", ""] + details
    if namen:
        md += ["", "## Spiel-IDs", ""] + [f"- {i}: {n}" for i, n in namen.items()]
    (root / "analysis/ergebnis.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:60]))


if __name__ == "__main__":
    main()
