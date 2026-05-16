"""
AI Invest Radar – Phase 1, 2, 2.5, 2.7
Holt Preise + technische Indikatoren für 40 US-Aktien und Top-40-Kryptos,
aggregiert News aus mehreren Quellen, sammelt Reddit-Buzz und liefert
klare Buy/Watch/Hold/Sell-Empfehlungen.

CLI:  python main.py
Web:  streamlit run app.py  (öffnet http://localhost:8501)
"""

import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator

warnings.filterwarnings("ignore")
load_dotenv()

MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY", "").strip()
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "").strip()
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "").strip()
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "ai-invest-radar/0.1").strip()

NEWS_LOOKBACK_DAYS = 7
MAX_HEADLINES_PER_TICKER = 5  # mehr Quellen → mehr Headlines anzeigen
MAX_REDDIT_POSTS_PER_TICKER = 5

# ----------------------------------------------------------------------------
# Universum: Top 40 US-Aktien (Marketcap-sortiert, Swissquote-handelbar)
# ----------------------------------------------------------------------------
US_STOCKS = [
    "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "TSLA",
    "BRK-B", "LLY", "JPM", "V", "WMT", "XOM", "MA", "UNH",
    "ORCL", "COST", "JNJ", "PG", "NFLX", "HD", "BAC", "ABBV",
    "CRM", "KO", "CVX", "AMD", "MRK", "PEP", "ADBE", "ACN",
    "CSCO", "TMO", "MCD", "LIN", "IBM", "PLTR", "COIN", "INTC",
]

# Clearbit-Logo-Domains (Ticker → Firmen-Domain)
STOCK_DOMAINS = {
    "NVDA": "nvidia.com", "MSFT": "microsoft.com", "AAPL": "apple.com",
    "AMZN": "amazon.com", "GOOGL": "google.com", "META": "meta.com",
    "AVGO": "broadcom.com", "TSLA": "tesla.com",
    "BRK-B": "berkshirehathaway.com", "LLY": "lilly.com",
    "JPM": "jpmorganchase.com", "V": "visa.com", "WMT": "walmart.com",
    "XOM": "exxonmobil.com", "MA": "mastercard.com",
    "UNH": "unitedhealthgroup.com", "ORCL": "oracle.com",
    "COST": "costco.com", "JNJ": "jnj.com", "PG": "pg.com",
    "NFLX": "netflix.com", "HD": "homedepot.com",
    "BAC": "bankofamerica.com", "ABBV": "abbvie.com",
    "CRM": "salesforce.com", "KO": "coca-cola.com",
    "CVX": "chevron.com", "AMD": "amd.com", "MRK": "merck.com",
    "PEP": "pepsico.com", "ADBE": "adobe.com", "ACN": "accenture.com",
    "CSCO": "cisco.com", "TMO": "thermofisher.com",
    "MCD": "mcdonalds.com", "LIN": "linde.com", "IBM": "ibm.com",
    "PLTR": "palantir.com", "COIN": "coinbase.com", "INTC": "intel.com",
}

STABLECOIN_SYMBOLS = {
    # Klassische Stablecoins
    "USDT", "USDC", "DAI", "USDE", "TUSD", "FDUSD", "PYUSD", "USDD",
    "USDP", "FRAX", "GUSD", "LUSD", "USDS", "RLUSD", "USDG", "USYC",
    # Gold/Asset-backed (kein Trading-Volatility-Signal)
    "PAXG", "XAUT", "DGLD",
    # Tokenized Funds (BlackRock etc.)
    "BUIDL",
    # Wrapped Variants — eigener Trade selten sinnvoll
    "WBTC", "WETH", "WSTETH", "STETH", "WEETH", "WBETH", "RETH", "CBETH",
}

# Mehr Subreddits — werden gleichzeitig durchsucht
REDDIT_STOCK_SUBS = (
    "wallstreetbets+stocks+investing+StockMarket+options"
    "+ValueInvesting+SecurityAnalysis+dividends+pennystocks+Daytrading"
)
REDDIT_CRYPTO_SUBS = (
    "CryptoCurrency+CryptoMarkets+Bitcoin+ethereum"
    "+altcoin+defi+CryptoTechnology+SatoshiStreetBets"
)


# ============================================================================
# CRYPTO-UNIVERSE: dynamisch Top 40 ohne Stablecoins
# ============================================================================
def get_top_cryptos(n: int = 40) -> dict[str, dict]:
    """Holt die Top-N Coins nach Marketcap (ohne Stablecoins) von CoinGecko.

    Liefert dict {coingecko_id: {ticker, name, image, change_7d, change_30d, price}}.
    """
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 100,  # Buffer für Stablecoin-Ausfilter
        "page": 1,
        "price_change_percentage": "24h,7d,30d",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    out: dict[str, dict] = {}
    for coin in r.json():
        sym = coin.get("symbol", "").upper()
        if sym in STABLECOIN_SYMBOLS:
            continue
        if len(out) >= n:
            break
        out[coin["id"]] = {
            "ticker": sym,
            "name": coin.get("name", ""),
            "image": coin.get("image", ""),
            "price": coin.get("current_price") or 0,
            "change_24h": coin.get("price_change_percentage_24h_in_currency") or 0,
            "change_7d": coin.get("price_change_percentage_7d_in_currency") or 0,
            "change_30d": coin.get("price_change_percentage_30d_in_currency") or 0,
        }
    return out


# ============================================================================
# AKTIEN-ANALYSE
# ============================================================================
def fetch_stock_data_bulk(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Holt 3 Monate OHLC für alle Tickers in einem Call. Viel schneller als Schleife."""
    df = yf.download(
        tickers, period="3mo", interval="1d",
        progress=False, auto_adjust=True, group_by="ticker", threads=True,
    )
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            sub = df[t].dropna(how="all") if len(tickers) > 1 else df
            if not sub.empty and len(sub) >= 30:
                out[t] = sub
        except (KeyError, AttributeError):
            continue
    return out


def fetch_stock_data(ticker: str) -> pd.DataFrame | None:
    """Single-Ticker-Variante (für Streamlit-Cache und CLI-Convenience)."""
    df = yf.download(ticker, period="3mo", interval="1d",
                     progress=False, auto_adjust=True)
    if df.empty or len(df) < 30:
        return None
    return df


def analyze_stock_df(df: pd.DataFrame, ticker: str) -> dict:
    """RSI/MACD/SMA + Score aus OHLC-DataFrame."""
    close = df["Close"].squeeze()
    rsi = float(RSIIndicator(close).rsi().iloc[-1])
    macd_diff = float(MACD(close).macd_diff().iloc[-1])
    sma_20 = float(SMAIndicator(close, window=20).sma_indicator().iloc[-1])
    sma_50 = float(SMAIndicator(close, window=50).sma_indicator().iloc[-1])
    price = float(close.iloc[-1])

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
        "logo": f"https://logo.clearbit.com/{STOCK_DOMAINS.get(ticker, '')}" if STOCK_DOMAINS.get(ticker) else "",
    }


def analyze_stock(ticker: str) -> dict | None:
    df = fetch_stock_data(ticker)
    if df is None:
        return None
    return analyze_stock_df(df, ticker)


def analyze_all_stocks() -> tuple[list[dict], dict[str, pd.DataFrame]]:
    """Bulk-Variante: scort alle US_STOCKS in einem Rutsch + liefert OHLC-Daten."""
    data = fetch_stock_data_bulk(US_STOCKS)
    results = []
    for t in US_STOCKS:
        if t not in data:
            continue
        try:
            results.append(analyze_stock_df(data[t], t))
        except Exception:
            continue
    return results, data


# ============================================================================
# KRYPTO-ANALYSE
# ============================================================================
def analyze_crypto(top_n: int = 40) -> list[dict]:
    """Holt + bewertet die Top-N Kryptos."""
    coins = get_top_cryptos(top_n)
    results = []
    for cid, c in coins.items():
        score, signals = 0, []
        if c["change_7d"] < -10:
            score += 2; signals.append("starker 7d-Dip")
        elif c["change_7d"] > 15:
            score -= 1; signals.append("überhitzt 7d")
        if c["change_30d"] > 20:
            score += 1; signals.append("Momentum 30d")
        if c["change_30d"] < -25:
            score += 1; signals.append("möglicher Boden")

        results.append({
            "ticker": c["ticker"],
            "name": c["name"],
            "price": c["price"],
            "change_24h": round(c["change_24h"], 1),
            "change_7d": round(c["change_7d"], 1),
            "change_30d": round(c["change_30d"], 1),
            "score": score,
            "signals": ", ".join(signals) or "neutral",
            "logo": c["image"],
            "coingecko_id": cid,
        })
    return results


# ============================================================================
# NEWS-SAMMELN: aggregiert + dedupliziert aus mehreren Quellen
# ============================================================================
def fetch_news_marketaux(ticker: str) -> list[dict]:
    if not MARKETAUX_API_KEY:
        return []
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
            "provider": "marketaux",
        })
    return out


def fetch_news_finnhub(ticker: str) -> list[dict]:
    if not FINNHUB_API_KEY:
        return []
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=NEWS_LOOKBACK_DAYS)
    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": ticker, "from": since.isoformat(),
        "to": today.isoformat(), "token": FINNHUB_API_KEY,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    items = (r.json() or [])
    items.sort(key=lambda x: x.get("datetime", 0), reverse=True)
    out = []
    for it in items[:MAX_HEADLINES_PER_TICKER]:
        ts = datetime.fromtimestamp(it.get("datetime", 0), tz=timezone.utc)
        out.append({
            "date": ts.strftime("%Y-%m-%d"),
            "source": it.get("source", "?"),
            "headline": (it.get("headline") or "").strip(),
            "url": it.get("url", ""),
            "sentiment": None,
            "provider": "finnhub",
        })
    return out


def fetch_news_yahoo(ticker: str) -> list[dict]:
    """Yahoo Finance News via yfinance (gratis, kein Key)."""
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)).timestamp()
    out = []
    for it in raw[:MAX_HEADLINES_PER_TICKER * 2]:
        # yfinance liefert teils nested unter "content"
        content = it.get("content") or it
        ts = it.get("providerPublishTime") or 0
        if not ts and content.get("pubDate"):
            try:
                ts = datetime.fromisoformat(
                    content["pubDate"].replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                ts = 0
        if ts and ts < cutoff:
            continue
        title = content.get("title") or it.get("title", "")
        url = content.get("canonicalUrl", {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else it.get("link", "")
        publisher = content.get("provider", {}).get("displayName") if isinstance(content.get("provider"), dict) else it.get("publisher", "Yahoo")
        if not title:
            continue
        out.append({
            "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "",
            "source": publisher or "Yahoo Finance",
            "headline": title.strip(),
            "url": url or "",
            "sentiment": None,
            "provider": "yahoo",
        })
    return out[:MAX_HEADLINES_PER_TICKER]


def fetch_news_google_rss(query: str) -> list[dict]:
    """Google News RSS (gratis, keine Auth). Liefert publisher-Mix."""
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query + ' stock')}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        feed = feedparser.parse(url)
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)
    out = []
    for entry in feed.entries[:MAX_HEADLINES_PER_TICKER * 3]:
        try:
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            published = datetime.now(timezone.utc)
        if published < cutoff:
            continue
        source = entry.get("source", {}).get("title", "Google News")
        out.append({
            "date": published.strftime("%Y-%m-%d"),
            "source": source,
            "headline": entry.get("title", "").strip(),
            "url": entry.get("link", ""),
            "sentiment": None,
            "provider": "google",
        })
    return out[:MAX_HEADLINES_PER_TICKER]


def _dedup_news(items: list[dict]) -> list[dict]:
    """Headlines aus mehreren Quellen mergen — gleiche Headline = 1 Eintrag.

    Wenn Sentiment-Score von einer Quelle existiert (Marketaux), wird er bevorzugt.
    """
    by_key: dict[str, dict] = {}
    for it in items:
        key = (it.get("headline") or "").lower()[:80].strip()
        if not key:
            continue
        if key not in by_key:
            by_key[key] = it
            continue
        # Existing keep, but merge sentiment from Marketaux if available
        existing = by_key[key]
        if existing.get("sentiment") is None and it.get("sentiment") is not None:
            existing["sentiment"] = it["sentiment"]
    merged = list(by_key.values())
    merged.sort(key=lambda x: x.get("date", ""), reverse=True)
    return merged[:MAX_HEADLINES_PER_TICKER]


def fetch_news(ticker: str, query_name: str | None = None) -> list[dict]:
    """Aggregiert News aus allen verfügbaren Quellen parallel + dedupliziert.

    query_name: für Krypto den vollen Namen ("Bitcoin") statt Ticker ("BTC") nutzen,
    sonst findet Google News nichts Brauchbares.
    """
    name = query_name or ticker
    fetchers = [
        (fetch_news_marketaux, ticker),
        (fetch_news_finnhub, ticker),
        (fetch_news_yahoo, ticker),
        (fetch_news_google_rss, name),
    ]
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fn, arg): fn.__name__ for fn, arg in fetchers}
        for fut in as_completed(futures):
            try:
                items.extend(fut.result())
            except Exception:
                continue
    return _dedup_news(items)


def _format_sentiment(score: float | None) -> str:
    if score is None:
        return ""
    if score > 0.15:
        return f" [+] {score:+.2f}"
    if score < -0.15:
        return f" [-] {score:+.2f}"
    return f" [~] {score:+.2f}"


# ============================================================================
# REDDIT-BUZZ: parallel über mehr Subreddits, mit 24h-Velocity
# ============================================================================
def _filter_and_pack_posts(raw: list[dict], ticker: str) -> dict:
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)).timestamp()
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
    posts = []
    mentions_24h = 0
    total_upvotes = 0
    for p in raw:
        if p["created_utc"] < cutoff_7d:
            continue
        haystack = f" {p['title'].upper()} "
        if (
            f" {ticker.upper()} " not in haystack
            and f"${ticker.upper()}" not in haystack
        ):
            continue
        posts.append({
            "date": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc).strftime("%Y-%m-%d"),
            "subreddit": p["subreddit"],
            "title": p["title"],
            "score": p["score"],
            "num_comments": p["num_comments"],
            "url": f"https://reddit.com{p['permalink']}",
        })
        total_upvotes += p["score"]
        if p["created_utc"] >= cutoff_24h:
            mentions_24h += 1
    posts.sort(key=lambda x: x["score"], reverse=True)
    # Velocity: 24h-Rate als x-Fache der 7d-Durchschnittsrate
    expected_24h = len(posts) / NEWS_LOOKBACK_DAYS if posts else 0
    velocity = (mentions_24h / expected_24h) if expected_24h > 0 else 0
    return {
        "mentions": len(posts),
        "mentions_24h": mentions_24h,
        "velocity": round(velocity, 1),  # 1.0 = normal, >2.0 = Hype-Spike
        "upvotes": total_upvotes,
        "posts": posts[:MAX_REDDIT_POSTS_PER_TICKER],
    }


def fetch_reddit_buzz_public(ticker: str, subs: str) -> dict:
    """Reddit public JSON-Endpoint, keine Auth."""
    url = f"https://www.reddit.com/r/{subs}/search.json"
    params = {
        "q": f"${ticker} OR {ticker}",
        "restrict_sr": "on",
        "t": "week",
        "sort": "new",
        "limit": 50,
    }
    headers = {"User-Agent": REDDIT_USER_AGENT}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return {"mentions": 0, "mentions_24h": 0, "velocity": 0,
                "upvotes": 0, "posts": [], "error": str(e)}

    raw = []
    for child in r.json().get("data", {}).get("children", []):
        d = child.get("data", {})
        raw.append({
            "created_utc": d.get("created_utc", 0),
            "title": (d.get("title") or "").strip(),
            "subreddit": d.get("subreddit", "?"),
            "score": int(d.get("score", 0)),
            "num_comments": int(d.get("num_comments", 0)),
            "permalink": d.get("permalink", ""),
        })
    return _filter_and_pack_posts(raw, ticker)


_reddit_praw = None


def _get_reddit_praw():
    global _reddit_praw
    if _reddit_praw is not None:
        return _reddit_praw
    if not (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET):
        return None
    import praw
    _reddit_praw = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )
    _reddit_praw.read_only = True
    return _reddit_praw


def fetch_reddit_buzz_praw(ticker: str, subs: str) -> dict:
    reddit = _get_reddit_praw()
    if reddit is None:
        return {"mentions": 0, "mentions_24h": 0, "velocity": 0,
                "upvotes": 0, "posts": []}
    raw = []
    try:
        for s in reddit.subreddit(subs).search(
            f"${ticker} OR {ticker}", sort="new", time_filter="week", limit=50
        ):
            raw.append({
                "created_utc": s.created_utc,
                "title": (s.title or "").strip(),
                "subreddit": s.subreddit.display_name,
                "score": int(s.score),
                "num_comments": int(s.num_comments),
                "permalink": s.permalink,
            })
    except Exception:
        return {"mentions": 0, "mentions_24h": 0, "velocity": 0,
                "upvotes": 0, "posts": []}
    return _filter_and_pack_posts(raw, ticker)


def fetch_reddit_buzz(ticker: str, subs: str) -> dict:
    if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
        return fetch_reddit_buzz_praw(ticker, subs)
    return fetch_reddit_buzz_public(ticker, subs)


def fetch_reddit_buzz_bulk(tickers: list[str], subs: str, max_workers: int = 10) -> dict[str, dict]:
    """Parallele Reddit-Abfragen für viele Tickers gleichzeitig."""
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_reddit_buzz, t, subs): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                out[t] = fut.result()
            except Exception:
                out[t] = {"mentions": 0, "mentions_24h": 0, "velocity": 0,
                          "upvotes": 0, "posts": []}
    return out


# ============================================================================
# EMPFEHLUNGS-LOGIK
# ============================================================================
def recommendation_label(score: int, mentions_24h: int = 0, velocity: float = 0) -> str:
    """Klares Buy/Watch/Hold/Sell-Label basierend auf Score + optional Reddit-Buzz."""
    # Reddit-Velocity-Boost: starker Hype kann WATCH zu BUY anheben
    effective = score
    if velocity >= 2.5 and mentions_24h >= 3:
        effective += 1  # 24h-Spike boostet
    if effective >= 3:
        return "BUY"
    if effective >= 1:
        return "WATCH"
    if effective >= -1:
        return "HOLD"
    if effective >= -2:
        return "REDUCE"
    return "SELL"


# ============================================================================
# CLI-Report
# ============================================================================
def main() -> None:
    print(f"\n=== AI Invest Radar – {datetime.now():%Y-%m-%d %H:%M} ===\n")

    print(">>> US-Aktien (Top 40 nach Marketcap)")
    stocks, _ohlc = analyze_all_stocks()
    stocks.sort(key=lambda x: x["score"], reverse=True)
    df = pd.DataFrame(stocks)[["ticker", "price", "rsi", "score", "signals"]]
    print(df.to_string(index=False))

    print("\n>>> Krypto (Top 40 nach Marketcap, dynamisch)")
    cryptos = analyze_crypto(40)
    cryptos.sort(key=lambda x: x["score"], reverse=True)
    df = pd.DataFrame(cryptos)[["ticker", "name", "price", "change_7d", "change_30d", "score", "signals"]]
    print(df.to_string(index=False))

    print("\n=== Top 10 Long-Kandidaten ===")
    combined = (
        [{"asset": s["ticker"], "score": s["score"], "signals": s["signals"],
          "label": recommendation_label(s["score"])} for s in stocks]
        + [{"asset": c["ticker"], "score": c["score"], "signals": c["signals"],
            "label": recommendation_label(c["score"])} for c in cryptos]
    )
    combined.sort(key=lambda x: x["score"], reverse=True)
    for r in combined[:10]:
        print(f"  [{r['label']:6}] {r['asset']:8} score={r['score']:+d}  ({r['signals']})")
    print()


if __name__ == "__main__":
    main()
