# Twitch Clip Engine

Zieht automatisch die besten Momente großer deutscher Twitch-Streamer, schneidet sie ins Hochformat (1080×1920, Untertitel, Titel, Credit) und postet sie auf **TikTok** (Schwerpunkt) und **YouTube Shorts**. Laufende Kosten: **0 €**.

```
 Twitch-API (kostenlos)                       Schnitt (lokal)                     Posten
┌──────────────────────┐   ┌──────────┐   ┌───────────────────────┐   ┌──────────────────────────┐
│ Wer ist live?        │──▶│ Download │──▶│ ffmpeg 9:16 + Whisper │──▶│ Warteschlange / Freigabe │
│ Community-Clips      │   │ offiziell│   │ Untertitel, Titel,    │   │ Slots, Ruhezeit, Limits  │
│ Chat-Hype → Clip     │   │ / yt-dlp │   │ Credit, −14 LUFS      │   │ Buffer → TikTok, YouTube │
└──────────────────────┘   └──────────┘   └───────────────────────┘   └──────────────────────────┘
```

## So findet die Engine Clips

1. **Community-Clips (Standard):** Während ein Streamer live ist, fragt die Engine alle 5 Min. seine neuen Clips ab. Zuschauer clippen genau die lustigen Momente, also ist das das beste Viralitäts-Signal. Bewertet wird nach *Views pro Minute seit Erstellung*, normiert auf die Zuschauerzahl. Doppelte Clips desselben Moments werden aussortiert.
2. **Live-Hype (optional, `hype.aktiv: true`):** Die Engine liest den Chat anonym mit. Explodiert der Chat (z. B. KEKW-Welle, 3,5× über Durchschnitt), erstellt sie über dein Twitch-Konto **selbst** einen Clip der letzten 45 s. Das ist der echte „Live“-Modus. Braucht einmalig `auth-twitch`.
3. **Streamer-Quelle:** feste Liste in `config.yaml` oder zusätzlich `quelle.modus: top_de` = automatisch die größten deutschsprachigen Live-Streams.

## Drei Punkte, die du vor dem Start kennen musst

**1. Rechte.** *(Aktuelle Einstellung: `nur_mit_erlaubnis: false` – es wird alles automatisch gepostet, MontanaBlack ist über `ausschliessen` komplett ausgenommen.)* Die Clips gehören den Streamern. Ohne Erlaubnis riskierst du Copyright-Meldungen und Konto-Sperren (vor allem bei Musik im Stream). Deshalb gilt standardmäßig `nur_mit_erlaubnis: true`: Clips von Streamern ohne `erlaubnis: true` werden zwar geschnitten, landen aber in der **Freigabe** statt automatisch online zu gehen. Viele große Streamer haben offizielle Clip-Programme (Discord, Management) oder bezahlen Clipper sogar pro 1.000 Views über Kampagnen-Plattformen wie Whop Content Rewards. Das ist gleichzeitig der realistischste Weg, damit Geld zu verdienen: Reupload-Kanäle sind bei TikTok Creator Rewards und im YouTube-Partnerprogramm meist nicht monetarisierbar („reused content“).
Bonus: Macht dich ein Streamer zum **Editor**, nutzt die Engine den offiziellen Twitch-Download – bei Dual-Format-Streams direkt im Hochformat.

**2. TikTok-Posting.** Eigene TikTok-Apps posten ohne Audit nur privat. Deshalb drei Wege:

| Ziel | Öffentlich? | Kosten | Aufwand |
|---|---|---|---|
| `buffer_tiktok` **(Empfehlung)** | ja, sofort | Buffer Free (3 Kanäle) | API-Key + Kanal-ID |
| `tiktok_api` mit `inbox` | ja, nach 1 Tipp in der App | 0 € | eigene TikTok-Dev-App, max. ~5 Entwürfe/Tag |
| `tiktok_api` mit `direct` | erst nach TikTok-Audit | 0 € | Audit (Tage bis Wochen, Reposts fremder Clips fallen evtl. durch) |

**3. YouTube.** Gleiches Problem: Uploads aus nicht geprüften Google-Projekten bleiben privat. Mit `buffer_youtube` umgehst du das. Falls Amalis Uploads über das Projekt „Amali Social“ privat ankommen: genau deshalb.

> Für dieses Projekt einen **eigenen Buffer-Account** anlegen (eigene E-Mail), damit die 3 Gratis-Kanäle nicht mit Amali geteilt werden.

## Einrichtung (ca. 20 Min.)

1. **Twitch-App** anlegen: https://dev.twitch.tv/console/apps → Redirect `http://localhost:3000`, Kategorie „Application Integration“. Client-ID und Secret in `.env`.
2. **TikTok- und YouTube-Kanal** anlegen (Business-Konto bei TikTok) und in einem neuen **Buffer**-Account verbinden.
3. Buffer → Settings → API → Key erzeugen, in `.env` als `BUFFER_API_KEY`.
4. Supabase: `SUPABASE_URL` und `SUPABASE_SERVICE_KEY` (Service-Role) in `.env`. Der Bucket `clips` wird automatisch öffentlich angelegt, Videos werden nach 48 h gelöscht.
5. ```bash
   cp config.example.yaml config.yaml && cp .env.example .env   # ausfüllen
   pip install -r requirements.txt                              # + ffmpeg installiert
   python -m engine buffer-kanaele                              # IDs in .env eintragen
   python -m engine test-schnitt beispiel.mp4 papaplatte        # Schnitt prüfen
   python -m engine once                                        # ein echter Durchlauf
   ```
6. Optional Live-Hype: `python -m engine auth-twitch`, dann `hype.aktiv: true`.

## Betrieb

**Server (empfohlen, alle Funktionen inkl. Live-Hype):**
```bash
docker compose up -d --build
docker compose logs -f
```
Braucht ca. 2 CPU-Kerne und 2–3 GB RAM (Whisper `small`). Läuft auf jedem vorhandenen VPS. Gratis-Alternative: Oracle Cloud „Always Free“ (ARM, 4 Kerne, 24 GB).

**Ohne Server:** `.github/workflows/engine.yml` startet alle 20 Min. einen Durchlauf auf GitHub Actions (Secrets wie in `.env` anlegen). Bei privatem Repo reichen die Freiminuten nicht, dann Server nehmen. Live-Hype geht hier nicht.

## Befehle

| Befehl | Zweck |
|---|---|
| `python -m engine run` | Dauerbetrieb |
| `python -m engine once` | ein Durchlauf |
| `python -m engine liste` | Freigabe, Warteschlange, Gepostetes, Fehler |
| `python -m engine freigeben <id>` | Clip aus der Freigabe zum Posten freigeben |
| `python -m engine verwerfen <id>` | Clip verwerfen |
| `python -m engine test-schnitt <datei> [login] [layout]` | Schnitt testen |
| `python -m engine auth-twitch / auth-tiktok / auth-youtube` | Tokens einrichten |

Mit `NOTIFY_DISCORD_WEBHOOK` meldet die Engine neue Freigaben und Fehler in einen Discord-Kanal.

## Layouts

- `blur` (Standard): Spielbild mittig, leicht gezoomt, unscharfer Hintergrund, Titel oben, Untertitel unten.
- `split`: Facecam oben (1/3), Gameplay unten. Pro Streamer `facecam: [x, y, breite, höhe]` relativ zum 16:9-Bild angeben, z. B. Facecam oben links mit 25 % Breite: `[0.0, 0.0, 0.25, 0.3]`. Die Werte am besten an einem Screenshot ablesen.
- `crop`: einfacher Mittelausschnitt (gut für Just Chatting).

Untertitel entstehen lokal mit faster-whisper (Deutsch, Wort für Wort gelb hervorgehoben). Texte bleiben links der TikTok-Buttons und über der Caption-Zone.

## Einstellungen, die du anfangs anpassen solltest

- `posten.max_pro_tag` (8) und `min_abstand_min` (75): TikTok verträgt viel, aber neue Konten lieber langsam starten (3–5/Tag in Woche 1).
- `auswahl.min_views` / `min_views_pro_minute`: höher = weniger, dafür stärkere Clips.
- `auswahl.blockwoerter`: Glücksspiel-Clips (Casino, Slots) sperren TikTok und YouTube schnell.

## Aufbau

```
engine/
  __main__.py   Kommandozeile
  pipeline.py   Ablauf: live → Clips → Schnitt → Warteschlange → Posten → Aufräumen
  twitch.py     Helix-API (Streams, Clips, Create Clip, offizieller Download)
  discover.py   Streamer-Liste + Top-DE-Entdeckung
  scoring.py    Clip-Bewertung und Duplikat-Filter
  hype.py       Chat-Ausbruchserkennung (anonymes IRC)
  download.py   offizieller Download / yt-dlp
  edit.py       ffmpeg-Layouts, Lautheit
  captions.py   Whisper → ASS-Untertitel, Titel, Credit
  texts.py      Caption, Hashtags, YouTube-Titel
  scheduler.py  Slots, Ruhezeit, Frische
  storage.py    Supabase Storage (öffentliche URL)
  publish/      buffer.py, tiktok.py, youtube.py
tests/          python -m pytest
```
