# Deployment auf Streamlit Community Cloud (gratis)

URL nach Deploy: `https://<dein-app-name>.streamlit.app/?token=DEIN_APP_TOKEN`

## Vorbereitung (einmalig)

1. **GitHub-Repo muss aktuell sein** — alles gepusht (`git push`)
2. **App-Token wählen** (z.B. 16 zufällige Zeichen, das ist dein "URL-Passwort"):
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(16))"
   ```
   → z.B. `xK9_aB2nQpQ8AAEF9kL3oN8m7Q5RvW2T1uYg`

## Schritte

### 1. Bei Streamlit Cloud einloggen
- https://share.streamlit.io → **Continue with GitHub**
- Du erlaubst Streamlit Zugriff auf dein Repo (read-only für public, read/write für private)

### 2. Neue App erstellen
- **"Create app"** oben rechts → **"Yup, I have an app"**
- Felder ausfüllen:
  - **Repository:** `marcoz9779/ai-invest-radar`
  - **Branch:** `main`
  - **Main file path:** `app.py`
  - **App URL (custom):** dein Wunsch-Name, z.B. `marco-radar` → `https://marco-radar.streamlit.app`

### 3. Secrets eintragen (vor dem ersten Deploy)
- Im Setup-Dialog: **"Advanced settings"** öffnen
- Im **Secrets**-Textfeld diesen Block einfügen (Werte aus deiner `.env`):

```toml
MARKETAUX_API_KEY = "dein-marketaux-key"
FINNHUB_API_KEY = "dein-finnhub-key"
ANTHROPIC_API_KEY = "dein-anthropic-key"

REDDIT_USER_AGENT = "ai-invest-radar/0.1 by /u/TrackTraditional6354"

TELEGRAM_BOT_TOKEN = "dein-telegram-bot-token"
TELEGRAM_CHAT_ID = "deine-chat-id"
```

> **Optional — URL-Schutz:** Wenn du die App nicht öffentlich willst, füge
> `APP_TOKEN = "irgendein-geheimwort"` hinzu. Dann ist sie nur via
> `?token=irgendein-geheimwort` in der URL erreichbar. Ohne die Zeile = öffentlich.

⚠️ Marketaux/Finnhub/Anthropic/Telegram-Keys ggf vorher regenerieren wenn sie im
Chat aufgetaucht sind.

### 4. Deploy
- **"Deploy"** klicken → Streamlit baut die App (~2-5 Min beim ersten Mal)
- Wenn fertig: URL aufrufen mit `?token=DEIN_APP_TOKEN`
- Beispiel: `https://marco-radar.streamlit.app/?token=xK9_aB2nQpQ8AAEF9kL3oN8m7Q5RvW2T1uYg`

### 5. Bookmark setzen
- Den kompletten Link inkl. `?token=...` als Lesezeichen speichern.
- Wer den Token nicht hat, sieht nur eine "Zugriff verweigert"-Seite.

## Updates ausrollen

Jeder `git push` auf `main` triggert automatisch einen Rebuild (~30s).

## Sleeping-Behaviour

Free-Tier-Apps schlafen nach **~7 Tagen Inaktivität**. Erster Aufruf danach
bootet ~30-60s. Bei regelmäßiger Nutzung (mehrmals pro Woche) bleibt sie wach.

Tipp: cron-job.org pingt unsere App eh laufend (alle 30 Min Watcher) — die App
bleibt damit immer warm.

## Resource-Limits (Free)

- **1 GB RAM** pro App
- **800 MB disk**
- **1 concurrent app** (für mehrere müsstest du Streamlit-Pro nehmen)

Unser Tool nutzt typisch ~300 MB RAM — passt.

## Troubleshooting

| Symptom | Lösung |
|---------|--------|
| "Pip install failed" beim Build | requirements.txt prüfen, vielleicht eine zu strikte Version-Pin |
| "Module not found" | requirements.txt fehlt was — push die fehlende Library |
| Schwarze Seite, kein Output | Logs ansehen (Manage app → Logs) |
| Tokens werden nicht erkannt | Secrets-Format prüfen: `KEY = "wert"` mit Anführungszeichen |
