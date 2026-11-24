# Twitch-Clips automatisch auf TikTok posten – Setup zum Nachbauen

**Von:** Djamil (gebaut mit Claude) · **Stand:** 24.11.2026
**Code:** öffentliches Repo https://github.com/djamilhalim/twitch-clip-engine

---

## Teil 1 – Für euch (2 Minuten lesen)

Wir haben eine „Engine“ gebaut, die **komplett automatisch** die besten Momente von Twitch-Streamern findet, sie fürs Handy zuschneidet (Hochformat, Untertitel, Titel) und auf **TikTok** postet. Ziel: ca. 20 Videos am Tag, ohne dass jemand etwas anfassen muss.

- **Kosten:** 0 €. Alles läuft auf kostenlosen Diensten.
- **PC muss nicht an sein.** Die Engine läuft in der Cloud bei GitHub, rund um die Uhr.
- **Euer Aufwand:** einmal ca. 30–45 Minuten Konten anlegen und Schlüssel kopieren. Den Rest macht euer Claude.
- **Wie es aussieht:** oben der Titel, darunter „twitch.tv/streamer“ als Quellenangabe, in der Mitte das komplette Streambild auf unscharfem Hintergrund, unten Untertitel Wort für Wort.

**Was ihr braucht:**
1. Einen TikTok-Account (am besten ein **neuer**, nur für die Clips, als Business-Konto)
2. Einen **Buffer**-Account (Gratis-Plan) – darüber wird gepostet
3. Einen **Twitch**-Account mit **Zwei-Faktor-Schutz** (ohne 2FA lässt Twitch keine Entwickler-App zu)
4. Einen **GitHub**-Account
5. Einen **Supabase**-Account (Speicher für die fertigen Videos)

**Tipp:** Legt euch für das Projekt ein eigenes Chrome-Profil an und meldet dort alle neuen Konten an. Dann kommt nichts mit euren anderen Konten durcheinander, und euer Claude kann darin mitklicken.

**Wichtig – Risiko:** Die Clips gehören den Streamern. Ohne deren Erlaubnis kann es Copyright-Meldungen oder Kontosperren geben. Fan-Clip-Kanäle sind verbreitet, aber es bleibt euer Risiko. Viele große Streamer bezahlen Clipper sogar (z. B. über „Content Rewards“ auf Whop) – das ist der sauberste Weg, damit Geld zu verdienen.

Gebt die Datei unten einfach eurem Claude und sagt: **„Bau mir das nach.“**

---

## Teil 2 – Für euren Claude

> Hallo Claude. Diese Anleitung beschreibt ein funktionierendes System, das Djamil und sein Claude gebaut haben. Bitte baue es für deinen Nutzer nach. Der Code liegt öffentlich unter https://github.com/djamilhalim/twitch-clip-engine – am einfachsten ist ein **Fork** (oder „Use this template“) in den GitHub-Account deines Nutzers. Danach musst du nur Konten, Secrets und `config.yaml` anpassen.

### 1. Was das System macht

```
Twitch-API (kostenlos)          Schnitt (GitHub-Runner)             Posten
┌───────────────────────┐   ┌──────────┐   ┌──────────────────────┐   ┌────────────────────┐
│ Fokus-Streamer-Clips  │──▶│ yt-dlp   │──▶│ ffmpeg 1080×1920     │──▶│ Supabase (URL)     │
│ Spiel-Clips (z.B. GTA)│   │ Download │   │ + faster-whisper     │   │ → Buffer → TikTok  │
│ Live-Streams          │   └──────────┘   │ Untertitel, Titel    │   │ Slots/Limits/Pause │
└───────────────────────┘                  └──────────────────────┘   └────────────────────┘
        ▲                                                                     │
        └──────── GitHub Actions: Dauerlauf, alle 20 Min ein Durchlauf ───────┘
                  Zustand in data/state.json (wird zurück ins Repo committet)
```

### 2. Technik-Entscheidungen und warum

| Entscheidung | Grund |
|---|---|
| **Twitch Helix API** (App-Token, Client-Credentials) statt Scraping | Kostenlos, stabil. `GET /clips` liefert Clips je Streamer **oder je Spiel** (`game_id`) inkl. `view_count`, `language`, `game_id`. |
| **Community-Clips** als Quelle statt Stream-Mitschnitt | Zuschauer clippen genau die viralen Momente. Kein Dauer-Recording nötig. |
| **Buffer** (GraphQL-API, `https://api.buffer.com`, Bearer-Key) zum Posten | Eigene TikTok-Apps posten ohne TikTok-Audit **nur privat** (`SELF_ONLY`). Buffer ist zugelassen → Posts sind sofort öffentlich. Buffer holt das Video per **öffentlicher URL**. |
| **Supabase Storage** (öffentlicher Bucket `clips`) | Liefert die öffentliche URL für Buffer. Videos werden nach 48 h automatisch gelöscht. Hinweis: Gratis-Plan hat ein Traffic-Limit; bei ~20 Videos/Tag knapp, ein vorhandener Pro-Plan reicht locker. |
| **GitHub Actions**, öffentliches Repo | Unbegrenzte Gratis-Minuten bei öffentlichen Repos. Läuft ohne PC. Secrets liegen verschlüsselt in den Repo-Secrets. |
| **faster-whisper** (`small`, int8, CPU) | Deutsche Untertitel lokal und kostenlos. |
| **YouTube** (optional) | Eigene Google-Projekte laden ohne Audit nur **privat** hoch. Entweder über Buffer posten oder das Projekt prüfen lassen. Ziel `youtube_api` ist im Code vorhanden. |

### 3. Stolperfallen, die wir schon gelöst haben (wichtig!)

1. **GitHub-Zeitplan (`schedule`) ist unzuverlässig.** Beim neuen Repo lief der Cron 7 Stunden lang gar nicht. Lösung im Workflow: Ein Job läuft ~5,7 h und macht alle 20 Min einen Durchlauf, am Ende startet er sich selbst neu (`gh workflow run`, braucht `permissions: actions: write`). Der Cron bleibt nur als Reserve.
2. **Twitch-Clips haben real wenig Views** (meist 10–3.000). Feste hohe Mindestwerte lassen nichts durch. Lösung: **Rangfolge** (beste Clips zuerst) mit niedrigen Mindestwerten.
3. **Nachts sind Live-Clips schwach.** Deshalb ein gemeinsamer Pool aus Live-Clips, den besten Clips der Fokus-Streamer der letzten 48 h und Spiel-Clips – sortiert nach Views.
4. **faster-whisper + neues PyAV** → Fehler `unexpected keyword argument 'metadata_errors'`. Lösung: Audio per ffmpeg als 16-kHz-Array extrahieren und direkt an `transcribe()` geben.
5. **Whisper erfindet Text** bei Musik/Spielsound („KÜHL, FILGT DIES“). Lösung: Segmente mit `no_speech_prob > 0.5` oder `avg_logprob < -0.8` und Wörter mit `probability < 0.35` weglassen.
6. **Nicht reinzoomen.** Ein Ausschnitt schneidet die Facecam ab. Das ganze 16:9-Bild auf unscharfem Hintergrund wirkt besser.
7. **Schlechte Clip-Titel** („????XDDDD“, „eier“) werden automatisch durch Aufhänger ersetzt („Was macht X da?! 😂“).
8. **Twitch-Entwickler-App** geht nur mit 2FA auf dem Twitch-Konto. Nach dem Einrichten von 2FA ab- und neu anmelden.
9. **Buffer-API-Key** gibt es erst nach bestätigter E-Mail. Der Key wird nur **einmal** angezeigt.
10. **Glücksspiel-Clips** (Casino, Slots) sperrt TikTok schnell → Blockwörter in der Config.
11. **TikTok** kann Posts über Schnittstellen ab ~15 pro Tag drosseln → beobachten.
12. Wenn du im Browser Videos löschst, während die Engine postet, **verrutscht die Liste**. Immer die Caption der Zeile prüfen, nie nach Position klicken.

### 4. Einrichtung Schritt für Schritt

1. **Repo forken** (öffentlich lassen) in den GitHub-Account deines Nutzers. In `data/state.json` den Inhalt auf `{"clips": {}, "posts": [], "hype": {}, "meta": {}}` zurücksetzen.
2. **Twitch:** https://dev.twitch.tv/console/apps → Anwendung registrieren, Redirect `http://localhost:3000`, Kategorie „Application Integration“, Client-Typ „Vertraulich“. Client-ID + neues Client-Geheimnis notieren. (2FA vorher aktivieren, s. o.)
3. **TikTok:** Konto anlegen (Business), Profilbild/Name/Bio passend zum Thema.
4. **Buffer:** https://buffer.com/signup (eigene E-Mail), TikTok-Kanal verbinden, E-Mail bestätigen, dann https://publish.buffer.com/settings/api → „New Key“ (Ablauf 1 Jahr). Die **Kanal-ID** steht in der URL, wenn man den Kanal in Buffer öffnet (`/channels/<ID>/…`).
5. **Supabase:** Projekt → Project Settings → API Keys → Project-URL und **Secret key** (bzw. service_role). Den Bucket legt die Engine selbst an. Neue Keys (`sb_secret_…`) werden nur im `apikey`-Header geschickt – ist im Code schon so gelöst.
6. **GitHub-Secrets** (Repo → Settings → Secrets and variables → Actions). Diese Werte muss dein Nutzer selbst eintragen:
   `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `BUFFER_API_KEY`, `BUFFER_TIKTOK_CHANNEL_ID`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
7. **`config.yaml` anpassen** (siehe Abschnitt 5) und committen.
8. **Starten:** Repo → Actions. Bei einem Fork sind Actions zuerst deaktiviert → „I understand my workflows, go ahead and enable them“ klicken. Dann Workflow „clip-engine“ → „Run workflow“. Ab dann läuft es dauerhaft.
9. **Prüfen:** Im Log stehen pro Durchlauf die gefundenen Clips, „✂️ … → warteschlange“ und „📤 … → buffer_tiktok“. In Buffer unter „Sent“ und auf TikTok nachsehen.
10. Optional: eine tägliche geplante Claude-Aufgabe (günstigstes Modell reicht), die `data/state.json` auswertet und kurz berichtet.

### 5. Die wichtigsten Einstellungen (`config.yaml`)

```yaml
nur_mit_erlaubnis: false          # true = nur Streamer mit erlaubnis: true werden auto-gepostet
ausschliessen: [streamername]     # nie clippen
spiele: ["Grand Theft Auto V", "Grand Theft Auto VI"]   # exakte Twitch-Namen; leer = alle Spiele
fokus:
  streamer: [trymacs, papaplatte, eliasn97]   # bevorzugte Streamer (Ranking-Bonus)
  alle_kategorien: true           # bei Fokus-Streamern auch Just Chatting/IRL
  bonus: 2.0
auswahl:
  min_views: 10
  tages_fenster_stunden: 48
  tages_min_views: 25
  sprache: de                     # Clip-Sprache bei der Spiel-Suche
  blockwoerter: [casino, slots, stake, gamble]
posten:
  ziele: [buffer_tiktok]          # später z. B. buffer_youtube oder youtube_api
  max_pro_tag: 20
  min_abstand_min: 40
  ruhezeit: ["05:00", "07:00"]    # Europe/Berlin
laufzeit:
  max_neue_pro_lauf: 4            # so viele Clips schneidet ein Durchlauf höchstens
```

### 6. Welche Streamer? Erst messen, dann entscheiden

Im Repo gibt es eine **Analyse**: Namen in `analysis/kandidaten.txt` eintragen und committen → der Workflow „analyse“ läuft automatisch und schreibt `analysis/ergebnis.md` (Clip-Views der letzten 30 Tage, Top-Clips, häufigste Kategorien). Unser Ergebnis vom 24.11.2026 als Orientierung:

| Streamer | Clip-Views 30 Tage |
|---|---|
| iShowSpeed (EN) | 2,1 Mio |
| Kai Cenat (EN) | 2,0 Mio |
| Trymacs | 432k |
| MontanaBlack | 378k |
| Papaplatte | 298k |
| EliasN97 | 185k |
| Mert (mertabimula) | 93k |
| Marli | 79k |
| Ali Bumaye (alibumayetv) | 75k |
| CoachLim | 67k |
| Sidney (sidneyeweka) | 66k |

GTA VI ist aktuell das größte Thema. Englische Streamer sind ca. 5× viraler – dafür aber einen **eigenen** englischen Kanal nehmen, nicht mit deutschen Clips mischen. Streamer, die auf Kick oder YouTube streamen, findet diese Engine nicht (nur Twitch).

### 7. Projektaufbau

```
engine/
  __main__.py   Befehle: run | once | liste | freigeben | verwerfen | test-schnitt | buffer-kanaele
  pipeline.py   Ablauf: live → Kandidaten-Pool → Schnitt → Warteschlange → Posten → Aufräumen
  twitch.py     Helix: Streams, Clips (je Streamer/Spiel), Spiele, Create Clip
  discover.py   Fokus-Streamer, Spiel-Live-Streams (Sprache de)
  scoring.py    Auswahl + Duplikat-Filter (gleicher VOD-Moment)
  download.py   yt-dlp (offizieller Download nur als Editor des Streamers)
  edit.py       ffmpeg-Layouts (blur = ganzes Bild, split = Facecam oben, crop), Lautheit −14 LUFS
  captions.py   faster-whisper → ASS-Untertitel, Titel, Credit
  texts.py      Caption, Hashtags, Aufhänger für schwache Titel
  scheduler.py  Slots, Ruhezeit, Frische
  storage.py    Supabase Storage
  publish/      buffer.py, tiktok.py (offizielle API, Inbox-Entwürfe), youtube.py
scripts/analyse.py      Streamer-Analyse
.github/workflows/      engine.yml (Dauerlauf), analyse.yml
tests/                  python -m pytest -q tests
```

### 8. Regeln für dich, Claude

- API-Keys und Passwörter trägt **dein Nutzer selbst** ein (GitHub-Secrets, Logins). Du gibst ihm die Werte bzw. Links.
- Vor jeder Code-Änderung `python -m pytest -q tests`, nur committen, wenn grün.
- Beim ersten Lauf das Log genau ansehen: Werden Clips gefunden? Wird hochgeladen? Kommt der Post bei Buffer an?
- Löschen auf TikTok immer erst bestätigen lassen (endgültig nach 30 Tagen).
