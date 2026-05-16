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

# 3. Erster Lauf
python main.py
```

## Was das aktuell tut (Phase 1 + 2)

- Holt 3 Monate OHLC-Daten für 15 US-Aktien via `yfinance` (gratis, kein Key)
- Holt 24h/7d/30d Performance für 8 Kryptos via CoinGecko (gratis, kein Key)
- Berechnet **RSI**, **MACD**, **SMA20/SMA50**
- Vergibt einen einfachen, transparenten Score je Asset
- Listet die Top-Long-Kandidaten
- Sammelt aktuelle Company-News der letzten 7 Tage je Aktie
  - **Marketaux** (primär, $19/Monat) – kuratierte Finanz-News mit Sentiment-Score
  - **Finnhub** (Fallback, gratis)
  - **NewsAPI** (zweiter Fallback, gratis)
- Sammelt **Reddit-Buzz** (Mentions + Top-Posts der letzten 7 Tage) je Aktie und Krypto
  - Aktien: r/wallstreetbets, r/stocks, r/investing, r/StockMarket, r/options
  - Krypto: r/CryptoCurrency, r/CryptoMarkets, r/Bitcoin, r/ethereum
  - Läuft **anonym ohne Account** via public JSON-Endpoint. Optional kann eine PRAW-Auth eingerichtet werden, falls man höhere Rate-Limits braucht.

Ohne API-Keys läuft alles inkl. Reddit-Buzz. Nur die News-Sektion wird ohne Marketaux/Finnhub/NewsAPI übersprungen.

## Roadmap

| Phase | Inhalt | Status |
|-------|--------|--------|
| 1 | Daten + technische Indikatoren + Score | ✅ |
| 2 | News-Sammeln (Marketaux + Finnhub + NewsAPI) | ✅ |
| 2.5 | Reddit-Buzz (PRAW, gratis) | ✅ |
| 3 | Sentiment-Analyse mit FinBERT / Claude | nächste |
| 4 | Earnings-Tracking + Surprise-Signale | offen |
| 5 | Telegram-Bot für tägliche Alerts | offen |
| 6 | SQLite-Storage + Backtesting | offen |
| 7 | Swissquote-Whitelist als Filter | offen |

## Universum

Aktuelle Whitelist (siehe `main.py`):
- **US-Aktien:** AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, AMD, AVGO, NFLX, CRM, ORCL, ADBE, PLTR, COIN
- **Krypto:** BTC, ETH, SOL, ADA, XRP, DOT, LINK, AVAX

Alle bei Swissquote handelbar. In Phase 7 ersetzen wir das durch einen automatischen Abgleich mit deiner Swissquote-Liste (CSV-Export).

## API-Keys

Kopiere `env.example` zu `.env` und trage ein, sobald du dich registriert hast:
- [Marketaux](https://www.marketaux.com/) – **$19/Monat**: 1.000 calls/Tag, Sentiment inklusive
- [Finnhub](https://finnhub.io/) – Free-Tier: 60 calls/min, News + Earnings
- [NewsAPI](https://newsapi.org/) – Free-Tier: 100 calls/Tag
- Reddit – läuft **standardmäßig anonym, keine Anmeldung nötig**. Wer möchte, kann optional bei [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) eine "script"-App für höhere Rate-Limits anlegen.
- [Anthropic Claude](https://console.anthropic.com/) – ~$5/Monat, smartes Re-Ranking (Phase 3+)
- Telegram Bot Token via [@BotFather](https://t.me/BotFather)
