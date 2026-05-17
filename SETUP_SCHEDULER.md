# Scheduler-Setup: cron-job.org → GitHub Actions

Dieses Setup triggert deine Telegram-Alerts zeitgesteuert in der Cloud — der Mac kann ausgeschaltet sein.

## Schritt 1: GitHub Secrets eintragen

Öffne im Repo: **Settings → Secrets and variables → Actions → New repository secret**

Trag diese Secrets ein (Werte aus deiner `.env`):

| Secret-Name | Wert |
|-------------|------|
| `MARKETAUX_API_KEY` | Marketaux-Token |
| `FINNHUB_API_KEY` | Finnhub-Token |
| `CRYPTOPANIC_API_KEY` | (optional, falls vorhanden) |
| `TELEGRAM_BOT_TOKEN` | BotFather-Token |
| `TELEGRAM_CHAT_ID` | deine Chat-ID (`1658310876`) |
| `REDDIT_USER_AGENT` | `ai-invest-radar/0.1 by /u/TrackTraditional6354` |

Optional (alle haben sinnvolle Defaults):
- `NEWSAPI_KEY`
- `ANTHROPIC_API_KEY`
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`

## Schritt 2: Personal Access Token erstellen

cron-job.org muss GitHub authentifizieren. Erstelle einen klassischen PAT:

1. https://github.com/settings/tokens → **Generate new token (classic)**
2. Name: `cron-job.org Alerts Trigger`
3. Expiration: 1 year
4. Scope: nur `repo` (Full control)
5. **Generate** → Token kopieren (du siehst ihn nur einmal)

## Schritt 3: GitHub Workflow erstmals manuell testen

1. Im Repo: **Actions → "AI Invest Radar — Telegram-Alerts"**
2. Rechts oben: **Run workflow** (Dropdown)
3. Wähle `mode: test` → **Run**
4. Nach ~1 Min sollte eine Test-Nachricht im Telegram landen ✓

Wenn nicht: prüfe die Logs (rotes X anklicken) auf Secret-Fehler.

## Schritt 4: cron-job.org einrichten

Für jede gewünschte Zeit erstellst du **einen Cronjob** in cron-job.org:

### Job 1: Morning-Digest (08:00 CH)

- **Title:** `AI Invest Radar — Morning Digest`
- **URL:** `https://api.github.com/repos/marcoz9779/ai-invest-radar/dispatches`
- **Schedule:** täglich, **08:00**, Timezone: `Europe/Zurich`
- **Request Method:** `POST`
- **Headers** (in Advanced):
  - `Authorization: Bearer <dein-PAT>`
  - `Accept: application/vnd.github+json`
  - `X-GitHub-Api-Version: 2022-11-28`
- **Request Body** (JSON):
  ```json
  {"event_type": "morning-digest"}
  ```
- **Save**

### Job 2: Pre-Market Diff (15:00 CH)

Identisch wie Job 1, aber:
- **Title:** `AI Invest Radar — Pre-Market Diff`
- **Schedule:** **15:00**, Europe/Zurich
- **Body:** `{"event_type": "premarket-diff"}`

### Job 3: Open-Reaction Diff (16:00 CH)

- **Title:** `AI Invest Radar — US Open Diff`
- **Schedule:** **16:00**, Europe/Zurich
- **Body:** `{"event_type": "open-diff"}`

### Job 4: Watcher (alle 30 Min, 24/7)

- **Title:** `AI Invest Radar — Live Watcher`
- **Schedule:** **alle 30 Minuten** (Custom: `*/30 * * * *`), Europe/Zurich
- **Body:** `{"event_type": "watcher"}`

> 💡 cron-job.org Free Tier erlaubt 5-Minuten-Intervall — 30 Min ist locker drin.

## Wie es zusammen funktioniert

```
cron-job.org (Zeit-Trigger, CH-Zeit native)
       │ HTTP POST (PAT-authentifiziert)
       ▼
GitHub API (repository_dispatch)
       │
       ▼
GitHub Actions Workflow (.github/workflows/alerts.yml)
       │ Python + Secrets
       ▼
alerts.py [--digest | --watcher | (diff)]
       │
       ▼
Telegram Bot → dein Handy 📱
```

## Diff-State zwischen Runs

GitHub Actions ist stateless. Damit `last_signals.json` und `last_watcher.json` zwischen Runs überleben (Throttle, "nur neue Signale"), nutzt der Workflow **actions/cache** automatisch.

## Schnell-Test

Manueller Test ohne cron-job.org:

```bash
# Action manuell triggern via gh-CLI:
gh api repos/marcoz9779/ai-invest-radar/dispatches \
  -f event_type=test \
  -X POST

# Oder via Actions-Tab → Run workflow → mode: test
```

## Anpassungen später

- **Schwellwerte** für Watcher (Volume/News): in `alerts.py` oben — `VOLUME_SPIKE_THRESHOLD`, `NEWS_VELOCITY_THRESHOLD`
- **Throttle** (gleicher Ticker max 1 Alert / Xh): `WATCHER_THROTTLE_HOURS`
- **Subreddits / Aktien-Universum**: in `main.py`
