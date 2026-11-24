"""Tests ohne Netz: Logik-Bausteine + kompletter Engine-Durchlauf mit Attrappen."""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine import captions as cap
from engine import scheduler, scoring, texts
from engine.hype import HypeDetector


def _clip(cid, views, minuten_alt, dauer=30, title="Krasser Moment", bid="1", vod=None, offset=None):
    created = datetime.now(timezone.utc) - timedelta(minutes=minuten_alt)
    return {
        "id": cid, "view_count": views, "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration": dauer, "title": title, "broadcaster_id": bid, "broadcaster_name": "Papaplatte",
        "video_id": vod, "vod_offset": offset, "url": f"https://clips.twitch.tv/{cid}",
    }


# ── Auswahl ─────────────────────────────────────────────────────────────
def test_auswahl_filtert_und_sortiert():
    cfg = {"min_views": 100, "min_views_pro_minute": 3, "max_dauer_s": 60, "min_dauer_s": 8,
           "blockwoerter": ["casino"]}
    clips = [
        _clip("langsam", 120, 60),            # 2 vpm → raus
        _clip("schnell", 600, 10),            # 60 vpm
        _clip("mittel", 300, 20),             # 15 vpm
        _clip("casino", 5000, 5, title="CASINO win"),
        _clip("kurz", 900, 5, dauer=4),
        _clip("bekannt", 900, 5),
    ]
    out = scoring.auswaehlen(clips, cfg, bekannt={"bekannt"}, zuschauer=20000)
    assert [c["id"] for c in out] == ["schnell", "mittel"]


def test_auswahl_entfernt_doppelte_momente():
    cfg = {"min_views": 10, "min_views_pro_minute": 1}
    clips = [_clip("a", 500, 10, vod="v1", offset=1000), _clip("b", 400, 10, vod="v1", offset=1015),
             _clip("c", 300, 10, vod="v1", offset=2000)]
    out = scoring.auswaehlen(clips, cfg, bekannt=set())
    assert [c["id"] for c in out] == ["a", "c"]


# ── Hype ────────────────────────────────────────────────────────────────
def test_hype_erkennt_ausbruch_und_cooldown():
    h = HypeDetector({"basis_fenster_s": 60, "spitzen_fenster_s": 5, "faktor": 3, "min_nachrichten_pro_s": 2,
                      "cooldown_s": 120, "trigger_woerter": ["kekw"]}, on_hype=lambda *a: None)
    t0 = 1000.0
    for i in range(60):                      # 1 Nachricht/s Grundrauschen
        assert h.feed("ch", "hallo", t0 + i) is None
    hits = [h.feed("ch", "KEKW", t0 + 60 + i * 0.1) for i in range(50)]  # 10/s
    assert any(hits)
    assert h.feed("ch", "KEKW", t0 + 66) is None  # Cooldown


# ── Scheduler ───────────────────────────────────────────────────────────
def test_slots_und_ruhezeit():
    tz = "Europe/Berlin"
    nacht = datetime(2026, 11, 20, 3, 0, tzinfo=scheduler.ZoneInfo(tz)).timestamp()
    tag = datetime(2026, 11, 20, 15, 0, tzinfo=scheduler.ZoneInfo(tz)).timestamp()
    assert scheduler.in_ruhezeit(["02:00", "08:00"], tz, nacht)
    assert not scheduler.in_ruhezeit(["02:00", "08:00"], tz, tag)
    assert scheduler.in_ruhezeit(["23:00", "07:00"], tz, nacht)
    assert scheduler.slot_frei(0, 0, 8, 75, ["02:00", "08:00"], tz, tag)
    assert not scheduler.slot_frei(8, 0, 8, 75, None, tz, tag)
    assert not scheduler.slot_frei(1, tag - 600, 8, 75, None, tz, tag)
    assert scheduler.frische_score(100, 3 * 3600) == pytest.approx(50)


# ── Texte & Untertitel ──────────────────────────────────────────────────
def test_texte():
    c = _clip("x", 1, 1, title="er hat es wirklich gemacht")
    cap_text = texts.caption(c, ["#twitch", "#fyp"], "Minecraft")
    assert cap_text.startswith("Er hat es wirklich gemacht")
    assert "#Papaplatte" in cap_text and "#Minecraft" in cap_text
    assert len(texts.youtube_titel(_clip("y", 1, 1, title="a" * 200))) <= 100
    erste = texts.caption(_clip("z", 1, 1, title=""), [], None).splitlines()[0]
    assert len(erste) > 5


def test_untertitel_gruppen():
    w = [cap.Wort(i * 0.3, i * 0.3 + 0.25, t) for i, t in enumerate("eins zwei drei vier fünf.".split())]
    g = cap.gruppieren(w)
    assert [len(x) for x in g] == [3, 2]


# ── Kompletter Durchlauf mit Attrappen ──────────────────────────────────
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg fehlt")
def test_engine_durchlauf(tmp_path, monkeypatch):
    from engine import pipeline
    from engine.config import load_config

    # Testvideo erzeugen
    roh = tmp_path / "quelle.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=10",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=10", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(roh)], check=True)

    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text(f"""
nur_mit_erlaubnis: true
streamer:
  - {{login: papaplatte, erlaubnis: true}}
  - {{login: knossi, erlaubnis: false}}
auswahl: {{min_views: 50, min_views_pro_minute: 1, max_pro_streamer_pro_tag: 2}}
schnitt: {{untertitel: false}}
posten: {{ziele: [], max_pro_tag: 5, min_abstand_min: 0, ruhezeit: null}}
laufzeit: {{datenordner: {tmp_path / 'data'}}}
""")
    monkeypatch.setenv("TWITCH_CLIENT_ID", "x")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "y")
    cfg = load_config(cfgfile)

    class FakeTwitch:
        has_user = False
        def live_streams(self, logins):
            return [{"user_login": l, "user_id": str(i), "user_name": l, "viewer_count": 20000, "game_name": "Just Chatting"}
                    for i, l in enumerate(logins)]
        def top_streams(self, *a, **k): return []
        def clips(self, bid, minutes):
            name = "papaplatte" if bid == "0" else "knossi"
            return [dict(_clip(f"{name}-1", 800, 10, bid=bid), broadcaster_name=name),
                    dict(_clip(f"{name}-2", 500, 10, bid=bid, offset=10, vod="v"), broadcaster_name=name),
                    dict(_clip(f"{name}-3", 20, 10, bid=bid), broadcaster_name=name)]

    posted = []

    class FakeZiel:
        name = "test"
        braucht_url = False
        def publish(self, datei, url, clip, texte):
            assert datei.is_file()
            posted.append((clip["id"], texte.caption))
            return {"ok": True, "id": "p1"}

    monkeypatch.setattr(pipeline, "Twitch", lambda *a, **k: FakeTwitch())
    monkeypatch.setattr(pipeline, "download_clip", lambda clip, s, tw, ordner: (shutil.copy(roh, ordner.mkdir(parents=True, exist_ok=True) or ordner / f"{clip['id']}.mp4") and Path(ordner / f"{clip['id']}.mp4"), False))

    e = pipeline.Engine(cfg)
    e.ziele = [FakeZiel()]
    e.einmal()

    st = e.state.data["clips"]
    assert st["papaplatte-1"]["status"] in ("warteschlange", "gepostet")
    assert st["knossi-1"]["status"] == "freigabe"          # keine Erlaubnis → nicht auto
    assert "papaplatte-3" not in st                         # zu wenig Views
    assert len(posted) == 1 and posted[0][0] == "papaplatte-1"
    assert Path(st["papaplatte-1"]["datei"]).is_file()

    # zweiter Lauf: postet den nächsten, verarbeitet nichts doppelt
    e.einmal()
    assert [p[0] for p in posted] == ["papaplatte-1", "papaplatte-2"]


def test_top_de_entdeckung_begrenzt():
    from engine.config import Config, Streamer
    from engine.discover import live_ziele

    cfg = Config(raw={"quelle": {"modus": "top_de", "top_de_anzahl": 2, "top_de_min_zuschauer": 1000},
                      "ausschliessen": ["montanablack88"]},
                 streamer=[Streamer(login="papaplatte", erlaubnis=True)])

    class TW:
        def live_streams(self, logins):
            return [{"user_login": "papaplatte", "viewer_count": 30000}]
        def top_streams(self, lang, count):
            return [{"user_login": n, "viewer_count": v} for n, v in
                    [("papaplatte", 30000), ("montanablack88", 50000), ("a", 9000), ("b", 8000), ("c", 7000), ("klein", 500)]]

    out = live_ziele(cfg, TW())
    assert [s.login for s, _ in out] == ["papaplatte", "a", "b"]
    assert cfg.streamer_by_login("a").entdeckt and not cfg.streamer_by_login("a").erlaubnis
