"""
AI Invest Radar – Phase 2
Holt Preise + technische Indikatoren für US-Aktien und Krypto,
berechnet einen transparenten Score, sammelt aktuelle News (Finnhub + NewsAPI)
und gibt einen Tages-Report aus.

Phase 1 läuft ohne Keys. News (Phase 2) sind optional – wenn Keys in .env
fehlen, wird die News-Sektion einfach übersprungen.
"""

import os
import warnings
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator

warnings.filterwarnings("ignore")  # yfinance ist gesprächig
load_dotenv()

MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY", "").strip()
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "").strip()
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "").strip()
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "ai-invest-radar/0.1").strip()

NEWS_LOOKBACK_DAYS = 7
MAX_HEADLINES_PER_TICKER = 3
REDDIT_STOCK_SUBS = "wallstreetbets+stocks+investing+StockMarket+options"
REDDIT_CRYPTO_SUBS = "CryptoCurrency+CryptoMarkets+Bitcoin+ethereum"
MAX_REDDIT_POSTS_PER_TICKER = 3

# ----------------------------------------------------------------------------
# Universum – alle Werte sind bei Swissquote handelbar
# ----------------------------------------------------------------------------
US_STOCKS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "AMD", "AVGO", "NFLX", "CRM", "ORCL", "ADBE", "PLTR", "COIN",
]

CRYPTOS = {
    # CoinGecko-ID : Ticker
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "cardano": "ADA",
    "ripple": "XRP",
    "polkadot": "DOT",
    "chainlink": "LINK",
    "avalanche-2": "AVAX",
}


# ----------------------------------------------------------------------------
# Aktien-Analyse
# ----------------------------------------------------------------------------
def analyze_stock(ticker: str) -> dict | None:
    """3 Monate Daten holen, RSI/MACD/SMA berechnen, Score vergeben."""
    df = yf.download(ticker, period="3mo", interval="1d",
                     progress=False, auto_adjust=True)
    if df.empty or len(df) < 30:
        return None

    close = df["Close"].squeeze()
    rsi = float(RSIIndicator(close).rsi().iloc[-1])
    macd_diff = float(MACD(close).macd_diff().iloc[-1])
    sma_20 = float(SMAIndicator(close, window=20).sma_indicator().iloc[-1])
    sma_50 = float(SMAIndicator(close, window=50).sma_indicator().iloc[-1])
    price = float(close.iloc[-1])

    # Transparenter Regel-basierter Score
    score, signals = 0, []
    if rsi < 30:
        score += 2; signals.append("RSI oversold")
    elif rsi > 70:
        score -= 2; signals.append("RSI overbought")

    if macd_diff > 0:
        score += 1; signals.append("MACD bullish")
    else:
        score -= 1; signals.append("MACD bearish")

    if price > sma_20 > sma_50:
        score += 1; signals.append("Aufwärtstrend")
    elif price < sma_20 < sma_50:
        score -= 1; signals.append("Abwärtstrend")

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "rsi": round(rsi, 1),
        "score": score,
        "signals": ", ".join(signals),
    }


# ----------------------------------------------------------------------------
# Krypto-Analyse (CoinGecko, gratis, kein Key)
# ----------------------------------------------------------------------------
def analyze_crypto() -> list[dict]:
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(CRYPTOS.keys()),
        "price_change_percentage": "24h,7d,30d",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()

    results = []
    for coin in r.json():
        ch_7d = coin.get("price_change_percentage_7d_in_currency") or 0
        ch_30d = coin.get("price_change_percentage_30d_in_currency") or 0

        score, signals = 0, []
        if ch_7d < -10:
            score += 2; signals.append("starker 7d-Dip")
        elif ch_7d > 15:
            score -= 1; signals.append("überhitzt 7d")
        if ch_30d > 20:
            score += 1; signals.append("Momentum 30d")
        if ch_30d < -25:
            score += 1; signals.append("möglicher Boden")

        results.append({
            "ticker": CRYPTOS[coin["id"]],
            "price": coin["current_price"],
            "change_7d": round(ch_7d, 1),
            "change_30d": round(ch_30d, 1),
            "score": score,
            "signals": ", ".join(signals) or "neutral",
        })
    return results


# ----------------------------------------------------------------------------
# News-Sammeln (Phase 2)
# ----------------------------------------------------------------------------
def fetch_news_marketaux(ticker: str) -> list[dict]:
    """Marketaux liefert kuratierte Finanz-News inkl. per-Entity Sentiment."""
    url = "https://api.marketaux.com/v1/news/all"
    params = {
        "symbols": ticker,
        "filter_entities": "true",
        "language": "en",
        "limit": MAX_HEADLINES_PER_TICKER,
        "published_after": (
            datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%dT%H:%M"),
        "api_token": MARKETAUX_API_KEY,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    out = []
    for it in r.json().get("data", []):
        # Sentiment für genau diesen Ticker rausziehen
        sentiment = None
        for ent in it.get("entities", []):
            if ent.get("symbol", "").upper() == ticker.upper():
                sentiment = ent.get("sentiment_score")
                break
        out.append({
            "date": (it.get("published_at") or "")[:10],
            "source": it.get("source", "?"),
            "headline": (it.get("title") or "").strip(),
            "url": it.get("url", ""),
            "sentiment": sentiment,
        })
    return out


def fetch_news_finnhub(ticker: str) -> list[dict]:
    """Holt Company-News der letzten NEWS_LOOKBACK_DAYS Tage via Finnhub."""
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=NEWS_LOOKBACK_DAYS)
    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": ticker,
        "from": since.isoformat(),
        "to": today.isoformat(),
        "token": FINNHUB_API_KEY,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    items = r.json() or []
    items.sort(key=lambda x: x.get("datetime", 0), reverse=True)
    out = []
    for it in items[:MAX_HEADLINES_PER_TICKER]:
        ts = datetime.fromtimestamp(it.get("datetime", 0), tz=timezone.utc)
        out.append({
            "date": ts.strftime("%Y-%m-%d"),
            "source": it.get("source", "?"),
            "headline": it.get("headline", "").strip(),
            "url": it.get("url", ""),
            "sentiment": None,
        })
    return out


def fetch_news_newsapi(ticker: str) -> list[dict]:
    """Fallback: Headlines via NewsAPI (everything-Endpunkt)."""
    since = (datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)).date()
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": ticker,
        "from": since.isoformat(),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": MAX_HEADLINES_PER_TICKER,
        "apiKey": NEWSAPI_KEY,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    out = []
    for it in r.json().get("articles", [])[:MAX_HEADLINES_PER_TICKER]:
        out.append({
            "date": (it.get("publishedAt") or "")[:10],
            "source": (it.get("source") or {}).get("name", "?"),
            "headline": (it.get("title") or "").strip(),
            "url": it.get("url", ""),
            "sentiment": None,
        })
    return out


def fetch_news(ticker: str) -> list[dict]:
    """Priorität: Marketaux (mit Sentiment) > Finnhub > NewsAPI."""
    if MARKETAUX_API_KEY:
        try:
            return fetch_news_marketaux(ticker)
        except Exception as e:
            print(f"  Marketaux-Fehler bei {ticker}: {e}")
    if FINNHUB_API_KEY:
        try:
            return fetch_news_finnhub(ticker)
        except Exception as e:
            print(f"  Finnhub-Fehler bei {ticker}: {e}")
    if NEWSAPI_KEY:
        try:
            return fetch_news_newsapi(ticker)
        except Exception as e:
            print(f"  NewsAPI-Fehler bei {ticker}: {e}")
    return []


# ----------------------------------------------------------------------------
# Reddit-Buzz (Phase 2.5 – gratis via PRAW)
# ----------------------------------------------------------------------------
_reddit_client = None


def _get_reddit():
    """Lazy-init des Reddit-Clients (Read-only-Modus, kein Login nötig)."""
    global _reddit_client
    if _reddit_client is not None:
        return _reddit_client
    if not (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET):
        return None
    import praw  # lokaler Import, falls praw nicht installiert ist
    _reddit_client = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )
    _reddit_client.read_only = True
    return _reddit_client


def fetch_reddit_buzz(ticker: str, subs: str) -> dict:
    """Sucht Posts der letzten Woche, die den Ticker erwähnen.

    Liefert mention-count, summierte Upvotes (engagement) und Top-Posts.
    """
    reddit = _get_reddit()
    if reddit is None:
        return {"mentions": 0, "upvotes": 0, "posts": []}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)).timestamp()
    query = f"${ticker} OR {ticker}"
    posts = []
    total_upvotes = 0
    try:
        for submission in reddit.subreddit(subs).search(
            query, sort="new", time_filter="week", limit=25
        ):
            if submission.created_utc < cutoff:
                continue
            title = (submission.title or "").strip()
            # Naive Treffer-Validierung: Ticker muss als ganzes Wort vorkommen
            haystack = f" {title.upper()} "
            if f" {ticker.upper()} " not in haystack and f"${ticker.upper()}" not in haystack:
                continue
            posts.append({
                "date": datetime.fromtimestamp(
                    submission.created_utc, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                "subreddit": submission.subreddit.display_name,
                "title": title,
                "score": int(submission.score),
                "num_comments": int(submission.num_comments),
                "url": f"https://reddit.com{submission.permalink}",
            })
            total_upvotes += int(submission.score)
    except Exception as e:
        print(f"  Reddit-Fehler bei {ticker}: {e}")
        return {"mentions": 0, "upvotes": 0, "posts": []}

    posts.sort(key=lambda p: p["score"], reverse=True)
    return {
        "mentions": len(posts),
        "upvotes": total_upvotes,
        "posts": posts[:MAX_REDDIT_POSTS_PER_TICKER],
    }


def _format_sentiment(score: float | None) -> str:
    """Sentiment als kompaktes Label: 📈 +0.42 / 📉 -0.31 / ➖ 0.05."""
    if score is None:
        return ""
    if score > 0.15:
        icon = "[+]"
    elif score < -0.15:
        icon = "[-]"
    else:
        icon = "[~]"
    return f" {icon} {score:+.2f}"


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def main() -> None:
    print(f"\n=== AI Invest Radar – {datetime.now():%Y-%m-%d %H:%M} ===\n")

    # --- Aktien ---
    print(">>> US-Aktien")
    stocks = []
    for t in US_STOCKS:
        try:
            r = analyze_stock(t)
            if r:
                stocks.append(r)
        except Exception as e:
            print(f"  Fehler bei {t}: {e}")
    stocks.sort(key=lambda x: x["score"], reverse=True)
    print(pd.DataFrame(stocks).to_string(index=False))

    # --- News (Phase 2) ---
    if MARKETAUX_API_KEY or FINNHUB_API_KEY or NEWSAPI_KEY:
        provider = (
            "Marketaux" if MARKETAUX_API_KEY
            else "Finnhub" if FINNHUB_API_KEY
            else "NewsAPI"
        )
        print(f"\n>>> News (letzte {NEWS_LOOKBACK_DAYS}d via {provider})")
        for s in stocks:
            t = s["ticker"]
            headlines = fetch_news(t)
            if not headlines:
                print(f"\n  {t}: keine News")
                continue
            print(f"\n  {t}")
            for h in headlines:
                title = h["headline"][:110]
                sent = _format_sentiment(h.get("sentiment"))
                print(f"    [{h['date']}] {h['source']}: {title}{sent}")
    else:
        print("\n>>> News: keine API-Keys gesetzt (siehe .env) – wird übersprungen")

    # --- Reddit-Buzz für Aktien ---
    if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
        print(f"\n>>> Reddit-Buzz Aktien (letzte {NEWS_LOOKBACK_DAYS}d, r/{REDDIT_STOCK_SUBS.replace('+', ', r/')})")
        for s in stocks:
            t = s["ticker"]
            buzz = fetch_reddit_buzz(t, REDDIT_STOCK_SUBS)
            if buzz["mentions"] == 0:
                print(f"\n  {t}: keine Mentions")
                continue
            print(f"\n  {t}: {buzz['mentions']} Mentions, {buzz['upvotes']:,} Upvotes")
            for p in buzz["posts"]:
                title = p["title"][:100]
                print(f"    [{p['date']} r/{p['subreddit']}] +{p['score']} ups / {p['num_comments']}c  {title}")
    else:
        print("\n>>> Reddit-Buzz: keine Reddit-Credentials in .env – wird übersprungen")

    # --- Krypto ---
    print("\n>>> Krypto")
    cryptos = analyze_crypto()
    cryptos.sort(key=lambda x: x["score"], reverse=True)
    print(pd.DataFrame(cryptos).to_string(index=False))

    # --- Reddit-Buzz für Krypto ---
    if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
        print(f"\n>>> Reddit-Buzz Krypto (letzte {NEWS_LOOKBACK_DAYS}d, r/{REDDIT_CRYPTO_SUBS.replace('+', ', r/')})")
        for c in cryptos:
            t = c["ticker"]
            buzz = fetch_reddit_buzz(t, REDDIT_CRYPTO_SUBS)
            if buzz["mentions"] == 0:
                print(f"\n  {t}: keine Mentions")
                continue
            print(f"\n  {t}: {buzz['mentions']} Mentions, {buzz['upvotes']:,} Upvotes")
            for p in buzz["posts"]:
                title = p["title"][:100]
                print(f"    [{p['date']} r/{p['subreddit']}] +{p['score']} ups / {p['num_comments']}c  {title}")

    # --- Top-Kandidaten ---
    print("\n=== Top 3 Long-Kandidaten ===")
    combined = (
        [{"asset": s["ticker"], "score": s["score"], "signals": s["signals"]} for s in stocks]
        + [{"asset": c["ticker"], "score": c["score"], "signals": c["signals"]} for c in cryptos]
    )
    combined.sort(key=lambda x: x["score"], reverse=True)
    for r in combined[:3]:
        print(f"  {r['asset']:6} score={r['score']:+d}  ({r['signals']})")
    print()


if __name__ == "__main__":
    main()
