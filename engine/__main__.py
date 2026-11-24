"""Kommandozeile:  python -m engine <befehl>

  run                  Dauerbetrieb (Server/Docker)
  once                 Ein Durchlauf (GitHub Actions / Cron)
  liste                Warteschlange und Freigaben anzeigen
  freigeben <id…>      Clip(s) aus der Freigabe in die Warteschlange schieben
  verwerfen <id…>      Clip(s) verwerfen
  auth-twitch          Twitch-User-Token holen (Live-Hype-Clips, Editor-Download)
  auth-tiktok          TikTok-Token holen (nur für Ziel tiktok_api)
  auth-youtube         YouTube-Token holen (nur für Ziel youtube_api)
  buffer-kanaele       Buffer-Kanal-IDs anzeigen
  test-schnitt <datei> [login] [layout]   Eine lokale Datei testweise schneiden
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(prog="engine", description="Twitch Clip Engine")
    ap.add_argument("befehl")
    ap.add_argument("args", nargs="*")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    from .config import load_config

    cfg = load_config(a.config)
    b = a.befehl

    if b in ("run", "once"):
        from .pipeline import Engine

        e = Engine(cfg)
        e.dauerbetrieb() if b == "run" else e.einmal()

    elif b in ("liste", "freigeben", "verwerfen"):
        from . import state as S

        st = S.State(cfg.data_dir / "state.json")
        if b == "liste":
            for status in (S.FREIGABE, S.WARTESCHLANGE, S.GEPOSTET, S.FEHLER):
                clips = st.clips_mit_status(status)
                print(f"\n── {status.upper()} ({len(clips)}) ──")
                for c in clips[:30]:
                    zeit = datetime.fromtimestamp(c.get("seit", 0)).strftime("%d.%m. %H:%M")
                    extra = c.get("fehler") or ",".join(c.get("gepostet_auf", []))
                    print(f"  {c['id'][:40]:40}  {c.get('streamer',''):16} {zeit}  {(c.get('titel') or '')[:40]:40} {extra}")
        else:
            for cid in a.args:
                if not st.has_clip(cid):
                    print(f"Unbekannt: {cid}")
                    continue
                st.upsert_clip(cid, status=S.WARTESCHLANGE if b == "freigeben" else S.VERWORFEN)
                print(f"{cid} → {'Warteschlange' if b == 'freigeben' else 'verworfen'}")
            st.save()

    elif b == "auth-twitch":
        from .twitch import Twitch

        tw = Twitch(cfg.data_dir / "tokens.json")
        print("1) In der Twitch-Dev-Konsole als OAuth Redirect URL eintragen: http://localhost:3000")
        print("2) Diese URL öffnen und erlauben:\n\n   " + tw.auth_url() + "\n")
        print("3) Der Browser landet auf einer nicht ladenden localhost-Seite. Komplette URL hier einfügen:")
        tw.exchange_code(input("> "))
        print("✅ Gespeichert in data/tokens.json")

    elif b == "auth-tiktok":
        from .publish import tiktok
        from .tokens import TokenStore

        print("Diese URL öffnen, mit dem Ziel-TikTok-Konto anmelden und erlauben:\n\n   " + tiktok.auth_url() + "\n")
        print("Danach die komplette Weiterleitungs-URL (mit ?code=…) hier einfügen:")
        tiktok.exchange_code(input("> "), TokenStore(cfg.data_dir / "tokens.json"))
        print("✅ Gespeichert")

    elif b == "auth-youtube":
        from .publish import youtube
        from .tokens import TokenStore

        print("Google-Projekt: OAuth-Client vom Typ 'Desktop-App' anlegen, YouTube Data API v3 aktivieren.")
        print("Diese URL öffnen und erlauben:\n\n   " + youtube.auth_url() + "\n")
        print("Danach die komplette localhost-URL (mit ?code=…) hier einfügen:")
        youtube.exchange_code(input("> "), TokenStore(cfg.data_dir / "tokens.json"))
        print("✅ Gespeichert")

    elif b == "buffer-kanaele":
        from .publish.buffer import kanaele_auflisten

        for k in kanaele_auflisten():
            print(f"{k['service']:12} {k['name']:30} {k['id']}   ({k['organisation']})")

    elif b == "test-schnitt":
        from .config import Streamer
        from .edit import schneiden

        if not a.args:
            sys.exit("Datei angeben")
        quelle = Path(a.args[0])
        login = a.args[1] if len(a.args) > 1 else "teststreamer"
        s = cfg.streamer_by_login(login) or Streamer(login=login)
        if len(a.args) > 2:
            s.layout = a.args[2]
        ziel = quelle.with_name(quelle.stem + f"_{s.layout}_9x16.mp4")
        res = schneiden(quelle, ziel, s, "Testtitel – das war knapp", cfg.get("schnitt", {}))
        print(f"✅ {res['datei']} ({res['dauer']:.1f} s, Layout {res['layout']})")
        if res["transkript"]:
            print("Transkript:", res["transkript"][:300])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
