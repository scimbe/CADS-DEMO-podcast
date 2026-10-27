# explainer-video: Vertrag fuer die Galerie

Demo "Erklaervideo aus deinem Text": aus dem eigenen Text (Einfuegen oder .txt/.md/.pdf) entsteht ein
kurzes Video mit Folien, Untertiteln und der Stimme "Thomas". Dieses Bundle ersetzt die Karten
`podcast` und `explainer`. `server.mjs` und die Seiten gehoeren der Galerie; hier steht nur, wie sie das Bundle aufruft.

## Aufruf

```
./run.sh <config.json|-> [out_dir]        # out_dir Standard: ./out (wird angelegt)
```

Pro Auftrag ein eigenes `out_dir` (z. B. Arbeitsverzeichnis der Sitzung). Kein Zustand zwischen Laeufen.
Die Galerie liest `.pdf`/`.txt`/`.md` selbst aus (z. B. `pdftotext`) und uebergibt nur Text.

### Konfiguration (JSON, stdin oder Datei)

| Feld | Typ | Pflicht | Grenzen |
|------|-----|---------|---------|
| `text` | string | ja | 40..4000 Zeichen (haerter Abbruch `text_too_long` / `text_too_short`) |
| `title` | string | nein | max. 100 Zeichen; sonst Titel aus dem LLM-Skript |
| `length` | 30 \| 60 \| 90 | nein (60) | Zielsekunden; tatsaechlich ca. 85-100 %, hartes Limit 120 s |
| `lang` | "de" \| "en" | nein ("de") | siehe "Sprache" |

Unbekannte Felder werden abgelehnt (Exit 2). Text wird als UTF-8 (NFC) gelesen; Umlaute und ß bleiben von Skript über Folien und Untertitel bis zur Sprachausgabe erhalten (Vergleiche im Belegcheck falten ä/ö/ü/ß nur intern). Der Text ist Daten: Anweisungen darin werden nicht befolgt.

## Ausgabe auf stdout (Fortschritt, eine Zeile je Ereignis)

```
PROGRESS {"stage":"script","pct":15,"message":"LLM-Skript, Versuch 1 (local-devstral-small2)"}
RESULT   {"title":"...","duration_s":56.36,"elapsed_s":96.5,"warnings":[],"files":["video.mp4","subtitles.vtt","script.json","poster.jpg","meta.json"]}
ERROR    {"code":"script_invalid","message":"..."}
```

- `stage`: `config` (2) -> `script` (8-30) -> `scenes` (30-88, je Szene Sprache + Folien + Clip, parallel) -> `encode` (Schluss: Tonmaster, Lautheit, MP4) -> `done` (100). `pct` steigt monoton.
- Genau eine Abschlusszeile: `RESULT` (Exit 0) oder `ERROR` (Exit ungleich 0). Zeilen auf stderr sind Diagnose, nie an Nutzer weiterreichen.
- SSE-Abbildung: jede Zeile `PROGRESS {json}` -> `event: progress\ndata: {json}`, `RESULT` -> `event: done`, `ERROR` -> `event: error`.

Fehlercodes (`ERROR.code`, Exit): `invalid_config` / `text_too_long` / `text_too_short` (2), `llm_not_configured` / `llm_unreachable` / `script_invalid` (3), `tts_not_configured` / `tts_failed` / `tts_lang_unavailable` (4), `tools_missing` / `font_missing` / `mix_failed` (5), `timeout` (6), `internal` (1).
`script_invalid` heisst: auch nach Wiederholungen (2 Modelle x 3 Versuche) kein Skript, das die Belegpruefung besteht; Meldung nennt die Befunde.

## Ausgabedateien (in `out_dir`)

| Datei | Inhalt |
|-------|--------|
| `video.mp4` | H.264 1920x1080 25 fps + AAC mono 44,1 kHz, -16 LUFS, `+faststart` (im Browser abspielbar, herunterladbar) |
| `subtitles.vtt` | WebVTT, satzweise; als `<track kind="subtitles">` einbinden (nicht eingebrannt) |
| `poster.jpg` | 1280x720, letzte Stufe der ersten Folie; als `poster=` |
| `script.json` | `{title, lang, voice, scenes:[{headline,bullets[2-4],narration,sources[],claims[]}]}`; `claims` sind die woertlichen Quellsaetze aus dem Nutzertext |
| `meta.json` | Dauer, Szenen, Woerter, Modell/Versuche, Lautheit, Zeiten je Stufe, `warnings`, `stub` |

## Grenzen und Zeitlimits

Text 4000 Zeichen; Video max. 120 s (ueberlange Folgen verlieren Schlussszenen, `warnings`); 4-8 Szenen;
Gesamtlimit `EVGEN_TOTAL_TIMEOUT` (240 s), Szenen-Worker `EVGEN_WORKERS` (4), x264-Preset `EVGEN_X264_PRESET` (veryfast); LLM 150 s je Aufruf; TTS 60 s je Satzblock; ffmpeg 120-240 s je Schritt.
Empfehlung: hoechstens 1 laufender Auftrag je Sitzung und 2 gleichzeitig insgesamt (TTS-Node serialisiert, Piper-Queue max. 5).

## Grounding (keine erfundenen Fakten)

Der Text wird in nummerierte Saetze zerlegt; das LLM nennt je Szene die Quellsaetze (`sources`). Code lehnt ein Skript ab (mit Rueckmeldung, neuer Versuch), wenn: Szenenzahl nicht 4-8, Satznummer ungueltig, Zahl in Szene fehlt in den genannten Saetzen, mehr als 50 % der Inhaltswoerter (>= 7 Buchstaben) nicht in den genannten Saetzen, Erzaehltext ausserhalb 70-115 % des Zielumfangs. Gibt der Text weniger her, wird das Video kuerzer (`warnings`) statt aufgefuellt. Die Pruefung ist heuristisch (Zahlen- und Wortstamm-Abgleich) und ersetzt keine Fachpruefung.

## Stimme "Thomas" und TTS-Pfad

"Thomas" ist die digitale Empfangskraft aus der Salon-Demo (`cloudflare-worker`, `src/lib/prompt.ts`); ihre Stimme ist
**Piper `de_DE-thorsten-high`** (Datei `~/llm-node/piper-voices/` auf dem M4-Node, `piper-serve.py` auf 127.0.0.1:8096), erreichbar ueber den
ct-agent-Channel-Dienst `audio_generation`, Stimme `primary` (Standard; `interviewer` waere die geklonte CosyVoice2-Stimme und wird bewusst nicht benutzt).
Der Node ist nur ueber Loopback/Channel erreichbar; es werden keine Ports geoeffnet.

Backend per `EVGEN_TTS_BACKEND`:

- `channel` (Produktion, Standard): persistenter `ct-agent channel`-Prozess, Aufruf `{"text":..., "voice":"primary"}` je stdin-Zeile, Antwort `{"ok":true,"output":"https://site-f29c55da.bunsenbrenner.org/audio/<id>.wav"}`, WAV wird abgeholt (TTL 15 min). Gleicher Mechanismus wie `gui/lib/channel.mjs` der Callcenter-Demo.
  Deploy-Schritt auf dem Galerie-Host: `CT_AGENT_BIN`, `CT_RELAY_ENV` (Datei mit `CT_CHANNEL_GRANT_2E`, `CT_CHANNEL_HOLDER_KEY`, `CT_CHANNEL_NOISE_KEY`, `CT_CHANNEL_FRONT_DOOR_CERT`) und `CT_AUDIO_CHANNEL_ID` setzen (dieselben Werte wie fuer die Callcenter-Demo). Diese Anbindung ist hier **nicht live getestet**, weil auf dem Entwicklungsrechner kein ct-agent/Relay-Env vorhanden ist; die Stimme selbst wurde ueber `ssh` gegen denselben `piper-serve`-Endpunkt getestet.
- `ssh` (nur Entwicklung): `EVGEN_TTS_SSH=user@host` ruft `127.0.0.1:8096/synthesize` per SSH auf. `http`: `EVGEN_TTS_URL`. `stub`: Testtoene (Selbsttest).
- LLM: `LLM_BASE_URL`, `LLM_API_KEY` oder `LLM_API_KEY_FILE`, `LLM_MODEL` (+ optional `EVGEN_LLM_FALLBACK_MODEL`). Schluessel werden nie geloggt.

### Sprache

`lang:"en"` liefert `tts_lang_unavailable`, solange keine englische Stimme erreichbar ist: der Channel bedient nur die deutsche Thorsten-Stimme.
Auf dem Node liegt `en_US-lessac-medium.onnx`, aber kein Dienst dafuer; noetig waere dort ein zweiter `piper-serve` plus Channel-Stimme (Aenderung am Produktionsnode, nicht Teil dieses PRs). Die Galerie sollte EN bis dahin ausblenden.

## Abhaengigkeiten im Galerie-Image

`python3` (nur stdlib), `ffmpeg`/`ffprobe` mit libx264, aac, `imagemagick` (`magick` oder `convert`), eine TTF-Schrift: DejaVu
(`fonts-dejavu-core`, Pfad `/usr/share/fonts/truetype/dejavu/`) oder Liberation; sonst `EVGEN_FONT`/`EVGEN_FONT_BOLD`. Kein Chromium. Bitte `fonts-dejavu-core` im Dockerfile ausdruecklich aufnehmen (heute nur indirekt ueber fontconfig).

## Messwerte (Testlauf, 60-s-Ziel, deutscher Beispieltext, 6 Szenen)

Video 56,4 s, 1920x1080 H.264 + AAC, -16,3 LUFS, Wanduhr 96,5 s auf einem Rechner mit Last 60 (CPU-Zeit 45 s); Stufen: Skript 26,8 s (3 Versuche), Folien 18,9 s parallel zu TTS 32,7 s, Encode 34,4 s.
Das 90-s-Ziel ist unter dieser Last knapp verfehlt; auf einem freien Host ist die CPU-Zeit (ca. 45 s) die untere Schranke. Hebel: Channel statt SSH (p50 ca. 1,9 s je Aufruf), kuerzere Skripte.
