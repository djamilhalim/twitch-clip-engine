"""Die Engine: finden → herunterladen → schneiden → einreihen → posten."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import state as S
from .config import Config, Streamer
from .discover import live_ziele
from .download import download_clip
from .edit import schneiden
from .hype import HypeDetector
from .notify import melden
from .publish import Texte, baue_ziele
from .scheduler import frische_score, slot_frei, tagesbeginn_ts, zeitfenster_slot
from .scoring import auswaehlen
from .storage import SupabaseStorage
from .texts import caption, titel_bereinigen, youtube_titel
from .tokens import TokenStore
from .twitch import Twitch, TwitchError

log = logging.getLogger(__name__)
MAX_QUEUE_ALTER_S = 12 * 3600


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        d = cfg.data_dir
        self.roh = d / "roh"
        self.fertig = d / "fertig"
        self.state = S.State(d / "state.json")
        self.tokens = TokenStore(d / "tokens.json")
        self.tw = Twitch(d / "tokens.json")
        self.ziele = baue_ziele(cfg.get("posten.ziele", []), cfg, self.tokens)
        self.speicher = None
        if any(z.braucht_url for z in self.ziele):
            self.speicher = SupabaseStorage(cfg.get("speicher.bucket", "clips"))
        self._verarbeitung = threading.Lock()  # Whisper/ffmpeg nacheinander
        self._live: dict[str, tuple[Streamer, dict[str, Any]]] = {}
        self._spiel_ids: set[str] | None = None
        self.hype: HypeDetector | None = None
        if cfg.get("hype.aktiv"):
            if self.tw.has_user:
                self.hype = HypeDetector(cfg.get("hype", {}), self._on_hype)
            else:
                log.warning("hype.aktiv ist an, aber kein Twitch-User-Token (auth-twitch) – Hype bleibt aus")

    # ── 1. Wer ist live? ────────────────────────────────────────────────
    def spiel_ids(self) -> set[str]:
        """IDs der erlaubten Spiele (config: spiele). Leer = alle Spiele."""
        if self._spiel_ids is None:
            namen = self.cfg.get("spiele", []) or []
            ids = self.tw.game_ids(namen) if namen else {}
            if namen:
                log.info("Spiele-Filter: %s", ids or "keine IDs gefunden!")
            self._spiel_ids = set(ids.values())
        return self._spiel_ids

    def live_aktualisieren(self) -> None:
        ziele = live_ziele(self.cfg, self.tw, self.spiel_ids() or None)
        self._live = {s.login: (s, st) for s, st in ziele}
        log.info("Live: %s", ", ".join(f"{l} ({st.get('viewer_count')})" for l, (_, st) in self._live.items()) or "niemand")
        if self.hype:
            self.hype.set_channels(set(self._live))
            self.hype.start()

    # ── 2. Community-Clips einsammeln ───────────────────────────────────
    def clips_einsammeln(self) -> int:
        """Sammelt Kandidaten aus Live-Streams und Tages-Fallback in EINEM Pool, verarbeitet die stärksten."""
        max_queue = int(self.cfg.get("laufzeit.max_warteschlange", 15))
        if len(self.state.clips_mit_status(S.WARTESCHLANGE)) >= max_queue:
            log.info("Warteschlange voll (≥%d) – keine neuen Clips in diesem Lauf", max_queue)
            return 0
        fenster = int(self.cfg.get("auswahl.fenster_minuten", 90))
        max_tag = int(self.cfg.get("auswahl.max_pro_streamer_pro_tag", 3))
        max_lauf = int(self.cfg.get("laufzeit.max_neue_pro_lauf", 0)) or 10**6
        heute = tagesbeginn_ts(self.cfg.get("posten.zeitzone", "Europe/Berlin"))
        bekannt = set(self.state.data["clips"])
        pool: list[tuple[dict[str, Any], Streamer, str]] = []

        for login, (streamer, stream) in list(self._live.items()):
            if self.state.clips_heute_fuer(login, heute) >= max_tag:
                continue
            try:
                clips = self.tw.clips(stream["user_id"], fenster)
            except TwitchError as e:
                log.warning("Clips für %s nicht abrufbar: %s", login, e)
                continue
            ist_fokus = login in self.cfg.fokus and self.cfg.get("fokus.alle_kategorien", True)
            if self.spiel_ids() and not ist_fokus:
                clips = [c for c in clips if c.get("game_id") in self.spiel_ids()]
            kandidaten = auswaehlen(clips, self.cfg.get("auswahl", {}), bekannt, stream.get("viewer_count"))
            log.info("%s: %d Clips im Fenster, %d passen", login, len(clips), len(kandidaten))
            gew = self._gewicht(login)
            for c in kandidaten[:2]:
                c["_spiel"] = stream.get("game_name")
                c["_score"] = c.get("_score", 0) * gew
                pool.append((c, streamer, "community"))

        nur_fokus = bool(self.cfg.get("fokus.nur_fokus", False)) and self.cfg.fokus
        if self.cfg.fokus:
            pool += self._fokus_kandidaten(heute, max_tag, bekannt)
        if nur_fokus:
            pool = [x for x in pool if x[1].login in self.cfg.fokus]
        elif self.spiel_ids():
            pool += self._spiel_kandidaten(heute, max_tag, bekannt)
        elif self.cfg.get("auswahl.tages_fallback", True):
            pool += self._tages_kandidaten(heute, max_tag, bekannt)

        pool.sort(key=lambda x: x[0].get("_score", 0), reverse=True)
        log.info("Pool: %d Kandidaten, verarbeite bis zu %d", len(pool), max_lauf)
        neu = 0
        limits = {str(k).lower(): int(v) for k, v in (self.cfg.get("fokus.max_pro_tag", {}) or {}).items()}
        for c, s, quelle in pool:
            if neu >= max_lauf:
                break
            if self.state.clips_heute_fuer(s.login, heute) >= limits.get(s.login, max_tag):
                continue
            if self.verarbeiten(c, s, quelle=quelle):
                neu += 1
        return neu

    def _gewicht(self, login: str, standard: float | None = None) -> float:
        gew = {str(k).lower(): float(v) for k, v in (self.cfg.get("fokus.gewichte", {}) or {}).items()}
        if login in gew:
            return gew[login]
        if standard is not None:
            return standard
        return float(self.cfg.get("fokus.bonus", 2.0)) if login in self.cfg.fokus else 1.0

    def _fokus_kandidaten(self, heute: float, max_tag: int, bekannt: set[str]) -> list[tuple[dict[str, Any], Streamer, str]]:
        """Beste Clips der Fokus-Streamer der letzten Stunden (auch offline), mit Bonus im Ranking."""
        aus = dict(self.cfg.get("auswahl", {}))
        aus["min_views"] = int(self.cfg.get("fokus.min_views", 50))
        aus["min_views_pro_minute"] = 0
        stunden = int(self.cfg.get("fokus.fenster_stunden", 48))
        bonus = float(self.cfg.get("fokus.bonus", 2.0))
        alle_kat = bool(self.cfg.get("fokus.alle_kategorien", True))
        try:
            ids = {u["login"]: u["id"] for u in self.tw.users(sorted(self.cfg.fokus))}
        except TwitchError as e:
            log.warning("Fokus: Nutzer nicht abrufbar: %s", e)
            return []
        out: list[tuple[dict[str, Any], Streamer, str]] = []
        for login, bid in ids.items():
            if self.state.clips_heute_fuer(login, heute) >= max_tag:
                continue
            try:
                clips = self.tw.clips(bid, stunden * 60, first=100)
            except TwitchError as e:
                log.warning("Fokus %s: %s", login, e)
                continue
            if not alle_kat and self.spiel_ids():
                clips = [c for c in clips if c.get("game_id") in self.spiel_ids()]
            mins = {str(k).lower(): int(v) for k, v in (self.cfg.get("fokus.min_views_je", {}) or {}).items()}
            aus_s = dict(aus, min_views=mins.get(login, aus["min_views"]))
            gewaehlt = auswaehlen(clips, aus_s, bekannt, None)[:3]
            log.info("Fokus %s: %d Clips, %d passen", login, len(clips), len(gewaehlt))
            s = self.cfg.streamer_by_login(login) or Streamer(login=login)
            gew = self._gewicht(login, bonus)
            # Fair über Streamer hinweg: Views relativ zum typischen Clip DIESES Streamers
            # (Marli/Sidney haben auf Twitch wenig Views, gehen auf TikTok aber gut)
            alle_views = sorted(c.get("view_count", 0) for c in clips) or [1]
            median = max(alle_views[len(alle_views) // 2], 5)
            for c in gewaehlt:
                c["_score"] = c.get("view_count", 0) / median * gew
                out.append((c, s, "fokus"))
        return out

    def _spiel_kandidaten(self, heute: float, max_tag: int, bekannt: set[str]) -> list[tuple[dict[str, Any], Streamer, str]]:
        """Meistgesehene deutsche Clips der erlaubten Spiele (z. B. GTA) der letzten Stunden – egal welcher Streamer."""
        aus = dict(self.cfg.get("auswahl", {}))
        aus["min_views"] = int(self.cfg.get("auswahl.tages_min_views", 25))
        aus["min_views_pro_minute"] = 0
        stunden = int(self.cfg.get("auswahl.tages_fenster_stunden", 48))
        sprache = self.cfg.get("auswahl.sprache", "de")
        roh: list[dict[str, Any]] = []
        for gid in self.spiel_ids():
            try:
                roh += self.tw.clips_by_game(gid, stunden * 60, pages=int(self.cfg.get("auswahl.seiten", 8)))
            except TwitchError as e:
                log.warning("Spiel-Clips %s: %s", gid, e)
        de = [c for c in roh if (c.get("language") or "").lower().startswith(sprache)
              and c.get("broadcaster_name", "").lower() not in self.cfg.ausgeschlossen]
        gewaehlt = auswaehlen(de, aus, bekannt, None)
        log.info("Spiel-Clips: %d gesamt, %d auf %s, %d passen", len(roh), len(de), sprache, len(gewaehlt))
        out: list[tuple[dict[str, Any], Streamer, str]] = []
        pro_streamer: dict[str, int] = {}
        for c in gewaehlt:
            login = c["broadcaster_name"].lower()
            if pro_streamer.get(login, 0) >= 2 or self.state.clips_heute_fuer(login, heute) >= max_tag:
                continue
            pro_streamer[login] = pro_streamer.get(login, 0) + 1
            s = self.cfg.streamer_by_login(login) or Streamer(login=login, entdeckt=True)
            c["_score"] = c.get("view_count", 0) / 100
            c["_spiel"] = "GTA"
            out.append((c, s, "spiel"))
        return out

    def _tages_kandidaten(self, heute: float, max_tag: int, bekannt: set[str]) -> list[tuple[dict[str, Any], Streamer, str]]:
        aus = dict(self.cfg.get("auswahl", {}))
        aus["min_views"] = int(self.cfg.get("auswahl.tages_min_views", 25))
        aus["min_views_pro_minute"] = 0
        stunden = int(self.cfg.get("auswahl.tages_fenster_stunden", 24))
        feste = [s for s in self.cfg.streamer if not s.entdeckt and s.login not in self._live]
        if not feste:
            return []
        try:
            ids = {u["login"]: u["id"] for u in self.tw.users([s.login for s in feste])}
        except TwitchError as e:
            log.warning("Tages-Fallback: Nutzer nicht abrufbar: %s", e)
            return []
        out: list[tuple[dict[str, Any], Streamer, str]] = []
        for s in feste:
            if s.login not in ids or self.state.clips_heute_fuer(s.login, heute) >= max_tag:
                continue
            try:
                clips = self.tw.clips(ids[s.login], stunden * 60)
            except TwitchError as e:
                log.warning("Tages-Fallback %s: %s", s.login, e)
                continue
            gewaehlt = auswaehlen(clips, aus, bekannt, None)[:3]
            log.info("Tages-Fallback %s: %d Clips, %d passen", s.login, len(clips), len(gewaehlt))
            for c in gewaehlt:
                c["_score"] = c.get("view_count", 0) / 100  # absolute Views: stärkste zuerst
                out.append((c, s, "tag"))
        return out

    # ── 3. Live-Hype → eigener Clip ─────────────────────────────────────
    def _on_hype(self, channel: str, info: dict[str, Any]) -> None:
        eintrag = self._live.get(channel)
        if not eintrag:
            return
        streamer, stream = eintrag
        try:
            dauer = float(self.cfg.get("hype.clip_dauer_s", 45))
            clip_id = self.tw.create_clip(stream["user_id"], title=f"{stream['user_name']} Moment", duration=dauer)
            clip = self.tw.wait_for_clip(clip_id)
            if not clip:
                log.warning("Hype-Clip %s nicht rechtzeitig verfügbar", clip_id)
                return
            clip["_score"] = 50 * info.get("rate_spitze", 1) / max(info.get("rate_basis", 1), 0.5)
            clip["_spiel"] = stream.get("game_name")
            self.verarbeiten(clip, streamer, quelle="hype")
        except TwitchError as e:
            log.warning("Hype-Clip bei %s fehlgeschlagen: %s", channel, e)

    # ── Verarbeitung eines Clips ────────────────────────────────────────
    def _titel_doppelt(self, streamer: str, titel: str) -> bool:
        """Mehrere Zuschauer clippen denselben Moment unter gleichem Titel → nur einmal nehmen."""
        norm = "".join(ch for ch in (titel or "").lower() if ch.isalnum())
        if len(norm) < 6:
            return False
        grenze = time.time() - 3 * 86400
        for c in self.state.data["clips"].values():
            if c.get("streamer") == streamer and c.get("seit", 0) >= grenze and c.get("status") != S.FEHLER:
                if "".join(ch for ch in (c.get("titel") or "").lower() if ch.isalnum()) == norm:
                    return True
        return False

    def verarbeiten(self, clip: dict[str, Any], streamer: Streamer, quelle: str) -> bool:
        cid = clip["id"]
        if self.state.has_clip(cid):
            return False
        if self._titel_doppelt(streamer.login, clip.get("title", "")):
            log.info("Übersprungen (gleicher Titel schon da): %s – %s", streamer.login, clip.get("title"))
            return False
        self.state.upsert_clip(
            cid, status=S.GEFUNDEN, streamer=streamer.login, quelle=quelle, titel=clip.get("title"),
            url=clip.get("url"), views=clip.get("view_count"), score=clip.get("_score", 0),
            erstellt=clip.get("created_at"),
        )
        self.state.save()
        with self._verarbeitung:
            try:
                roh, hoch = download_clip(clip, streamer, self.tw, self.roh)
                self.fertig.mkdir(parents=True, exist_ok=True)
                ziel = self.fertig / f"{streamer.login}_{cid}.mp4"
                titel = titel_bereinigen(clip.get("title", ""), clip.get("broadcaster_name", streamer.login), cid)
                res = schneiden(roh, ziel, streamer, titel, self.cfg.get("schnitt", {}), hochformat=hoch)
            except Exception as e:  # ein kaputter Clip darf die Engine nicht stoppen
                log.exception("Verarbeitung %s fehlgeschlagen", cid)
                self.state.upsert_clip(cid, status=S.FEHLER, fehler=str(e)[:500])
                self.state.save()
                return False

        # Sofort öffentlich ablegen: Buffer braucht eine URL, und in GitHub Actions
        # ist die lokale Datei nach dem Lauf weg.
        public_url = None
        if self.speicher:
            try:
                self.speicher.ensure_bucket()
                public_url = self.speicher.upload(Path(res["datei"]), Path(res["datei"]).name)
            except Exception as e:
                log.warning("Upload in den Speicher fehlgeschlagen (%s) – neuer Versuch beim Posten", e)

        auto = self.cfg.darf_auto_posten(streamer.login)
        status = S.WARTESCHLANGE if auto else S.FREIGABE
        self.state.upsert_clip(
            cid, status=status, datei=res["datei"], dauer=res["dauer"], layout=res["layout"],
            transkript=res["transkript"][:1000], gepostet_auf=[],
            caption=caption(clip, self.cfg.get("posten.hashtags_basis", []), clip.get("_spiel")),
            yt_titel=youtube_titel(clip), broadcaster_name=clip["broadcaster_name"], public_url=public_url,
        )
        self.state.save()
        log.info("✂️  %s (%s) → %s", cid, streamer.login, status)
        if not auto:
            melden(f"🟡 Freigabe nötig: **{streamer.login}** – {clip.get('title')}\n{clip.get('url')}\n"
                   f"`python -m engine freigeben {cid}`")
        return True

    # ── 4. Posten ───────────────────────────────────────────────────────
    def posten(self) -> int:
        tz = self.cfg.get("posten.zeitzone", "Europe/Berlin")
        heute = tagesbeginn_ts(tz)
        max_tag = int(self.cfg.get("posten.max_pro_tag", 8))
        abstand = float(self.cfg.get("posten.min_abstand_min", 75))
        ruhe = self.cfg.get("posten.ruhezeit")
        gepostet = 0
        jetzt = time.time()

        # abgelaufene Clips verwerfen
        for c in self.state.clips_mit_status(S.WARTESCHLANGE):
            if jetzt - c.get("seit", jetzt) > MAX_QUEUE_ALTER_S and not c.get("gepostet_auf"):
                self.state.upsert_clip(c["id"], status=S.VERWORFEN, fehler="zu alt")

        for ziel in self.ziele:
            erfolgreich_heute = [p for p in self.state.posts_fuer(ziel.name, heute) if p.get("ok")]
            zeitplan = self.cfg.get("posten.zeitplan")
            if zeitplan:
                ok_ts = [p["ts"] for p in erfolgreich_heute]
                frei, grund = zeitfenster_slot(zeitplan, ok_ts, abstand, tz)
                if not frei or len(erfolgreich_heute) >= max_tag:
                    log.info("%s: kein Slot (%s, heute %d/%d)", ziel.name, grund, len(erfolgreich_heute), max_tag)
                    continue
            elif not slot_frei(len(erfolgreich_heute), self.state.letzter_post(ziel.name), max_tag, abstand, ruhe, tz):
                log.info("%s: kein Slot frei (heute %d/%d, Ruhezeit %s)", ziel.name, len(erfolgreich_heute), max_tag, ruhe)
                continue
            kandidaten = [c for c in self.state.clips_mit_status(S.WARTESCHLANGE)
                          if ziel.name not in c.get("gepostet_auf", []) and self._verfuegbar(c, ziel.braucht_url)]
            if not kandidaten:
                log.info("%s: Warteschlange leer", ziel.name)
                continue
            heute_pro = {}
            for p in erfolgreich_heute:
                st = self.state.clip(p["clip"]) or {}
                heute_pro[st.get("streamer")] = heute_pro.get(st.get("streamer"), 0) + 1
            kandidaten.sort(key=lambda c: frische_score(c.get("score", 0), jetzt - c.get("seit", jetzt))
                            / (1 + heute_pro.get(c.get("streamer"), 0)), reverse=True)
            c = kandidaten[0]
            try:
                url = c.get("public_url") if ziel.braucht_url else None
                if ziel.braucht_url and url:
                    datei = Path(c.get("datei") or url)  # Datei wird von Buffer per URL geholt
                else:
                    datei = self._lokale_datei(c)
                    if ziel.braucht_url:
                        self.speicher.ensure_bucket()
                        url = self.speicher.upload(datei, datei.name)
                        self.state.upsert_clip(c["id"], public_url=url)
                texte = Texte(
                    caption=c["caption"],
                    yt_titel=c["yt_titel"],
                    yt_beschreibung=c["caption"],
                )
                clip_info = {"broadcaster_name": c.get("broadcaster_name", c["streamer"]), "id": c["id"]}
                res = ziel.publish(datei, url, clip_info, texte)
            except Exception as e:
                log.exception("Posten auf %s fehlgeschlagen", ziel.name)
                res = {"ok": False, "info": str(e)[:300]}
            self.state.add_post(ziel.name, c["id"], res)
            if res.get("ok"):
                gepostet += 1
                auf = list(set(c.get("gepostet_auf", [])) | {ziel.name})
                fertig = all(z.name in auf for z in self.ziele)
                self.state.upsert_clip(c["id"], gepostet_auf=auf, status=S.GEPOSTET if fertig else S.WARTESCHLANGE)
                log.info("📤 %s → %s (%s)", c["id"], ziel.name, res.get("info", res.get("id")))
            else:
                fehlversuche = c.get("fehlversuche", 0) + 1
                self.state.upsert_clip(c["id"], fehlversuche=fehlversuche,
                                       status=S.FEHLER if fehlversuche >= 3 else S.WARTESCHLANGE)
                melden(f"🔴 Posten auf {ziel.name} fehlgeschlagen: {res.get('info')}")
            self.state.save()
        return gepostet

    @staticmethod
    def _verfuegbar(c: dict[str, Any], braucht_url: bool) -> bool:
        lokal = bool(c.get("datei")) and Path(c["datei"]).is_file()
        return lokal or bool(c.get("public_url"))

    def _lokale_datei(self, c: dict[str, Any]) -> Path:
        """Lokale Datei; falls weg (z. B. GitHub Actions), aus dem Speicher zurückholen."""
        if c.get("datei") and Path(c["datei"]).is_file():
            return Path(c["datei"])
        url = c.get("public_url")
        if not url:
            raise RuntimeError("Weder lokale Datei noch öffentliche URL vorhanden")
        self.fertig.mkdir(parents=True, exist_ok=True)
        ziel = self.fertig / Path(url).name
        if not ziel.is_file():
            import requests

            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(ziel, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
        self.state.upsert_clip(c["id"], datei=str(ziel))
        return ziel

    # ── 5. Aufräumen ────────────────────────────────────────────────────
    def aufraeumen(self) -> None:
        grenze = time.time() - 2 * 86400
        for ordner in (self.roh, self.fertig):
            if ordner.exists():
                for f in ordner.iterdir():
                    if f.stat().st_mtime < grenze:
                        f.unlink(missing_ok=True)
        if self.speicher:
            try:
                self.speicher.delete_older_than(float(self.cfg.get("speicher.loeschen_nach_stunden", 48)))
            except Exception as e:
                log.debug("Speicher-Aufräumen: %s", e)
        self.state.prune()
        self.state.save()

    # ── Betriebsarten ───────────────────────────────────────────────────
    def einmal(self) -> None:
        """Ein kompletter Durchlauf (für GitHub Actions / Cron)."""
        self.live_aktualisieren()
        self.clips_einsammeln()
        self.posten()
        self.aufraeumen()

    def dauerbetrieb(self) -> None:
        t_live = int(self.cfg.get("laufzeit.intervall_live_check_s", 120))
        t_clips = int(self.cfg.get("laufzeit.intervall_clips_s", 300))
        naechst = {"live": 0.0, "clips": 0.0, "post": 0.0, "putz": time.time() + 3600}
        melden("🟢 Twitch Clip Engine gestartet")
        while True:
            jetzt = time.time()
            try:
                if jetzt >= naechst["live"]:
                    self.live_aktualisieren()
                    naechst["live"] = jetzt + t_live
                if jetzt >= naechst["clips"]:
                    self.clips_einsammeln()
                    naechst["clips"] = jetzt + t_clips
                if jetzt >= naechst["post"]:
                    self.posten()
                    naechst["post"] = jetzt + 60
                if jetzt >= naechst["putz"]:
                    self.aufraeumen()
                    naechst["putz"] = jetzt + 6 * 3600
            except Exception:
                log.exception("Fehler im Hauptlauf – weiter in 60 s")
                time.sleep(60)
            time.sleep(5)
