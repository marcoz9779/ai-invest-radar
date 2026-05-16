"""
AI Invest Radar – Streamlit Dashboard
Lokal: `streamlit run app.py` (öffnet http://localhost:8501)

Lazy-Loading: News + Reddit werden erst geholt, wenn der Ticker-Expander
geöffnet wird. Caching via st.cache_data (TTL 5 Minuten), damit Filter-
Anpassungen nicht jedes Mal alle APIs neu treffen.
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from ta.trend import SMAIndicator

from main import (
    CRYPTOS,
    FINNHUB_API_KEY,
    MARKETAUX_API_KEY,
    MAX_HEADLINES_PER_TICKER,
    NEWSAPI_KEY,
    NEWS_LOOKBACK_DAYS,
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_CRYPTO_SUBS,
    REDDIT_STOCK_SUBS,
    US_STOCKS,
    analyze_crypto,
    analyze_stock_df,
    fetch_news,
    fetch_reddit_buzz,
    fetch_stock_data,
)

# ----------------------------------------------------------------------------
# Page-Config
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="AI Invest Radar",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ----------------------------------------------------------------------------
# Cached Fetchers
# ----------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def cached_stock(ticker: str):
    df = fetch_stock_data(ticker)
    if df is None:
        return None, None
    return df, analyze_stock_df(df, ticker)


@st.cache_data(ttl=300, show_spinner=False)
def cached_crypto():
    return analyze_crypto()


@st.cache_data(ttl=300, show_spinner=False)
def cached_news(ticker: str):
    return fetch_news(ticker)


@st.cache_data(ttl=300, show_spinner=False)
def cached_reddit(ticker: str, subs: str):
    return fetch_reddit_buzz(ticker, subs)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def score_badge(score: int) -> str:
    """Streamlit-Color-Markdown für Score."""
    if score >= 2:
        return f":green[**{score:+d}**]"
    if score >= 1:
        return f":green[{score:+d}]"
    if score == 0:
        return f":gray[{score:+d}]"
    if score >= -1:
        return f":orange[{score:+d}]"
    return f":red[**{score:+d}**]"


def sentiment_badge(score: float | None) -> str:
    if score is None:
        return ""
    if score > 0.15:
        return f" :green[(+{score:.2f})]"
    if score < -0.15:
        return f" :red[({score:.2f})]"
    return f" :gray[({score:+.2f})]"


def render_price_chart(df: pd.DataFrame, ticker: str):
    """Candlestick + SMA20 + SMA50."""
    close = df["Close"].squeeze()
    sma_20 = SMAIndicator(close, window=20).sma_indicator()
    sma_50 = SMAIndicator(close, window=50).sma_indicator()

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["Open"].squeeze(),
        high=df["High"].squeeze(),
        low=df["Low"].squeeze(),
        close=close,
        name=ticker,
        showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=sma_20, name="SMA20",
        line=dict(color="#ff9800", width=1.5),
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=sma_50, name="SMA50",
        line=dict(color="#9c27b0", width=1.5),
    ))
    fig.update_layout(
        height=300,
        xaxis_rangeslider_visible=False,
        margin=dict(t=10, b=10, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_dark",
    )
    st.plotly_chart(fig, width="stretch")


def render_news_block(ticker: str, show_news: bool):
    if not show_news:
        return
    if not (MARKETAUX_API_KEY or FINNHUB_API_KEY or NEWSAPI_KEY):
        st.caption("Keine News-API-Keys konfiguriert (siehe .env)")
        return
    st.markdown("**News**")
    headlines = cached_news(ticker)
    if not headlines:
        st.caption("Keine News")
        return
    for h in headlines:
        sent = sentiment_badge(h.get("sentiment"))
        url = h.get("url") or "#"
        st.markdown(
            f"`{h['date']}` · *{h['source']}* — [{h['headline'][:110]}]({url}){sent}",
            unsafe_allow_html=False,
        )


def render_reddit_block(ticker: str, subs: str, show_reddit: bool):
    if not show_reddit:
        return
    st.markdown("**Reddit-Buzz**")
    buzz = cached_reddit(ticker, subs)
    if buzz["mentions"] == 0:
        st.caption("Keine Mentions")
        return
    st.caption(f"{buzz['mentions']} Mentions · {buzz['upvotes']:,} Upvotes")
    for p in buzz["posts"]:
        st.markdown(
            f"`{p['date']}` · [r/{p['subreddit']}]({p['url']}) · "
            f"+{p['score']} ups / {p['num_comments']}c — {p['title'][:100]}"
        )


# ----------------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------------
col_title, col_meta, col_refresh = st.columns([5, 3, 1])
col_title.title("AI Invest Radar")
col_meta.markdown(
    f"<div style='text-align:right; padding-top:1.5rem; color:#888;'>"
    f"Letztes Update · {datetime.now():%Y-%m-%d %H:%M}</div>",
    unsafe_allow_html=True,
)
if col_refresh.button("Refresh", width="stretch", type="primary"):
    st.cache_data.clear()
    st.rerun()

st.caption(
    "Research- und Alert-Tool für US-Aktien und Krypto. "
    "Kein Anlageberater — Trade-Entscheidungen triffst du selbst."
)

# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
with st.sidebar:
    st.header("Filter")
    min_score = st.slider("Min. Score", -5, 5, -5,
                          help="Nur Ticker mit Score >= diesem Wert anzeigen")
    show_news = st.checkbox("News anzeigen", value=True)
    show_reddit = st.checkbox("Reddit-Buzz anzeigen", value=True)
    show_chart = st.checkbox("Charts anzeigen", value=True)

    st.divider()
    st.subheader("Provider-Status")
    news_provider = (
        ":green[Marketaux]" if MARKETAUX_API_KEY
        else ":orange[Finnhub]" if FINNHUB_API_KEY
        else ":orange[NewsAPI]" if NEWSAPI_KEY
        else ":red[keine]"
    )
    reddit_mode = (
        ":green[PRAW auth]" if (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)
        else ":blue[anonym/public]"
    )
    st.markdown(f"**News:** {news_provider}")
    st.markdown(f"**Reddit:** {reddit_mode}")
    st.caption(f"Lookback: {NEWS_LOOKBACK_DAYS} Tage")
    st.caption(f"Headlines/Posts pro Ticker: {MAX_HEADLINES_PER_TICKER}")

# ----------------------------------------------------------------------------
# Daten laden (mit Spinner für UX)
# ----------------------------------------------------------------------------
with st.spinner("Lade Aktien-Daten..."):
    stock_rows = []
    for t in US_STOCKS:
        df, a = cached_stock(t)
        if a:
            stock_rows.append((t, df, a))

with st.spinner("Lade Krypto-Daten..."):
    crypto_rows = cached_crypto()

# Filter
stock_rows = [r for r in stock_rows if r[2]["score"] >= min_score]
crypto_rows = [c for c in crypto_rows if c["score"] >= min_score]
stock_rows.sort(key=lambda r: r[2]["score"], reverse=True)
crypto_rows.sort(key=lambda c: c["score"], reverse=True)

# Top-Kandidaten (kombiniert)
top_combined = (
    [{"asset": r[2]["ticker"], "score": r[2]["score"],
      "signals": r[2]["signals"], "type": "Aktie",
      "price": r[2]["price"]} for r in stock_rows]
    + [{"asset": c["ticker"], "score": c["score"],
        "signals": c["signals"], "type": "Krypto",
        "price": c["price"]} for c in crypto_rows]
)
top_combined.sort(key=lambda x: x["score"], reverse=True)

# Summary-Metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Aktien gescannt", len(stock_rows))
m2.metric("Long-Kandidaten", sum(1 for r in stock_rows if r[2]["score"] > 0))
m3.metric("Short-Signale", sum(1 for r in stock_rows if r[2]["score"] < 0))
m4.metric("Krypto-Coins", len(crypto_rows))

# ----------------------------------------------------------------------------
# Tabs
# ----------------------------------------------------------------------------
tab_stocks, tab_crypto, tab_top = st.tabs(
    ["US-Aktien", "Krypto", "Top-Kandidaten"]
)

# ---- Tab: Aktien ----
with tab_stocks:
    if not stock_rows:
        st.info("Keine Aktien matchen den aktuellen Score-Filter.")
    for ticker, df, a in stock_rows:
        header = (
            f"**{ticker}**  ·  ${a['price']:.2f}  ·  "
            f"RSI {a['rsi']:.1f}  ·  Score {score_badge(a['score'])}  ·  "
            f"_{a['signals']}_"
        )
        with st.expander(header):
            if show_chart:
                render_price_chart(df, ticker)
            col_news, col_reddit = st.columns(2)
            with col_news:
                render_news_block(ticker, show_news)
            with col_reddit:
                render_reddit_block(ticker, REDDIT_STOCK_SUBS, show_reddit)

# ---- Tab: Krypto ----
with tab_crypto:
    if not crypto_rows:
        st.info("Keine Kryptos matchen den aktuellen Score-Filter.")
    for c in crypto_rows:
        header = (
            f"**{c['ticker']}**  ·  ${c['price']:.4f}  ·  "
            f"7d {c['change_7d']:+.1f}%  ·  30d {c['change_30d']:+.1f}%  ·  "
            f"Score {score_badge(c['score'])}  ·  _{c['signals']}_"
        )
        with st.expander(header):
            render_reddit_block(c["ticker"], REDDIT_CRYPTO_SUBS, show_reddit)

# ---- Tab: Top-Kandidaten ----
with tab_top:
    st.subheader("Top Long-Kandidaten (Score-sortiert)")
    df_top = pd.DataFrame(top_combined[:10])
    if df_top.empty:
        st.info("Keine Kandidaten — Filter zu strikt?")
    else:
        st.dataframe(
            df_top[["asset", "type", "score", "price", "signals"]],
            width="stretch",
            hide_index=True,
            column_config={
                "asset": "Ticker",
                "type": "Typ",
                "score": st.column_config.NumberColumn("Score", format="%+d"),
                "price": st.column_config.NumberColumn("Preis", format="$%.4f"),
                "signals": "Signale",
            },
        )

st.divider()
st.caption(
    "Daten via yfinance, CoinGecko, Marketaux / Finnhub / NewsAPI, Reddit. "
    f"Cache: 5 Min · Lookback News/Reddit: {NEWS_LOOKBACK_DAYS}d"
)
