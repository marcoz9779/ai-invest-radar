# AI Invest Radar

Research- und Alert-Tool für US-Aktien und Krypto. Fokus: Daten aus mehreren öffentlichen Quellen bündeln, technische und fundamentale Signale berechnen, Sentiment aus News ziehen und tägliche Empfehlungen liefern.

> **Disclaimer:** Dieses Tool ist ein Research-Assistent, keine Anlageberatung. Es garantiert keinen Gewinn. Jede Trade-Entscheidung triffst du selbst und auf eigenes Risiko.

## Quickstart

```bash
# 1. Virtual Environment
python -m venv venv
source venv/bin/activate          # macOS/Linux
# venv\Scripts\activate            # Windows

# 2. Dependencies
pip install -r requirements.txt

# 3a. CLI-Lauf (Terminal-Report)
python main.py

# 3b. Web-Dashboard (öffnet http://localhost:8501)
streamlit run app.py
```

## Was das aktuell tut

**Universum (Phase 1):**
- 40 US-Aktien (kuratierte Marketcap-Top-40, alle Swissquote-handelbar)
- 40 Kryptos dynamisch via CoinGecko (immer aktuelle Marketcap-Liste, ohne Stablecoins/Wrapped-Tokens)
- Bulk-OHLC-Fetching via `yfinance.download(multi-ticker)` → 40 Aktien in ~5s statt ~40s
- Indikatoren: **RSI**, **MACD**, **SMA20/SMA50**
- Transparenter, regelbasierter Score je Asset

**News (Phase 2 + 4) – 7 Quellen parallel + dedupliziert:**
- **Marketaux** (primär, $19-$29/Monat) – kuratierte Finanz-News mit Sentiment-Score
- **Finnhub** (gratis als Zusatz-Layer)
- **Yahoo Finance** (gratis via yfinance)
- **Google News RSS** (gratis)
- **StockTwits** (gratis, Aktien) – Retail-Trader-Sentiment mit explizitem Bullish/Bearish-Tag
- **CryptoPanic** (gratis Plan, Krypto) – kuratierte News mit Community-Bull/Bear-Votes
- **Global RSS-Top-Stories** – MarketWatch, Yahoo, CNBC Markets, CNBC Investing, Reuters Business
- Headlines werden nach Titel-Hash dedupliziert; Sentiment von Marketaux wird priorisiert
- **Bullish/Bearish-Ratio** wird aus allen sentiment-getaggten Quellen berechnet

**Reddit-Buzz (Phase 2.5):**
- 10 Subreddits für Aktien (wallstreetbets, stocks, investing, StockMarket, options, ValueInvesting, SecurityAnalysis, dividends, pennystocks, Daytrading)
- 8 Subreddits für Krypto (CryptoCurrency, CryptoMarkets, Bitcoin, ethereum, altcoin, defi, CryptoTechnology, SatoshiStreetBets)
- **Parallele Fetches** für 80 Tickers gleichzeitig (`concurrent.futures`, ~5s statt ~25s)
- **Mention-Velocity-Indikator** (24h-Rate vs 7d-Durchschnitt) zeigt Hype-Spikes
- Läuft **anonym ohne Account** via public JSON-Endpoint

**Dashboard (Phase 2.7 – "Non plus ultra"-Build):**
- **Market-Context-Bar** im Header: Fear & Greed Krypto + stärkster/schwächster Sektor (5d)
- **Treemap-Heatmap** aller 80 Assets — Fläche = Score-Stärke, Farbe = Empfehlung
- **Top-5-Empfehlungen-Karten** mit Logo, Earnings-Badge und Reddit-Spike-Indikator
- Pro Ticker: Logo, Sparkline, BUY/WATCH/HOLD/REDUCE/SELL-Badge
- **Earnings-Badge** "📅 Earnings in 3d" wenn innerhalb der nächsten 14 Tage
- **Reddit-Spike-Badge** "🔥 Spike 3.2x" bei Velocity ≥ 2
- **TradingView-Widget** im Expander (interaktiver Pro-Chart mit RSI & MACD)
- News (4 Quellen + dedup) und Reddit-Buzz unter jedem Ticker
- **Claude AI-Sentiment-Fusion** im Expander (wenn ANTHROPIC_API_KEY gesetzt)
- Sidebar: Empfehlungs-Filter, Min-Score-Slider, vollständige Sektoren-Performance

**Score-Modell (erweitert):**
- RSI (oversold/overbought)
- MACD-Cross (bullish/bearish)
- SMA20/50-Konfluenz (Auf-/Abwärtstrend)
- **Volume-Spike** (heute vs 20d-Ø, >2x = +1) — jetzt auch für Krypto via Binance
- **52w-Position** (nahe Tief = oversold-Bonus, nahe Hoch = Risiko)
- **Insider-Käufe/Verkäufe** in den letzten 30 Tagen (Finnhub free, ≥2 Käufe = +1)
- **Reddit-Velocity-Boost** (24h-Spike kann WATCH→BUY anheben)
- **Earnings-Bonus** (anstehende Earnings boosten WATCH→BUY bei Score ≥ +1)
- **AI-Sentiment-Override** (Claude kann Label um 1 Stufe verschieben)

**Watchlist (Phase 2.9):**
- "☆ Watch"-Button auf jeder Ticker-Karte
- Persistiert lokal in `watchlist.json` (gitignored)
- Separater Tab zeigt nur die gestarteten Assets

**Backtest (Phase 3.5):**
- 90-Tage P&L-Simulation pro Ticker
- Strategie: BUY bei Score ≥ +3, SELL bei Score ≤ -3
- Vergleicht gegen Buy-and-Hold (Alpha-Metrik)
- Equity-Curve + Trade-Historie als Tabelle

**Multi-Signal-Pattern (Phase 4):**
- **STRONG BUY** wenn ≥3 unabhängige Signale konvergieren:
  Tech-Score, Reddit-Spike, Insider-Käufe, News-Bullish-Ratio
- **STRONG SELL** symmetrisch (Tech-Score, Insider-Verkäufe, News-Bearish)
- Nur STRONG-Pattern triggern Telegram-Alerts → kein Spam
- Reasons werden auf der Karte angezeigt ("Tech-Score+3, Insider-Käufe, News 80% bullish")

**Telegram-Alerts (Phase 5):**
- `alerts.py` als CLI-Script (auch für cron/scheduler)
- `python alerts.py --test` → Test-Nachricht
- `python alerts.py` → Diff-Alerts (nur neue STRONG-Signale)
- `python alerts.py --digest` → Tägliche Morning-Zusammenfassung
- `last_signals.json` speichert vorherigen Stand (gitignored)
- Im Dashboard: Sidebar-Buttons für Morning-Digest + Test-Alert

**Datenquellen — Übersicht:**
| Daten | Provider | Kosten |
|-------|----------|--------|
| Aktien-OHLC (bulk) | yfinance | gratis |
| Aktien-Fundamentaldaten | yfinance | gratis |
| Aktien-Earnings | yfinance | gratis |
| Aktien-Insider-Trades | Finnhub | gratis (60/min) |
| Sektoren-ETFs | yfinance | gratis |
| Krypto-Marketcap-Ranking | CoinGecko | gratis (1 Call/Run) |
| Krypto-OHLCV | **Binance Public API** | gratis, **1200/min** |
| News (Sentiment) | Marketaux | $19-29/Monat |
| News (Fallback) | Yahoo Finance + Google News RSS | gratis |
| Reddit-Buzz | public JSON-Endpoint | gratis (anonym) |
| Fear & Greed Krypto | alternative.me | gratis |
| AI-Sentiment | Anthropic Claude Haiku | ~$3-5/Monat |

## Roadmap

| Phase | Inhalt | Status |
|-------|--------|--------|
| 1 | Daten + technische Indikatoren + Score (40 Aktien) | ✅ |
| 1.5 | Krypto-Universe Top 40 dynamisch (jetzt mit Binance OHLCV + Volume) | ✅ |
| 2 | News-Aggregation (Marketaux + Yahoo + Google + Finnhub, dedup) | ✅ |
| 2.5 | Reddit-Buzz (10+8 Subreddits, parallel, mit Velocity) | ✅ |
| 2.7 | Streamlit-Dashboard (Treemap, Logos, Sparklines, TradingView) | ✅ |
| 2.8 | Market-Context (Fear&Greed + Sektoren-ETFs) | ✅ |
| 2.9 | Earnings + Insider + Fundamentaldaten + Watchlist | ✅ |
| 3 | Claude AI-Sentiment-Fusion (Hook im Code) | ✅ Code drin, Key optional |
| 3.5 | Backtest-Engine (90-Tage P&L, Alpha vs Buy-and-Hold) | ✅ |
| 4 | Multi-Signal-Pattern (STRONG BUY/SELL) + News-Sentiment-Ratio | ✅ |
| 4.5 | News-Tiefe: StockTwits + CryptoPanic + RSS-Top-Stories | ✅ |
| 5 | Telegram-Alerts (Diff + Morning-Digest, alerts.py CLI) | ✅ |
| 6 | SQLite-Storage + History-Charts | offen |
| 7 | Swissquote-Whitelist als Filter | offen |

## Universum

**40 US-Aktien (kurated, Marketcap-sortiert, Swissquote-handelbar):**
NVDA, MSFT, AAPL, AMZN, GOOGL, META, AVGO, TSLA, BRK-B, LLY, JPM, V, WMT, XOM, MA, UNH, ORCL, COST, JNJ, PG, NFLX, HD, BAC, ABBV, CRM, KO, CVX, AMD, MRK, PEP, ADBE, ACN, CSCO, TMO, MCD, LIN, IBM, PLTR, COIN, INTC

**40 Kryptos (dynamisch via CoinGecko Top-Marketcap):**
Wird bei jedem Lauf live geholt — Stablecoins, gold-backed Tokens (PAXG/XAUT) und Wrapped-Varianten (WBTC/WETH/STETH) sind ausgefiltert. So bleibt das Universum automatisch aktuell.

In Phase 7 ersetzen wir die statische Aktien-Whitelist durch einen automatischen Abgleich mit deiner Swissquote-Liste (CSV-Export).

## API-Keys

Kopiere `env.example` zu `.env` und trage ein, sobald du dich registriert hast:
- [Marketaux](https://www.marketaux.com/) – **$19/Monat**: 1.000 calls/Tag, Sentiment inklusive
- [Finnhub](https://finnhub.io/) – Free-Tier: 60 calls/min, News + Earnings
- [NewsAPI](https://newsapi.org/) – Free-Tier: 100 calls/Tag
- Reddit – läuft **standardmäßig anonym, keine Anmeldung nötig**. Wer möchte, kann optional bei [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) eine "script"-App für höhere Rate-Limits anlegen.
- [Anthropic Claude](https://console.anthropic.com/) – ~$5/Monat, smartes Re-Ranking (Phase 3+)
- Telegram Bot Token via [@BotFather](https://t.me/BotFather)
