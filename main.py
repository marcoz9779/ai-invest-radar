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

# macOS Python 3.14 SSL-Workaround: _ssl-Modul kann certifi-Bundle nicht lesen.
# Wir setzen das System-Cert-Bundle als Default für requests/urllib3.
if "REQUESTS_CA_BUNDLE" not in os.environ:
    for candidate in ("/etc/ssl/cert.pem",
                      "/usr/local/etc/openssl/cert.pem",
                      "/opt/homebrew/etc/openssl/cert.pem"):
        if os.path.exists(candidate):
            os.environ["REQUESTS_CA_BUNDLE"] = candidate
            os.environ.setdefault("SSL_CERT_FILE", candidate)
            break

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
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

NEWS_LOOKBACK_DAYS = 7
MAX_HEADLINES_PER_TICKER = 5  # mehr Quellen → mehr Headlines anzeigen
MAX_REDDIT_POSTS_PER_TICKER = 5
EARNINGS_LOOKAHEAD_DAYS = 14  # zeige Earnings die innerhalb der nächsten X Tage anstehen


# ============================================================================
# GLOSSARY — zentrale Definitionen + Datenquellen (für Tooltips im Dashboard)
# ============================================================================
GLOSSARY = {
    "RSI": "Relative Strength Index (0-100). Momentum-Indikator. <30 = oversold (oft Kauf-Setup), >70 = overbought (Vorsicht). · Quelle: yfinance OHLC + ta.momentum.RSIIndicator (Wilder, 1978)",
    "MACD": "Moving Average Convergence Divergence. macd_diff = MACD-Linie minus Signal-Linie. >0 bullish, <0 bearish. · Quelle: yfinance OHLC + ta.trend.MACD (Appel, 1979)",
    "SMA": "Simple Moving Average. SMA20 = Mittelwert der letzten 20 Tages-Closes. Preis > SMA20 > SMA50 = Aufwärtstrend. · Quelle: yfinance + ta.trend.SMAIndicator",
    "Score": "Eigener Multi-Signal-Score (-5 bis +9). Aggregiert RSI, MACD, SMA-Konfluenz, Volume-Spike, 52w-Position, Insider-Activity, Reddit-Velocity, Earnings, AI-Sentiment.",
    "Volume-Spike": "Heutiges Handelsvolumen vs 20-Tage-Durchschnitt. >2x normales Volumen = +1 Score (signifikantes Interesse). · Quelle: yfinance/Binance OHLCV",
    "Volume-Outlier": "Statistischer Outlier auf Volume (z-Score >= 2.5). Extreme Aktivität die nicht zum normalen Pattern passt. · Berechnung: (heute - μ20d) / σ20d",
    "Return-Outlier": "Statistischer Outlier auf Tages-Return (z-Score >= 2.5). Extreme Tagesbewegung. · Berechnung: z-Score auf pct_change Series",
    "Bollinger-Squeeze": "Niedrige Volatilität (enge Bollinger-Bänder) — oft Vorbote eines Breakouts.",
    "52w-Position": "Wie weit Preis vom 52-Wochen-Hoch/Tief entfernt ist. Nahe Tief = oversold-Bonus, nahe Hoch = Risiko.",
    "Insider-Käufe": "Käufe durch CEO/CFO/Insider in den letzten 30 Tagen. ≥2 Käufe mit positivem Net-Volumen = bullish Signal (+1 Score). · Quelle: Finnhub /stock/insider-transactions (gratis)",
    "Insider-Verkäufe": "Verkäufe durch Insider. ≥3 Verkäufe mit negativem Net-Volumen = bearish (-1 Score). · Quelle: Finnhub",
    "Beat-Rate": "Anteil der letzten 4 Quartale, in denen EPS-Actual > EPS-Estimate. ≥75% bei improving Trend = +1 Score. · Quelle: Finnhub /stock/earnings",
    "Earnings-Trend": "Vergleich der 2 letzten Quartale vs ältere 2: improving, stable, deteriorating.",
    "Reddit-Velocity": "24h-Mentions-Rate vs 7-Tage-Durchschnitt. ≥2.5x mit ≥3 Mentions = Hype-Spike, +1 Score. · Quelle: Reddit public JSON-Endpoint (anonym)",
    "Reddit-Mentions": "Anzahl Posts mit Ticker-Erwähnung in 10 Subreddits (wallstreetbets, stocks, investing, StockMarket, options, ValueInvesting, SecurityAnalysis, dividends, pennystocks, Daytrading).",
    "News-Sentiment-Ratio": "Anteil bullish vs bearish gestaffelter Headlines (alle Quellen). ≥70% bullish = +1, ≤30% = -1. · Quellen: Marketaux (mit AI-Sentiment), StockTwits, Yahoo, Google News, Hacker News",
    "Wiki-Aufmerksamkeit": "Wikipedia-Page-Views heute vs 14-Tage-Ø. ≥1.8x = signifikanter Aufmerksamkeits-Spike (oft vor Earnings/News). · Quelle: Wikimedia REST API (gratis)",
    "Money-Flow-Score": "Sektor-Performance + Volume-Trend (30% gewichtet). Positive Werte = Kapital fließt rein. · Quelle: yfinance Sektor-ETFs (XLK/XLF/XLE/...)",
    "Fear-Greed": "Krypto-Markt-Sentiment 0-100. <25 Extreme Fear (oft Kauf-Setup), >75 Extreme Greed (Vorsicht). · Quelle: alternative.me Fear & Greed Index",
    "Put-Call-Ratio": "Volume Put-Optionen / Volume Call-Optionen. <0.7 bullish, >1.0 bearish. · Quelle: yfinance Options-Chain (nächste Expiry)",
    "Unusual-Options": "Options-Strikes mit Volume ≥2x Open Interest und ≥100 Volumen. Oft Insider-Wetten oder Vor-News-Positionierung. · Quelle: yfinance Options-Chain",
    "Claude-AI-Sentiment": "Claude Haiku 4.5 liest die letzten News + Top-Reddit-Posts und gibt eine ganzheitliche Bewertung (-1 bis +1). · Quelle: Anthropic API (~$3-5/Monat)",
    "STRONG-BUY": "Multi-Signal-Pattern: ≥3 unabhängige bullish Signale konvergieren (Tech-Score + Reddit-Spike + Insider-Käufe + News-Bullish-Ratio). Strenger Auswahlfilter.",
    "STRONG-SELL": "Multi-Signal-Pattern: ≥3 unabhängige bearish Signale konvergieren. Symmetrisch zu STRONG-BUY.",
    "BUY": "Score ≥ 3 → Käufer-Setup. Mehrere Indikatoren bestätigen.",
    "WATCH": "Score 1-2 → Beobachten. Erste Signale aber noch keine starke Konvergenz.",
    "HOLD": "Score -1 bis 0 → Neutral, kein klares Signal.",
    "REDUCE": "Score -2 → Position reduzieren. Bearish-Signale überwiegen leicht.",
    "SELL": "Score ≤ -3 → Exit-Setup. Mehrere Indikatoren bestätigen Bearish.",
    "Wildcard": "Reddit-Trending Small/Mid-Cap der NICHT im Top-40-Universum ist. Erkannt durch Scan von WSB/r/stocks-hot-posts. Höhere Volatilität, höheres Risiko.",
    "Backtest-SQLite": "Simulation auf echten historischen Multi-Signal-Scores aus der DB. Wird mit jedem Run reicher.",
    "Backtest-Tech": "Simulation nur auf Tech-Indikatoren-Score (RSI/MACD/SMA), berechnet jedem Tag aus den 90d OHLC.",
}


def gloss(label: str, key: str) -> str:
    """Wrappt ein Label in einen HTML-Span mit Hover-Tooltip aus GLOSSARY.

    Nur in Streamlit-Kontext mit unsafe_allow_html=True nutzbar.
    """
    info = GLOSSARY.get(key, "")
    if not info:
        return label
    # Quote-Escape
    safe = info.replace('"', "&quot;").replace("'", "&#39;")
    return (
        f'<span title="{safe}" '
        f'style="border-bottom:1px dotted #b0b0b0; cursor:help;">{label}</span>'
    )

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
        "logo": STOCK_DOMAINS.get(ticker, ""),  # nur Domain — Logo-URL wird im Render mit Fallback gebaut
    }


def analyze_stock(ticker: str) -> dict | None:
    df = fetch_stock_data(ticker)
    if df is None:
        return None
    return analyze_stock_df(df, ticker)


def fetch_fundamentals(ticker: str) -> dict:
    """Fundamentaldaten via yfinance.Ticker.info (gratis)."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return {}
    return {
        "market_cap": info.get("marketCap"),
        "pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "dividend_yield": info.get("dividendYield"),
        "beta": info.get("beta"),
        "eps_growth": info.get("earningsGrowth"),
        "revenue_growth": info.get("revenueGrowth"),
        "profit_margin": info.get("profitMargins"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "sector_yf": info.get("sector"),
    }


def fetch_fundamentals_bulk(tickers: list[str], max_workers: int = 8) -> dict[str, dict]:
    """Parallele Fundamentaldaten-Fetches."""
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_fundamentals, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                out[t] = fut.result()
            except Exception:
                out[t] = {}
    return out


def fetch_insider_trades(ticker: str) -> dict:
    """Aggregierte Insider-Trades letzte 30 Tage via Finnhub.

    Liefert {buys, sells, net_shares, net_value, last_trade_date}.
    Wenn FINNHUB_API_KEY fehlt, leer.
    """
    empty = {"buys": 0, "sells": 0, "net_shares": 0, "net_value": 0.0, "last_trade_date": None}
    if not FINNHUB_API_KEY:
        return empty
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=30)
    url = "https://finnhub.io/api/v1/stock/insider-transactions"
    params = {"symbol": ticker, "from": since.isoformat(),
              "to": today.isoformat(), "token": FINNHUB_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        items = r.json().get("data", [])
    except Exception:
        return empty
    buys = sells = 0
    net_shares = 0
    net_value = 0.0
    last_date = None
    for it in items:
        change = it.get("change") or 0
        price = it.get("transactionPrice") or 0
        if change > 0:
            buys += 1
        elif change < 0:
            sells += 1
        net_shares += change
        net_value += change * price
        d = it.get("transactionDate") or it.get("filingDate")
        if d and (last_date is None or d > last_date):
            last_date = d
    return {
        "buys": buys, "sells": sells,
        "net_shares": int(net_shares),
        "net_value": round(net_value, 2),
        "last_trade_date": last_date,
    }


def fetch_insider_trades_bulk(tickers: list[str], max_workers: int = 6) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not FINNHUB_API_KEY:
        return {t: {"buys": 0, "sells": 0, "net_shares": 0,
                    "net_value": 0.0, "last_trade_date": None} for t in tickers}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_insider_trades, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                out[t] = fut.result()
            except Exception:
                out[t] = {"buys": 0, "sells": 0, "net_shares": 0,
                          "net_value": 0.0, "last_trade_date": None}
    return out


def news_velocity(headlines: list[dict]) -> tuple[int, float]:
    """Wie viele News heute vs Durchschnitt der letzten N Tage.

    Liefert (today_count, velocity_x). velocity > 1 = mehr News als üblich.
    """
    if not headlines:
        return 0, 0.0
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_count = sum(1 for h in headlines if h.get("date") == today_str)
    avg_per_day = len(headlines) / max(NEWS_LOOKBACK_DAYS, 1)
    velocity = (today_count / avg_per_day) if avg_per_day > 0 else 0
    return today_count, round(velocity, 2)


def fetch_earnings_surprise(ticker: str, n_quarters: int = 4) -> dict:
    """EPS Beat/Miss-Historie via Finnhub. Liefert beat_rate, avg_surprise_%, last_quarters.

    Wenn FINNHUB_API_KEY fehlt → leer.
    """
    empty = {"beat_rate": None, "avg_surprise_pct": None, "quarters": [], "trend": None}
    if not FINNHUB_API_KEY:
        return empty
    url = "https://finnhub.io/api/v1/stock/earnings"
    params = {"symbol": ticker, "limit": n_quarters, "token": FINNHUB_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        items = r.json() or []
    except Exception:
        return empty
    if not items:
        return empty
    quarters = []
    beats = 0
    surprise_pcts = []
    for it in items:
        actual = it.get("actual")
        estimate = it.get("estimate")
        if actual is None or estimate is None or estimate == 0:
            continue
        surprise_pct = (actual - estimate) / abs(estimate) * 100
        beat = surprise_pct > 0
        if beat:
            beats += 1
        surprise_pcts.append(surprise_pct)
        quarters.append({
            "period": it.get("period"),
            "actual": actual,
            "estimate": estimate,
            "surprise_pct": round(surprise_pct, 2),
            "beat": beat,
        })
    if not quarters:
        return empty
    beat_rate = beats / len(quarters)
    avg_surprise = sum(surprise_pcts) / len(surprise_pcts)
    # Trend: letzten 2 Quartale vs ältere 2 — verbessert sich's?
    trend = None
    if len(surprise_pcts) >= 4:
        recent = sum(surprise_pcts[:2]) / 2
        older = sum(surprise_pcts[2:4]) / 2
        if recent > older + 5:
            trend = "improving"
        elif recent < older - 5:
            trend = "deteriorating"
        else:
            trend = "stable"
    return {
        "beat_rate": round(beat_rate, 2),
        "avg_surprise_pct": round(avg_surprise, 2),
        "quarters": quarters,
        "trend": trend,
    }


def fetch_earnings_surprise_bulk(tickers: list[str], max_workers: int = 6) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not FINNHUB_API_KEY:
        return {t: {"beat_rate": None, "avg_surprise_pct": None, "quarters": [], "trend": None}
                for t in tickers}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_earnings_surprise, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                out[t] = fut.result()
            except Exception:
                out[t] = {"beat_rate": None, "avg_surprise_pct": None,
                          "quarters": [], "trend": None}
    return out


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
    """Bulk-Variante: scort alle US_STOCKS inkl. Earnings + Fundamentals + Insider + Surprise + Anomaly."""
    data = fetch_stock_data_bulk(US_STOCKS)
    tickers_with_data = list(data.keys())

    # Parallele Zusatz-Fetches
    earnings = fetch_earnings_calendar_bulk(tickers_with_data)
    fundamentals = fetch_fundamentals_bulk(tickers_with_data)
    insider = fetch_insider_trades_bulk(tickers_with_data)
    earnings_surprise = fetch_earnings_surprise_bulk(tickers_with_data)

    results = []
    for t in US_STOCKS:
        if t not in data:
            continue
        try:
            row = analyze_stock_df(data[t], t, earnings.get(t))
            row["fundamentals"] = fundamentals.get(t, {})
            row["insider"] = insider.get(t, {"buys": 0, "sells": 0,
                                              "net_shares": 0, "net_value": 0.0,
                                              "last_trade_date": None})
            row["earnings_surprise"] = earnings_surprise.get(t, {})
            row["anomaly"] = detect_anomalies(data[t], t)

            # Insider-Boost in Score
            ins = row["insider"]
            if ins["buys"] >= 2 and ins["net_shares"] > 0:
                row["score"] += 1
                row["signals"] += ", Insider-Käufe"
            elif ins["sells"] >= 3 and ins["net_shares"] < 0:
                row["score"] -= 1
                row["signals"] += ", Insider-Verkäufe"

            # Earnings-Beat-Rate-Boost
            es = row["earnings_surprise"]
            if es.get("beat_rate", 0) and es["beat_rate"] >= 0.75 and es.get("trend") == "improving":
                row["score"] += 1
                row["signals"] += f", Earnings-Beat-Rate {int(es['beat_rate']*100)}%"
            elif es.get("beat_rate", 1) and es["beat_rate"] <= 0.25:
                row["score"] -= 1
                row["signals"] += f", schwache Earnings-Rate {int(es['beat_rate']*100)}%"

            # Anomaly-Outlier-Highlight
            an = row["anomaly"]
            if an.get("is_volume_outlier") or an.get("is_return_outlier"):
                row["signals"] += ", Anomalie erkannt"

            results.append(row)
        except Exception:
            continue
    return results, data


# ============================================================================
# KRYPTO-ANALYSE
# ============================================================================
def fetch_crypto_ohlcv_binance(ticker: str, days: int = 90) -> pd.DataFrame | None:
    """OHLCV via Binance Public API (gratis, 1200 req/min, KEIN Key).

    Versucht USDT-Paar zuerst, fällt auf BUSD/USDC zurück. Liefert auch Volume.
    """
    for quote in ("USDT", "BUSD", "USDC"):
        symbol = f"{ticker.upper()}{quote}"
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": "1d", "limit": days}
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 400:
                continue  # Symbol existiert nicht, nächstes Quote-Paar probieren
            r.raise_for_status()
            data = r.json()
            if not data or len(data) < 30:
                continue
            df = pd.DataFrame(data, columns=[
                "open_time", "Open", "High", "Low", "Close", "Volume",
                "close_time", "qav", "ntrades", "tbbav", "tbqav", "ignore",
            ])
            df["Open"] = df["Open"].astype(float)
            df["High"] = df["High"].astype(float)
            df["Low"] = df["Low"].astype(float)
            df["Close"] = df["Close"].astype(float)
            df["Volume"] = df["Volume"].astype(float)
            df.index = pd.to_datetime(df["open_time"], unit="ms")
            return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            continue
    return None


def fetch_crypto_ohlc_bulk(tickers: list[str], max_workers: int = 12) -> dict[str, pd.DataFrame]:
    """Parallel via Binance (gratis, hohe Limits → wir können aggressive parallelisieren).

    Erwartet eine Liste von Krypto-TICKERN (z.B. ["BTC", "ETH", ...]), nicht IDs.
    """
    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_crypto_ohlcv_binance, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                df = fut.result()
                if df is not None:
                    out[t] = df
            except Exception:
                continue
    return out


def analyze_crypto(top_n: int = 40) -> tuple[list[dict], dict[str, pd.DataFrame]]:
    """Holt Top-N Kryptos + OHLCV (Binance) und scort sie mit RSI/MACD/SMA + Volume.

    Liefert (rows, ohlc_dict). ohlc_dict ist gekeyed nach TICKER.
    Marketcap-Ranking via CoinGecko (1 Call, gecacht). OHLCV via Binance (gratis).
    """
    coins = get_top_cryptos(top_n)
    if not coins:
        return [], {}

    tickers = [c["ticker"] for c in coins.values()]
    ohlc_by_ticker = fetch_crypto_ohlc_bulk(tickers)

    results = []
    for cid, c in coins.items():
        ticker = c["ticker"]
        df = ohlc_by_ticker.get(ticker)

        # Tech-Indikatoren wenn OHLCV verfügbar (jetzt mit echtem Volume!)
        ind_score = 0
        ind_signals: list[str] = []
        rsi = None
        if df is not None and len(df) >= 30:
            try:
                ind = _compute_indicators(df)
                ind_score, ind_signals = _score_from_indicators(ind, False)
                rsi = round(ind["rsi"], 1)
            except Exception:
                pass

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
# WIKIPEDIA PAGE-VIEWS (unterbewerteter Aufmerksamkeits-Indikator, gratis)
# ============================================================================
WIKI_PAGE_MAP = {
    # Custom mappings — Wikipedia-Page-Title für jeden Ticker
    "NVDA": "Nvidia", "MSFT": "Microsoft", "AAPL": "Apple_Inc.",
    "AMZN": "Amazon_(company)", "GOOGL": "Alphabet_Inc.",
    "META": "Meta_Platforms", "AVGO": "Broadcom", "TSLA": "Tesla,_Inc.",
    "BRK-B": "Berkshire_Hathaway", "LLY": "Eli_Lilly_and_Company",
    "JPM": "JPMorgan_Chase", "V": "Visa_Inc.", "WMT": "Walmart",
    "XOM": "ExxonMobil", "MA": "Mastercard", "UNH": "UnitedHealth_Group",
    "ORCL": "Oracle_Corporation", "COST": "Costco",
    "JNJ": "Johnson_%26_Johnson", "PG": "Procter_%26_Gamble",
    "NFLX": "Netflix", "HD": "The_Home_Depot",
    "BAC": "Bank_of_America", "ABBV": "AbbVie",
    "CRM": "Salesforce", "KO": "The_Coca-Cola_Company",
    "CVX": "Chevron_Corporation", "AMD": "Advanced_Micro_Devices",
    "MRK": "Merck_%26_Co.", "PEP": "PepsiCo", "ADBE": "Adobe_Inc.",
    "ACN": "Accenture", "CSCO": "Cisco", "TMO": "Thermo_Fisher_Scientific",
    "MCD": "McDonald%27s", "LIN": "Linde_plc", "IBM": "IBM",
    "PLTR": "Palantir_Technologies", "COIN": "Coinbase", "INTC": "Intel",
}


def fetch_wikipedia_pageviews(page_title: str, days: int = 14) -> dict:
    """Wikipedia-Page-Views der letzten N Tage. Kein Key, gratis.

    Liefert {today, avg_baseline, spike_ratio, daily_series}.
    """
    today = datetime.now()
    end_date = today.strftime("%Y%m%d")
    start_date = (today - timedelta(days=days)).strftime("%Y%m%d")
    url = (
        f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia.org/all-access/all-agents/{page_title}/daily/"
        f"{start_date}/{end_date}"
    )
    try:
        r = requests.get(url, timeout=10,
                         headers={"User-Agent": "ai-invest-radar/0.1"})
        r.raise_for_status()
        items = r.json().get("items", [])
    except Exception:
        return {"today": 0, "avg_baseline": 0, "spike_ratio": 0, "daily_series": []}
    if not items:
        return {"today": 0, "avg_baseline": 0, "spike_ratio": 0, "daily_series": []}
    series = [(it["timestamp"][:8], int(it["views"])) for it in items]
    views = [v for _, v in series]
    today_views = views[-1] if views else 0
    baseline = sum(views[:-1]) / max(len(views) - 1, 1) if len(views) > 1 else today_views
    spike = (today_views / baseline) if baseline > 0 else 0
    return {
        "today": today_views,
        "avg_baseline": round(baseline, 1),
        "spike_ratio": round(spike, 2),
        "daily_series": series,
    }


def fetch_wikipedia_pageviews_bulk(tickers: list[str], max_workers: int = 8) -> dict[str, dict]:
    out: dict[str, dict] = {}
    def work(t):
        page = WIKI_PAGE_MAP.get(t)
        if not page:
            return t, None
        return t, fetch_wikipedia_pageviews(page)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(work, t) for t in tickers]
        for fut in as_completed(futures):
            try:
                t, data = fut.result()
                if data is not None:
                    out[t] = data
            except Exception:
                continue
    return out


# ============================================================================
# COINGECKO TRENDING (was ist heute populär)
# ============================================================================
def fetch_coingecko_trending() -> list[dict]:
    """Top-7 Trending Coins der letzten 24h via CoinGecko (gratis, 1 Call)."""
    url = "https://api.coingecko.com/api/v3/search/trending"
    data = _coingecko_get(url, {})
    if not data:
        return []
    out = []
    for item in (data.get("coins") or [])[:7]:
        coin = item.get("item", {})
        out.append({
            "id": coin.get("id"),
            "ticker": (coin.get("symbol", "") or "").upper(),
            "name": coin.get("name", ""),
            "rank": coin.get("market_cap_rank"),
            "score": coin.get("score", 0),
        })
    return out


# ============================================================================
# OPTIONS-FLOW (unusual Activity, Aktien only)
# ============================================================================
def fetch_options_flow(ticker: str) -> dict:
    """Schaut auf nächsten Optionschain — unusual Volume vs Open Interest.

    Liefert {total_call_volume, total_put_volume, put_call_ratio, unusual_strikes, expiry}.
    """
    empty = {"total_call_volume": 0, "total_put_volume": 0,
             "put_call_ratio": None, "unusual_strikes": [], "expiry": None}
    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return empty
        # Nächste Expiry
        next_exp = expirations[0]
        chain = t.option_chain(next_exp)
        calls = chain.calls
        puts = chain.puts
    except Exception:
        return empty

    total_call_vol = int(calls["volume"].fillna(0).sum()) if "volume" in calls.columns else 0
    total_put_vol = int(puts["volume"].fillna(0).sum()) if "volume" in puts.columns else 0
    pc_ratio = (total_put_vol / total_call_vol) if total_call_vol > 0 else None

    # Unusual: volume > 2x open interest UND volume >= 100
    unusual = []
    for kind, df in (("CALL", calls), ("PUT", puts)):
        if "volume" not in df.columns or "openInterest" not in df.columns:
            continue
        for _, row in df.iterrows():
            vol = row.get("volume") or 0
            oi = row.get("openInterest") or 0
            if vol < 100 or oi <= 0:
                continue
            if vol >= 2 * oi:
                unusual.append({
                    "type": kind,
                    "strike": float(row["strike"]),
                    "volume": int(vol),
                    "open_interest": int(oi),
                    "vol_oi_ratio": round(vol / oi, 1),
                    "last_price": float(row.get("lastPrice") or 0),
                })
    unusual.sort(key=lambda x: x["vol_oi_ratio"], reverse=True)
    return {
        "total_call_volume": total_call_vol,
        "total_put_volume": total_put_vol,
        "put_call_ratio": round(pc_ratio, 2) if pc_ratio is not None else None,
        "unusual_strikes": unusual[:5],
        "expiry": next_exp,
    }


def fetch_options_flow_bulk(tickers: list[str], max_workers: int = 6) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_options_flow, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                out[t] = fut.result()
            except Exception:
                out[t] = {"total_call_volume": 0, "total_put_volume": 0,
                          "put_call_ratio": None, "unusual_strikes": [], "expiry": None}
    return out


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


def fetch_news_stocktwits(ticker: str) -> list[dict]:
    """StockTwits: Retail-Trader-Sentiment für Aktien, gratis, KEIN Key.

    Jede Message hat optional ein explicit "Bullish" oder "Bearish" Tag.
    """
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "ai-invest-radar/0.1"})
        if r.status_code != 200:
            return []
        messages = r.json().get("messages", [])
    except Exception:
        return []
    out = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)).timestamp()
    for m in messages[:MAX_HEADLINES_PER_TICKER * 3]:
        body = (m.get("body") or "").strip()
        if not body:
            continue
        created = m.get("created_at", "")
        try:
            ts = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            if ts < cutoff:
                continue
        except Exception:
            pass
        ent = m.get("entities") or {}
        sent_basic = (ent.get("sentiment") or {}).get("basic")
        sentiment = 0.6 if sent_basic == "Bullish" else -0.6 if sent_basic == "Bearish" else None
        out.append({
            "date": created[:10],
            "source": f"@{m.get('user', {}).get('username', 'StockTwits')}",
            "headline": body[:200],
            "url": f"https://stocktwits.com/{m.get('user', {}).get('username', '')}/message/{m.get('id', '')}",
            "sentiment": sentiment,
            "provider": "stocktwits",
        })
    return out[:MAX_HEADLINES_PER_TICKER]


def fetch_news_cryptopanic(ticker: str) -> list[dict]:
    """CryptoPanic: kuratierte Krypto-News + Community-Bull/Bear-Votes."""
    if not CRYPTOPANIC_API_KEY:
        return []
    url = "https://cryptopanic.com/api/v1/posts/"
    params = {
        "auth_token": CRYPTOPANIC_API_KEY,
        "currencies": ticker,
        "public": "true",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return []
    out = []
    for p in results[:MAX_HEADLINES_PER_TICKER]:
        votes = p.get("votes") or {}
        pos = int(votes.get("positive") or 0)
        neg = int(votes.get("negative") or 0)
        sentiment = None
        if pos + neg > 0:
            sentiment = (pos - neg) / (pos + neg)  # -1 .. +1
        out.append({
            "date": (p.get("created_at") or "")[:10],
            "source": (p.get("source") or {}).get("title", "CryptoPanic"),
            "headline": (p.get("title") or "").strip(),
            "url": p.get("url", ""),
            "sentiment": sentiment,
            "provider": "cryptopanic",
        })
    return out


NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.tiekoetter.com",
    "https://nitter.lunar.icu",
]


def fetch_news_hackernews(ticker: str, name: str | None = None) -> list[dict]:
    """Hacker News via Algolia-Search-API. Gratis, kein Key.

    Sucht in HN-Stories der letzten 14 Tage nach Ticker oder Firma.
    Bei Tech-Aktien (NVDA, AAPL, MSFT, ...) oft sehr substanzielle Diskussionen.
    """
    cutoff_ts = int((datetime.now() - timedelta(days=NEWS_LOOKBACK_DAYS)).timestamp())
    query = name or ticker
    url = "https://hn.algolia.com/api/v1/search"
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{cutoff_ts},points>5",
        "hitsPerPage": MAX_HEADLINES_PER_TICKER,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        hits = r.json().get("hits", [])
    except Exception:
        return []
    out = []
    for h in hits:
        title = (h.get("title") or "").strip()
        if not title:
            continue
        # Filter: muss Ticker oder Firma im Titel haben (Algolia matched auch Body)
        up_title = title.upper()
        if ticker.upper() not in up_title and (name or "").upper() not in up_title:
            continue
        created = h.get("created_at_i") or 0
        date = datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d") if created else ""
        points = int(h.get("points") or 0)
        # HN-Sentiment: hohe Points + Tech-Diskussion → mild bullish
        sentiment = None
        if points >= 100:
            sentiment = 0.3
        elif points >= 30:
            sentiment = 0.15
        out.append({
            "date": date,
            "source": f"HackerNews ({points} pts)",
            "headline": title[:200],
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "sentiment": sentiment,
            "provider": "hackernews",
        })
    return out


def fetch_news_nitter(ticker: str) -> list[dict]:
    """Twitter/X-Suche via Nitter RSS — gratis, mehrere Mirror als Fallback.

    Wird oft instabil — graceful skip wenn alle Mirror down.
    """
    query = f"${ticker}"
    for base in NITTER_INSTANCES:
        url = f"{base}/search/rss?f=tweets&q={quote_plus(query)}"
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                continue
            cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)
            out = []
            for entry in feed.entries[:MAX_HEADLINES_PER_TICKER * 2]:
                try:
                    pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pub = datetime.now(timezone.utc)
                if pub < cutoff:
                    continue
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                # Sehr einfaches Sentiment: positive/negative Wort-Counts
                low = title.lower()
                pos_kw = ["up", "bull", "rally", "moon", "buy", "long", "rocket",
                          "🚀", "📈", "beat", "surge", "rip"]
                neg_kw = ["down", "bear", "crash", "dump", "sell", "short", "puts",
                          "📉", "miss", "drop", "tank", "rug"]
                p_cnt = sum(1 for k in pos_kw if k in low)
                n_cnt = sum(1 for k in neg_kw if k in low)
                sentiment = None
                if p_cnt + n_cnt > 0:
                    sentiment = (p_cnt - n_cnt) / (p_cnt + n_cnt) * 0.5
                out.append({
                    "date": pub.strftime("%Y-%m-%d"),
                    "source": "X/Twitter",
                    "headline": title[:200],
                    "url": entry.get("link", ""),
                    "sentiment": sentiment,
                    "provider": "nitter",
                })
            if out:
                return out[:MAX_HEADLINES_PER_TICKER]
        except Exception:
            continue
    return []


def fetch_rss_topstories() -> list[dict]:
    """Globale Finanz-RSS-Feeds; einmal pro Run, dann pro Ticker gefiltert."""
    feeds = {
        "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
        "CNBC Markets": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
        "CNBC Investing": "https://www.cnbc.com/id/100727362/device/rss/rss.html",
        "Reuters Business": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
    }
    all_items: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)
    for src, url in feeds.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:30]:
                try:
                    pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pub = datetime.now(timezone.utc)
                if pub < cutoff:
                    continue
                all_items.append({
                    "date": pub.strftime("%Y-%m-%d"),
                    "source": src,
                    "headline": (entry.get("title") or "").strip(),
                    "url": entry.get("link", ""),
                    "sentiment": None,
                    "provider": "rss-top",
                    "_search_text": (
                        (entry.get("title") or "")
                        + " "
                        + (entry.get("summary") or "")
                    ).upper(),
                })
        except Exception:
            continue
    return all_items


def filter_rss_for_ticker(rss_items: list[dict], ticker: str, name: str = "") -> list[dict]:
    """Sucht Top-RSS-Stories die Ticker oder Firmen-Name erwähnen."""
    needles = [f"${ticker.upper()}", f" {ticker.upper()} "]
    if name:
        needles.append(name.upper())
    out = []
    for item in rss_items:
        text = item.get("_search_text", "")
        for needle in needles:
            if needle in text:
                out.append({k: v for k, v in item.items() if not k.startswith("_")})
                break
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


def fetch_news(
    ticker: str,
    query_name: str | None = None,
    asset_type: str = "stock",
    rss_pool: list[dict] | None = None,
) -> list[dict]:
    """Aggregiert News aus ALLEN verfügbaren Quellen parallel + dedupliziert.

    Aktien: Marketaux + Finnhub + Yahoo + Google + StockTwits + RSS-Pool
    Krypto: Marketaux + Google + CryptoPanic + RSS-Pool
    """
    name = query_name or ticker

    if asset_type == "crypto":
        fetchers = [
            (fetch_news_marketaux, ticker),
            (fetch_news_google_rss, name),
            (fetch_news_cryptopanic, ticker),
            (fetch_news_nitter, ticker),
        ]
    else:
        fetchers = [
            (fetch_news_marketaux, ticker),
            (fetch_news_finnhub, ticker),
            (fetch_news_yahoo, ticker),
            (fetch_news_google_rss, name),
            (fetch_news_stocktwits, ticker),
            (fetch_news_nitter, ticker),
        ]

    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fn, arg): fn.__name__ for fn, arg in fetchers}
        for fut in as_completed(futures):
            try:
                items.extend(fut.result())
            except Exception:
                continue

    # Hacker News (nur für Aktien, name optional)
    if asset_type == "stock":
        try:
            items.extend(fetch_news_hackernews(ticker, name if name != ticker else None))
        except Exception:
            pass

    # Global RSS-Top-Stories filtern, falls Pool übergeben
    if rss_pool:
        items.extend(filter_rss_for_ticker(rss_pool, ticker, name))

    return _dedup_news(items)


def news_sentiment_ratio(headlines: list[dict]) -> dict:
    """Aggregiert Sentiment aus allen Headlines mit Score.

    Liefert {bullish, bearish, neutral, total_scored, ratio}.
    ratio: 0..1, wobei 1 = nur bullish, 0 = nur bearish, 0.5 = neutral.
    """
    bullish = bearish = neutral = 0
    for h in headlines:
        s = h.get("sentiment")
        if s is None:
            continue
        if s > 0.15:
            bullish += 1
        elif s < -0.15:
            bearish += 1
        else:
            neutral += 1
    total = bullish + bearish + neutral
    if total == 0:
        return {"bullish": 0, "bearish": 0, "neutral": 0,
                "total_scored": 0, "ratio": None}
    ratio = bullish / (bullish + bearish) if (bullish + bearish) > 0 else 0.5
    return {"bullish": bullish, "bearish": bearish, "neutral": neutral,
            "total_scored": total, "ratio": round(ratio, 2)}


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


_DISCOVERY_BLACKLIST = {
    "USA", "USD", "CEO", "CFO", "CTO", "IPO", "ETF", "ETH", "BTC", "API",
    "EPS", "PE", "PR", "DD", "FOMO", "YOLO", "ATH", "ATL", "EOD", "EOY",
    "IRA", "401K", "SPY", "QQQ", "SPX", "VIX", "FOMC", "FED", "GDP", "CPI",
    "RH", "WSB", "TLDR", "TLDW", "PT", "WTF", "OMG", "LOL",
    "OPS", "GAAP", "EBIT", "EBITDA", "FY", "FCF", "ROE", "ROI",
    "RSI", "MACD", "SMA", "EMA", "MA",
    "I", "A", "AI", "EV", "OG",
    "EU", "UK", "DE", "JP", "CN", "PM", "AM",
    "MOD", "OP", "POST", "TLD", "URL", "PDF",
    "NYSE", "NASDAQ", "SEC", "FDIC", "IRS",
    "CEO", "COO", "VP",
    "ITM", "OTM", "CALL", "PUT", "EPS",
    "DAY", "WEEK", "YEAR",
    "NEW", "OLD", "TOP", "BUY", "SELL", "HOLD",
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD",
}


def discover_reddit_trending_tickers(subs: str = REDDIT_STOCK_SUBS,
                                      hours: int = 24,
                                      exclude: set[str] | None = None,
                                      max_retries: int = 3) -> list[dict]:
    """Scannt Top-Posts in WSB/Stocks/etc. nach $TICKERS, die NICHT im Universum sind.

    Erkennt Symbole im Stil $TSLA oder GME (3-5 Großbuchstaben).
    Hat 429-Retry mit Backoff, weil Reddit public Endpoint streng ist.
    """
    import re
    exclude = exclude or set()

    url = f"https://www.reddit.com/r/{subs}/hot.json"
    headers = {"User-Agent": REDDIT_USER_AGENT}
    params = {"limit": 100}

    posts: list[dict] = []
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=20)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            posts = data.get("data", {}).get("children", [])
            if posts:
                break
        except Exception:
            time.sleep(2)
            continue
    if not posts:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    ticker_pattern = re.compile(r"\$([A-Z]{1,5})\b|\b([A-Z]{3,5})\b")
    counts: dict[str, dict] = {}

    for child in posts:
        d = child.get("data", {})
        if d.get("created_utc", 0) < cutoff:
            continue
        title = (d.get("title") or "").strip()
        body = (d.get("selftext") or "")[:500]
        text = f" {title} {body}"
        score = int(d.get("score", 0))

        for match in ticker_pattern.finditer(text):
            symbol = match.group(1) or match.group(2)
            if not symbol or symbol in _DISCOVERY_BLACKLIST or symbol in exclude:
                continue
            if symbol not in counts:
                counts[symbol] = {
                    "ticker": symbol,
                    "mentions": 0,
                    "total_score": 0,
                    "top_title": title[:120],
                    "top_score": score,
                    "subreddits": set(),
                }
            counts[symbol]["mentions"] += 1
            counts[symbol]["total_score"] += score
            counts[symbol]["subreddits"].add(d.get("subreddit", ""))
            if score > counts[symbol]["top_score"]:
                counts[symbol]["top_title"] = title[:120]
                counts[symbol]["top_score"] = score

    # Validierung: nur Tickers mit ≥3 Mentions ODER 1 Mention mit hohem Score
    out = []
    for symbol, info in counts.items():
        if info["mentions"] >= 3 or info["total_score"] >= 500:
            info["subreddits"] = sorted(info["subreddits"])
            out.append(info)
    out.sort(key=lambda x: (x["mentions"], x["total_score"]), reverse=True)
    return out


def validate_trending_ticker(ticker: str) -> dict | None:
    """Prüft via yfinance ob ein Symbol als Aktien-Ticker existiert + holt Basis-Daten."""
    try:
        info = yf.Ticker(ticker).info or {}
        if not info or info.get("regularMarketPrice") is None:
            return None
        return {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName") or ticker,
            "price": float(info.get("regularMarketPrice", 0)),
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector", ""),
        }
    except Exception:
        return None


def analyze_wildcard_tickers(max_tickers: int = 15) -> tuple[list[dict], dict[str, pd.DataFrame]]:
    """Pipeline: Reddit-Trending → Validate → OHLC-Fetch → Score wie Aktien.

    Liefert (wildcard_rows, ohlc_dict) — kompatibel mit analyze_all_stocks-Format.
    Setzt 'is_wildcard'=True + 'hype_metadata' für UI-Hinweise.
    """
    trending = discover_reddit_trending_tickers(
        REDDIT_STOCK_SUBS, hours=48, exclude=set(US_STOCKS),
    )
    if not trending:
        return [], {}

    # Validate parallel
    candidates: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(validate_trending_ticker, t["ticker"]): t
                for t in trending[:max_tickers * 2]}
        for fut in as_completed(futs):
            hype = futs[fut]
            try:
                v = fut.result()
                if v is not None:
                    v["hype_metadata"] = hype
                    candidates.append(v)
            except Exception:
                continue

    # Sort by mentions × log(ups), take top-N
    import math
    candidates.sort(
        key=lambda x: (x["hype_metadata"]["mentions"]
                       * math.log10(max(x["hype_metadata"]["total_score"], 1) + 1)),
        reverse=True,
    )
    candidates = candidates[:max_tickers]
    if not candidates:
        return [], {}

    tickers = [c["ticker"] for c in candidates]
    ohlc = fetch_stock_data_bulk(tickers)

    rows = []
    for c in candidates:
        t = c["ticker"]
        if t not in ohlc:
            continue
        try:
            row = analyze_stock_df(ohlc[t], t, None)
            row["name"] = c.get("name", t)
            row["market_cap"] = c.get("market_cap")
            row["sector_yf"] = c.get("sector", "")
            row["is_wildcard"] = True
            row["hype_metadata"] = c["hype_metadata"]
            # Hype-Boost: bei vielen Reddit-Mentions +1
            hm = c["hype_metadata"]
            if hm["mentions"] >= 5 or hm["total_score"] >= 1000:
                row["score"] += 1
                row["signals"] += f", WSB-Hype {hm['mentions']}× ({hm['total_score']:,} ups)"
            rows.append(row)
        except Exception:
            continue
    return rows, ohlc


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
# MULTI-SIGNAL PATTERN-DETECTION (STRONG BUY / STRONG SELL)
# ============================================================================
def multi_signal_pattern(asset: dict) -> tuple[str | None, list[str]]:
    """Erkennt Konvergenz mehrerer unabhängiger Signale.

    STRONG BUY/SELL nur wenn ≥3 unabhängige Signale in dieselbe Richtung zeigen.
    Liefert (label, list_of_contributing_signals).
    """
    score = asset.get("score", 0)
    buzz = asset.get("buzz") or {}
    insider = asset.get("insider") or {}
    news_ratio = (asset.get("news_sentiment") or {}).get("ratio")
    has_earnings_soon = bool(asset.get("earnings_date"))

    bullish: list[str] = []
    bearish: list[str] = []

    # 1) Tech-Score
    if score >= 3:
        bullish.append("Tech-Score+3")
    elif score >= 1 and has_earnings_soon:
        bullish.append("Earnings + bullish Tech")
    elif score <= -3:
        bearish.append("Tech-Score-3")

    # 2) Reddit-Buzz
    if buzz.get("velocity", 0) >= 2.5 and buzz.get("mentions_24h", 0) >= 3:
        bullish.append(f"Reddit-Spike {buzz['velocity']}x")

    # 3) Insider-Trades
    if insider.get("buys", 0) >= 3 and insider.get("net_shares", 0) > 0:
        bullish.append(f"Insider-Käufe ({insider['buys']})")
    elif insider.get("sells", 0) >= 5 and insider.get("net_shares", 0) < 0:
        bearish.append(f"Insider-Verkäufe ({insider['sells']})")

    # 4) News-Sentiment-Ratio (aus mehreren Quellen)
    if news_ratio is not None and (asset.get("news_sentiment") or {}).get("total_scored", 0) >= 3:
        if news_ratio >= 0.7:
            bullish.append(f"News bullish {int(news_ratio*100)}%")
        elif news_ratio <= 0.3:
            bearish.append(f"News bearish {int(news_ratio*100)}%")

    if len(bullish) >= 3:
        return "STRONG BUY", bullish
    if len(bearish) >= 3:
        return "STRONG SELL", bearish
    return None, []


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
# WATCHLIST (JSON-Persistenz, lokal)
# ============================================================================
import json
from pathlib import Path

WATCHLIST_PATH = Path(__file__).parent / "watchlist.json"


def load_watchlist() -> list[str]:
    if not WATCHLIST_PATH.exists():
        return []
    try:
        return json.loads(WATCHLIST_PATH.read_text())
    except Exception:
        return []


def save_watchlist(tickers: list[str]) -> None:
    """Speichert die Watchlist auf Disk. Dedup + Sort."""
    deduped = sorted(set(tickers))
    WATCHLIST_PATH.write_text(json.dumps(deduped, indent=2))


def toggle_watchlist(ticker: str) -> list[str]:
    """Toggles einen Ticker in der Watchlist und persistiert."""
    current = load_watchlist()
    if ticker in current:
        current.remove(ticker)
    else:
        current.append(ticker)
    save_watchlist(current)
    return current


# ============================================================================
# BACKTESTING: 90-Tage P&L-Simulation
# ============================================================================
def backtest_ticker(df: pd.DataFrame, ticker: str = "", initial_capital: float = 10000) -> dict:
    """Simuliert: jeden Tag Score berechnen, BUY (Score>=3) und SELL (Score<=-3).

    Vereinfacht: keine Position-Sizing-Logik, alles in/alles out.
    Liefert {trades, win_rate, total_return_pct, sharpe, max_drawdown, equity_curve}.
    """
    if df is None or len(df) < 60:
        return {"trades": [], "win_rate": 0, "total_return_pct": 0,
                "final_equity": initial_capital, "equity_curve": [],
                "buy_hold_pct": 0, "alpha_pct": 0}

    closes = df["Close"].squeeze()
    trades: list[dict] = []
    equity = initial_capital
    shares = 0
    entry_price = 0
    entry_date = None
    equity_curve = []

    # Brauche mind. 50 Tage Lookback für SMA50 + 30 für RSI, also start ab Index 50
    for i in range(50, len(df)):
        sub = df.iloc[: i + 1]
        try:
            ind = _compute_indicators(sub)
            score, _ = _score_from_indicators(ind, False)
        except Exception:
            equity_curve.append(equity if shares == 0 else shares * float(closes.iloc[i]))
            continue

        price = float(closes.iloc[i])
        date = df.index[i].strftime("%Y-%m-%d") if hasattr(df.index[i], "strftime") else str(df.index[i])

        # SELL-Signal mit offener Position
        if score <= -3 and shares > 0:
            sell_value = shares * price
            pnl_pct = (price - entry_price) / entry_price * 100
            trades.append({
                "entry_date": entry_date, "exit_date": date,
                "entry_price": round(entry_price, 4), "exit_price": round(price, 4),
                "pnl_pct": round(pnl_pct, 2),
                "pnl_usd": round(sell_value - (shares * entry_price), 2),
            })
            equity = sell_value
            shares = 0
            entry_price = 0

        # BUY-Signal ohne Position
        elif score >= 3 and shares == 0:
            shares = equity / price
            entry_price = price
            entry_date = date

        equity_curve.append(equity if shares == 0 else shares * price)

    # Letzte Position closen
    if shares > 0:
        final_price = float(closes.iloc[-1])
        pnl_pct = (final_price - entry_price) / entry_price * 100
        trades.append({
            "entry_date": entry_date, "exit_date": "open",
            "entry_price": round(entry_price, 4), "exit_price": round(final_price, 4),
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(shares * final_price - shares * entry_price, 2),
        })
        equity = shares * final_price

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    win_rate = (wins / len(trades) * 100) if trades else 0
    total_return_pct = (equity / initial_capital - 1) * 100
    buy_hold_pct = (float(closes.iloc[-1]) / float(closes.iloc[50]) - 1) * 100 if len(df) > 50 else 0
    alpha_pct = total_return_pct - buy_hold_pct

    return {
        "ticker": ticker,
        "trades": trades,
        "n_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "total_return_pct": round(total_return_pct, 2),
        "buy_hold_pct": round(buy_hold_pct, 2),
        "alpha_pct": round(alpha_pct, 2),
        "final_equity": round(equity, 2),
        "equity_curve": equity_curve,
    }


# ============================================================================
# ANOMALY-DETECTION (Z-Score auf Volume/News-Count)
# ============================================================================
def detect_anomalies(df: pd.DataFrame, ticker: str = "") -> dict:
    """Z-Score-Outlier-Detection auf Volume + Daily-Returns.

    Liefert {volume_zscore, return_zscore, is_volume_outlier, is_return_outlier}.
    """
    out = {"volume_zscore": None, "return_zscore": None,
           "is_volume_outlier": False, "is_return_outlier": False}
    if df is None or len(df) < 20:
        return out
    try:
        close = df["Close"].squeeze()
        vol = df["Volume"].squeeze() if "Volume" in df.columns else None

        # Daily-Return-Outlier
        returns = close.pct_change().dropna()
        if len(returns) >= 10:
            mean = float(returns.mean())
            std = float(returns.std())
            if std > 0:
                last_return = float(returns.iloc[-1])
                z = (last_return - mean) / std
                out["return_zscore"] = round(z, 2)
                if abs(z) >= 2.5:
                    out["is_return_outlier"] = True

        # Volume-Outlier
        if vol is not None and len(vol) >= 20 and float(vol.iloc[-1]) > 0:
            mean_v = float(vol.tail(20).mean())
            std_v = float(vol.tail(20).std())
            if std_v > 0:
                z = (float(vol.iloc[-1]) - mean_v) / std_v
                out["volume_zscore"] = round(z, 2)
                if z >= 2.5:
                    out["is_volume_outlier"] = True
    except Exception:
        pass
    return out


# ============================================================================
# KORRELATIONS-MATRIX
# ============================================================================
def compute_correlation_matrix(ohlc_dict: dict[str, pd.DataFrame],
                              days: int = 30) -> pd.DataFrame:
    """Pearson-Korrelation auf täglichen Returns für gegebene Tickers."""
    closes = {}
    for t, df in ohlc_dict.items():
        if df is None or len(df) < days:
            continue
        try:
            closes[t] = df["Close"].squeeze().tail(days).pct_change().dropna()
        except Exception:
            continue
    if not closes:
        return pd.DataFrame()
    return pd.DataFrame(closes).corr().round(2)


# ============================================================================
# SEKTOR-MONEY-FLOW (erweitert die bestehende Sektoren-Performance)
# ============================================================================
def fetch_sector_money_flow() -> list[dict]:
    """Berechnet Money-Flow-Score je Sektor aus 5d/30d-Performance + Volume.

    Money-Flow positiv = Geld fließt rein (steigende Preise + steigendes Volumen).
    """
    tickers = list(SECTOR_ETFS.keys())
    try:
        df = yf.download(tickers, period="40d", interval="1d",
                         progress=False, auto_adjust=True, group_by="ticker")
    except Exception:
        return []
    out = []
    for etf, name in SECTOR_ETFS.items():
        try:
            sub = df[etf] if len(tickers) > 1 else df
            close = sub["Close"].dropna()
            vol = sub["Volume"].dropna() if "Volume" in sub.columns else None
            if len(close) < 30:
                continue
            now = float(close.iloc[-1])
            chg_5d = (now / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else 0
            chg_30d = (now / float(close.iloc[-30]) - 1) * 100

            # Money-Flow-Score: 5d-Performance + Volume-Trend
            vol_trend = 0
            if vol is not None and len(vol) >= 10:
                recent_vol = float(vol.tail(5).mean())
                older_vol = float(vol.tail(20).head(15).mean()) if len(vol) >= 20 else 0
                if older_vol > 0:
                    vol_trend = (recent_vol / older_vol - 1) * 100

            mf_score = chg_5d + (vol_trend * 0.3)  # Volume hat 30%-Gewicht

            out.append({
                "etf": etf, "sector": name,
                "change_5d": round(chg_5d, 2),
                "change_30d": round(chg_30d, 2),
                "vol_trend_pct": round(vol_trend, 1),
                "money_flow_score": round(mf_score, 2),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["money_flow_score"], reverse=True)
    return out


# ============================================================================
# TELEGRAM-BOT (Phase 5)
# ============================================================================
SIGNALS_HISTORY_PATH = Path(__file__).parent / "last_signals.json"


def load_last_signals() -> dict:
    if not SIGNALS_HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(SIGNALS_HISTORY_PATH.read_text())
    except Exception:
        return {}


def save_signals(signals: dict) -> None:
    SIGNALS_HISTORY_PATH.write_text(json.dumps(signals, indent=2))


def send_telegram(message: str, parse_mode: str = "Markdown") -> bool:
    """Schickt eine Telegram-Nachricht via Bot-API. Liefert True bei Erfolg."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def telegram_get_chat_id() -> str | None:
    """Holt die letzte Chat-ID aus den getUpdates des Bots.

    Erst aufrufen NACHDEM der User dem Bot eine erste Message geschickt hat.
    """
    if not TELEGRAM_BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        updates = r.json().get("result", [])
        if not updates:
            return None
        chat_id = updates[-1].get("message", {}).get("chat", {}).get("id")
        return str(chat_id) if chat_id else None
    except Exception:
        return None


def format_alert_signal(asset: dict, pattern_label: str, reasons: list[str]) -> str:
    """Formatiert eine einzelne STRONG-BUY/SELL-Alert-Nachricht."""
    emoji = "🚀" if pattern_label == "STRONG BUY" else "⚠️"
    asset_type = "Aktie" if asset.get("type") == "stock" else "Krypto"
    price = asset.get("price", 0)
    price_str = f"${price:,.2f}" if price >= 1 else f"${price:.6f}"
    lines = [
        f"{emoji} *{pattern_label}* — `{asset['ticker']}` ({asset_type})",
        f"Preis: {price_str}  ·  Score: {asset.get('score', 0):+d}",
        "",
        "*Konvergierende Signale:*",
    ]
    for r in reasons:
        lines.append(f"  • {r}")
    return "\n".join(lines)


def build_morning_digest(stocks: list[dict], cryptos: list[dict],
                        fear_greed: dict | None = None) -> str:
    """Baut die tägliche Morning-Zusammenfassung für Telegram."""
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"*🌅 AI Invest Radar — {today}*", ""]

    if fear_greed:
        lines.append(f"_Krypto Fear&Greed:_ {fear_greed['value']}/100 — {fear_greed['classification']}")
        lines.append("")

    strong_buys = [a for a in stocks + cryptos if a.get("pattern_label") == "STRONG BUY"]
    strong_sells = [a for a in stocks + cryptos if a.get("pattern_label") == "STRONG SELL"]

    if strong_buys:
        lines.append("🚀 *STRONG BUY:*")
        for a in strong_buys[:5]:
            reasons = ", ".join(a.get("pattern_reasons", [])[:3])
            lines.append(f"  • `{a['ticker']}` — {reasons}")
        lines.append("")

    if strong_sells:
        lines.append("⚠️ *STRONG SELL:*")
        for a in strong_sells[:5]:
            reasons = ", ".join(a.get("pattern_reasons", [])[:3])
            lines.append(f"  • `{a['ticker']}` — {reasons}")
        lines.append("")

    # Regular top-buys als Bonus
    buys = [a for a in stocks + cryptos
            if a.get("label") == "BUY" and a.get("pattern_label") != "STRONG BUY"]
    if buys[:3]:
        lines.append("📈 *Weitere BUY-Signale:*")
        for a in buys[:3]:
            lines.append(f"  • `{a['ticker']}` — Score {a['score']:+d}  ·  {a['signals'][:60]}")

    if not strong_buys and not strong_sells and not buys:
        lines.append("_Heute keine starken Signale._")

    return "\n".join(lines)


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
