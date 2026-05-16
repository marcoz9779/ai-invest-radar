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

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

NEWS_LOOKBACK_DAYS = 7
MAX_HEADLINES_PER_TICKER = 5  # mehr Quellen → mehr Headlines anzeigen
MAX_REDDIT_POSTS_PER_TICKER = 5
EARNINGS_LOOKAHEAD_DAYS = 14  # zeige Earnings die innerhalb der nächsten X Tage anstehen

# US-Sektor-ETFs für Sektor-Performance-Übersicht
SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication",
}

# Aktien-Sektor-Zuordnung (vereinfacht, für Sektor-Tags in Cards)
STOCK_SECTOR = {
    "NVDA": "XLK", "MSFT": "XLK", "AAPL": "XLK", "AVGO": "XLK",
    "ORCL": "XLK", "CRM": "XLK", "AMD": "XLK", "ADBE": "XLK",
    "ACN": "XLK", "CSCO": "XLK", "IBM": "XLK", "PLTR": "XLK",
    "INTC": "XLK", "COIN": "XLF",
    "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC",
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "MCD": "XLY",
    "BRK-B": "XLF", "JPM": "XLF", "V": "XLF", "MA": "XLF", "BAC": "XLF",
    "WMT": "XLP", "COST": "XLP", "PG": "XLP", "KO": "XLP", "PEP": "XLP",
    "XOM": "XLE", "CVX": "XLE",
    "LLY": "XLV", "UNH": "XLV", "JNJ": "XLV", "ABBV": "XLV", "MRK": "XLV", "TMO": "XLV",
    "LIN": "XLB",
}

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
def _coingecko_get(url: str, params: dict, max_retries: int = 3) -> dict | list | None:
    """GET mit Retry/Backoff für CoinGecko-Rate-Limits."""
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:
                # Rate-Limit: wait progressively longer
                wait = 5 * (attempt + 1)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except Exception:
            return None
    return None


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
    data = _coingecko_get(url, params)
    if not data:
        return {}
    out: dict[str, dict] = {}
    for coin in data:
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


def _compute_indicators(df: pd.DataFrame) -> dict:
    """Berechnet RSI/MACD/SMA20/SMA50/Volume-Spike/Bollinger/52w-Position."""
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze() if "Volume" in df.columns else None
    high = df["High"].squeeze() if "High" in df.columns else close
    low = df["Low"].squeeze() if "Low" in df.columns else close

    rsi = float(RSIIndicator(close).rsi().iloc[-1])
    macd_diff = float(MACD(close).macd_diff().iloc[-1])
    sma_20_series = SMAIndicator(close, window=20).sma_indicator()
    sma_50_series = SMAIndicator(close, window=50).sma_indicator()
    sma_20 = float(sma_20_series.iloc[-1])
    sma_50 = float(sma_50_series.iloc[-1])
    price = float(close.iloc[-1])

    # Volume-Spike: heute vs 20d-Durchschnitt
    vol_spike = None
    if volume is not None and len(volume) >= 20:
        avg_20 = float(volume.tail(20).mean()) or 0
        if avg_20 > 0:
            vol_spike = float(volume.iloc[-1]) / avg_20

    # Bollinger-Bandbreite (Squeeze-Erkennung: low volatility = breakout incoming)
    bb_squeeze = None
    if len(close) >= 20:
        std_20 = float(close.tail(20).std())
        if sma_20 > 0:
            bb_pct = (std_20 * 2) / sma_20  # ~Bandbreite als % vom Mittelwert
            bb_squeeze = bb_pct

    # 52w-Position: % unter 52w-Hoch (negativ = unter Hoch)
    period_high = float(high.max())
    period_low = float(low.min())
    pct_from_high = ((price - period_high) / period_high * 100) if period_high > 0 else 0
    pct_from_low = ((price - period_low) / period_low * 100) if period_low > 0 else 0

    return {
        "price": price, "rsi": rsi, "macd_diff": macd_diff,
        "sma_20": sma_20, "sma_50": sma_50,
        "vol_spike": vol_spike, "bb_squeeze": bb_squeeze,
        "pct_from_high": pct_from_high, "pct_from_low": pct_from_low,
    }


def _score_from_indicators(ind: dict, has_earnings_soon: bool = False) -> tuple[int, list[str]]:
    """Erweiterter Score: RSI/MACD/SMA + Volume + 52w + Earnings."""
    score, signals = 0, []
    rsi = ind["rsi"]
    if rsi < 30:
        score += 2; signals.append("RSI oversold")
    elif rsi > 70:
        score -= 2; signals.append("RSI overbought")

    if ind["macd_diff"] > 0:
        score += 1; signals.append("MACD bullish")
    else:
        score -= 1; signals.append("MACD bearish")

    price, sma_20, sma_50 = ind["price"], ind["sma_20"], ind["sma_50"]
    if price > sma_20 > sma_50:
        score += 1; signals.append("Aufwärtstrend")
    elif price < sma_20 < sma_50:
        score -= 1; signals.append("Abwärtstrend")

    # Volume-Spike: heute >2x normal = signifikantes Interesse
    if ind.get("vol_spike") is not None and ind["vol_spike"] >= 2.0:
        score += 1; signals.append(f"Volume {ind['vol_spike']:.1f}x")

    # 52w-Tief = oversold-Bonus
    if ind.get("pct_from_low") is not None and ind["pct_from_low"] < 5:
        score += 1; signals.append("nahe 52w-Tief")
    elif ind.get("pct_from_high") is not None and ind["pct_from_high"] > -3:
        score -= 1; signals.append("nahe 52w-Hoch")

    # Earnings-Boost
    if has_earnings_soon:
        signals.append("Earnings <14d")

    return score, signals


def analyze_stock_df(df: pd.DataFrame, ticker: str, earnings_date: str | None = None) -> dict:
    """RSI/MACD/SMA + erweiterter Score aus OHLC-DataFrame."""
    ind = _compute_indicators(df)
    has_earnings_soon = bool(earnings_date)
    score, signals = _score_from_indicators(ind, has_earnings_soon)

    return {
        "ticker": ticker,
        "price": round(ind["price"], 2),
        "rsi": round(ind["rsi"], 1),
        "score": score,
        "signals": ", ".join(signals),
        "vol_spike": round(ind["vol_spike"], 2) if ind.get("vol_spike") else None,
        "pct_from_high": round(ind["pct_from_high"], 1),
        "pct_from_low": round(ind["pct_from_low"], 1),
        "earnings_date": earnings_date,
        "sector": STOCK_SECTOR.get(ticker, ""),
        "logo": f"https://logo.clearbit.com/{STOCK_DOMAINS.get(ticker, '')}" if STOCK_DOMAINS.get(ticker) else "",
    }


def analyze_stock(ticker: str) -> dict | None:
    df = fetch_stock_data(ticker)
    if df is None:
        return None
    return analyze_stock_df(df, ticker)


def fetch_earnings_calendar(ticker: str) -> str | None:
    """Liefert das nächste Earnings-Datum (ISO-String) wenn innerhalb der nächsten 14 Tage."""
    try:
        info = yf.Ticker(ticker)
        cal = info.calendar
        if isinstance(cal, dict) and cal.get("Earnings Date"):
            dates = cal["Earnings Date"]
            if isinstance(dates, list) and dates:
                next_date = dates[0]
            else:
                next_date = dates
        elif hasattr(cal, "T"):  # DataFrame-Variante (älteres yfinance)
            row = cal.T
            next_date = row.get("Earnings Date", [None])[0] if "Earnings Date" in row else None
        else:
            return None
        if next_date is None:
            return None
        # Normalisieren zu date
        if hasattr(next_date, "date"):
            d = next_date.date()
        else:
            d = next_date
        today = datetime.now().date()
        if isinstance(d, datetime):
            d = d.date()
        delta = (d - today).days
        if 0 <= delta <= EARNINGS_LOOKAHEAD_DAYS:
            return d.isoformat()
        return None
    except Exception:
        return None


def fetch_earnings_calendar_bulk(tickers: list[str], max_workers: int = 8) -> dict[str, str | None]:
    """Parallele Earnings-Fetches."""
    out: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_earnings_calendar, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                out[t] = fut.result()
            except Exception:
                out[t] = None
    return out


def analyze_all_stocks() -> tuple[list[dict], dict[str, pd.DataFrame]]:
    """Bulk-Variante: scort alle US_STOCKS inkl. Earnings."""
    data = fetch_stock_data_bulk(US_STOCKS)
    earnings = fetch_earnings_calendar_bulk(list(data.keys()))
    results = []
    for t in US_STOCKS:
        if t not in data:
            continue
        try:
            results.append(analyze_stock_df(data[t], t, earnings.get(t)))
        except Exception:
            continue
    return results, data


# ============================================================================
# KRYPTO-ANALYSE
# ============================================================================
def fetch_crypto_ohlc(coingecko_id: str, days: int = 90) -> pd.DataFrame | None:
    """OHLC für eine Krypto-Coin via CoinGecko (mit Retry bei Rate-Limit)."""
    url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}/ohlc"
    params = {"vs_currency": "usd", "days": days}
    data = _coingecko_get(url, params)
    if not data or len(data) < 30:
        return None
    try:
        df = pd.DataFrame(data, columns=["ts", "Open", "High", "Low", "Close"])
        df["Volume"] = 0  # CoinGecko OHLC liefert kein Volume
        df.index = pd.to_datetime(df["ts"], unit="ms")
        df = df.drop(columns=["ts"])
        return df
    except Exception:
        return None


def fetch_crypto_ohlc_bulk(coingecko_ids: list[str], max_workers: int = 4) -> dict[str, pd.DataFrame]:
    """Parallel mit niedrigem max_workers, weil CoinGecko-Free-Tier ~30 calls/min."""
    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_crypto_ohlc, cid): cid for cid in coingecko_ids}
        for fut in as_completed(futures):
            cid = futures[fut]
            try:
                df = fut.result()
                if df is not None:
                    out[cid] = df
            except Exception:
                continue
    return out


def analyze_crypto(top_n: int = 40) -> tuple[list[dict], dict[str, pd.DataFrame]]:
    """Holt Top-N Kryptos + OHLC und scort sie mit RSI/MACD/SMA.

    Liefert (rows, ohlc_dict). ohlc_dict ist gekeyed nach Ticker (nicht id).
    """
    coins = get_top_cryptos(top_n)
    ohlc_data = fetch_crypto_ohlc_bulk(list(coins.keys()))

    results = []
    ohlc_by_ticker: dict[str, pd.DataFrame] = {}
    for cid, c in coins.items():
        ticker = c["ticker"]
        df = ohlc_data.get(cid)

        # Tech-Indikatoren wenn OHLC verfügbar
        ind_score = 0
        ind_signals: list[str] = []
        rsi = None
        if df is not None and len(df) >= 30:
            try:
                ind = _compute_indicators(df)
                ind_score, ind_signals = _score_from_indicators(ind, False)
                rsi = round(ind["rsi"], 1)
                ohlc_by_ticker[ticker] = df
            except Exception:
                df = None

        # Performance-Signale (zusätzlich)
        perf_score, perf_signals = 0, []
        if c["change_7d"] < -10:
            perf_score += 2; perf_signals.append("starker 7d-Dip")
        elif c["change_7d"] > 15:
            perf_score -= 1; perf_signals.append("überhitzt 7d")
        if c["change_30d"] > 20:
            perf_score += 1; perf_signals.append("Momentum 30d")
        if c["change_30d"] < -25:
            perf_score += 1; perf_signals.append("möglicher Boden")

        score = ind_score + perf_score
        signals = ind_signals + perf_signals

        results.append({
            "ticker": ticker,
            "name": c["name"],
            "price": c["price"],
            "rsi": rsi,
            "change_24h": round(c["change_24h"], 1),
            "change_7d": round(c["change_7d"], 1),
            "change_30d": round(c["change_30d"], 1),
            "score": score,
            "signals": ", ".join(signals) or "neutral",
            "logo": c["image"],
            "coingecko_id": cid,
        })
    return results, ohlc_by_ticker


# ============================================================================
# FEAR & GREED INDEX (Krypto-Stimmung, alternative.me)
# ============================================================================
def fetch_fear_greed_crypto() -> dict | None:
    """Free-API, kein Key. Liefert {value: 0-100, classification: "Fear" | "Greed" | ...}"""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if data:
            row = data[0]
            return {
                "value": int(row.get("value", 50)),
                "classification": row.get("value_classification", "Neutral"),
                "timestamp": row.get("timestamp", ""),
            }
    except Exception:
        pass
    return None


# ============================================================================
# SEKTOREN-PERFORMANCE (US-Sektor-ETFs)
# ============================================================================
def fetch_sector_performance() -> list[dict]:
    """5d/30d-Performance je US-Sektor (XLK/XLF/...)."""
    tickers = list(SECTOR_ETFS.keys())
    try:
        df = yf.download(tickers, period="40d", interval="1d",
                         progress=False, auto_adjust=True, group_by="ticker")
    except Exception:
        return []
    out = []
    for etf, name in SECTOR_ETFS.items():
        try:
            close = df[etf]["Close"] if len(tickers) > 1 else df["Close"]
            close = close.dropna()
            if len(close) < 30:
                continue
            now = float(close.iloc[-1])
            chg_5d = (now / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else 0
            chg_30d = (now / float(close.iloc[-30]) - 1) * 100
            out.append({
                "etf": etf, "sector": name,
                "change_5d": round(chg_5d, 2),
                "change_30d": round(chg_30d, 2),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["change_5d"], reverse=True)
    return out


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
# CLAUDE AI-SENTIMENT-FUSION (Phase 3 – optional, braucht ANTHROPIC_API_KEY)
# ============================================================================
def claude_sentiment_fusion(ticker: str, headlines: list[dict],
                            reddit_posts: list[dict]) -> dict | None:
    """Aggregiert News + Reddit zu einer holistischen Bewertung via Claude Haiku.

    Liefert {score: -1..+1, label: "bullish|neutral|bearish", summary: "..."}.
    None, wenn ANTHROPIC_API_KEY fehlt oder API-Fehler.
    """
    if not ANTHROPIC_API_KEY or (not headlines and not reddit_posts):
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    news_text = "\n".join(
        f"- [{h.get('date', '')}] {h.get('source', '')}: {h.get('headline', '')[:160]}"
        for h in headlines[:8]
    ) or "(keine News)"
    reddit_text = "\n".join(
        f"- [{p.get('date', '')}] r/{p.get('subreddit', '')} +{p.get('score', 0)}ups: {p.get('title', '')[:160]}"
        for p in reddit_posts[:5]
    ) or "(keine Reddit-Posts)"

    prompt = (
        f"Asset: {ticker}\n\n"
        f"News headlines (last 7 days):\n{news_text}\n\n"
        f"Top Reddit posts:\n{reddit_text}\n\n"
        "Task: Rate the overall sentiment toward this asset on a scale from -1.00 (very bearish) "
        "to +1.00 (very bullish). Consider context (earnings beats vs guidance misses, hype vs fundamentals). "
        "Return ONLY valid JSON: "
        '{"score": <float -1..1>, "label": "bullish"|"neutral"|"bearish", "summary": "<one short sentence>"}'
    )

    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Robuste JSON-Extraktion (Claude packt manchmal Erläuterungen drum herum)
        import json
        import re
        match = re.search(r"\{.*?\}", text, re.DOTALL)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception:
        return None


# ============================================================================
# EMPFEHLUNGS-LOGIK
# ============================================================================
def recommendation_label(score: int, mentions_24h: int = 0, velocity: float = 0,
                         has_earnings_soon: bool = False,
                         ai_score: float | None = None) -> str:
    """Klares Buy/Watch/Hold/Sell-Label basierend auf Score + Reddit + Earnings + AI."""
    effective = score
    if velocity >= 2.5 and mentions_24h >= 3:
        effective += 1  # Reddit-24h-Spike boostet
    if has_earnings_soon and score >= 1:
        effective += 1  # Anstehende Earnings = höhere Aufmerksamkeit
    if ai_score is not None:
        if ai_score > 0.4:
            effective += 1
        elif ai_score < -0.4:
            effective -= 1
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

    print("\n>>> Krypto (Top 40 nach Marketcap, dynamisch, mit OHLC-Indikatoren)")
    cryptos, _crypto_ohlc = analyze_crypto(40)
    cryptos.sort(key=lambda x: x["score"], reverse=True)
    df = pd.DataFrame(cryptos)[["ticker", "name", "price", "rsi", "change_7d", "change_30d", "score", "signals"]]
    print(df.to_string(index=False))

    fg = fetch_fear_greed_crypto()
    if fg:
        print(f"\n>>> Fear & Greed (Krypto): {fg['value']}/100 — {fg['classification']}")

    sectors = fetch_sector_performance()
    if sectors:
        print("\n>>> US-Sektoren (5d)")
        for s in sectors:
            print(f"  {s['etf']:5} {s['sector']:25} {s['change_5d']:+.2f}% (5d) | {s['change_30d']:+.2f}% (30d)")

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
