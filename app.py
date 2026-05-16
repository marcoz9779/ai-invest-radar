"""
AI Invest Radar – Streamlit Dashboard
Lokal: `streamlit run app.py` (öffnet http://localhost:8501)

Pro-Trader-Layout mit:
- Top-Empfehlungs-Karten (BUY/WATCH/HOLD) ganz oben
- Pro Ticker eine Karte mit Logo, Sparkline, klarem Label
- News + Reddit-Buzz lazy unter jeder Karte
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from ta.trend import SMAIndicator

from main import (
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
    analyze_all_stocks,
    analyze_crypto,
    fetch_news,
    fetch_reddit_buzz_bulk,
    recommendation_label,
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
def cached_all_stocks():
    """Bulk-Fetch + Score für alle US_STOCKS. OHLC-DataFrames als pickle."""
    rows, ohlc = analyze_all_stocks()
    # Streamlit-Cache hat Probleme mit dict[str, DataFrame]; wir wandeln zu list
    ohlc_pickle = {t: df.to_dict() for t, df in ohlc.items()}
    return rows, ohlc_pickle


@st.cache_data(ttl=300, show_spinner=False)
def cached_crypto():
    return analyze_crypto(40)


@st.cache_data(ttl=300, show_spinner=False)
def cached_news(ticker: str, name: str | None = None):
    return fetch_news(ticker, name)


@st.cache_data(ttl=300, show_spinner=False)
def cached_reddit_bulk(tickers_tuple: tuple, subs: str):
    """Parallele Reddit-Calls für alle Tickers auf einmal (gecached)."""
    return fetch_reddit_buzz_bulk(list(tickers_tuple), subs)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
LABEL_STYLES = {
    "BUY":    ("#16a34a", "white"),   # grün
    "WATCH":  ("#eab308", "black"),   # gelb
    "HOLD":   ("#6b7280", "white"),   # grau
    "REDUCE": ("#f97316", "white"),   # orange
    "SELL":   ("#dc2626", "white"),   # rot
}


def render_label_badge(label: str, big: bool = False) -> str:
    """HTML-Badge mit Farbe — wird via st.markdown(..., unsafe_allow_html=True) ausgegeben."""
    bg, fg = LABEL_STYLES.get(label, ("#6b7280", "white"))
    size = "1.1rem" if big else "0.85rem"
    pad = "0.4rem 0.9rem" if big else "0.15rem 0.55rem"
    return (
        f"<span style='background:{bg}; color:{fg}; "
        f"padding:{pad}; border-radius:8px; "
        f"font-weight:700; font-size:{size}; "
        f"letter-spacing:0.04em;'>{label}</span>"
    )


def render_sparkline(closes: pd.Series, label: str) -> go.Figure:
    """Mini-Linienchart 200x60. Farbe je nach Label-Empfehlung."""
    color = LABEL_STYLES.get(label, ("#6b7280", "white"))[0]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(range(len(closes))),
        y=closes.values,
        line=dict(color=color, width=2),
        fill="tozeroy" if False else None,
        showlegend=False,
        hoverinfo="skip",
    ))
    fig.update_layout(
        height=60,
        margin=dict(t=0, b=0, l=0, r=0),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def render_full_chart(df: pd.DataFrame, ticker: str):
    """Voller Candlestick + SMA20/50 im Expander."""
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
        height=350,
        xaxis_rangeslider_visible=False,
        margin=dict(t=10, b=10, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_dark",
    )
    st.plotly_chart(fig, width="stretch")


def sentiment_inline(score: float | None) -> str:
    if score is None:
        return ""
    if score > 0.15:
        return f" :green[(+{score:.2f})]"
    if score < -0.15:
        return f" :red[({score:.2f})]"
    return f" :gray[({score:+.2f})]"


def render_news_block(ticker: str, name: str | None = None):
    if not (MARKETAUX_API_KEY or FINNHUB_API_KEY or NEWSAPI_KEY):
        # Yahoo + Google RSS sind gratis und liefern trotzdem etwas
        pass
    headlines = cached_news(ticker, name)
    if not headlines:
        st.caption("Keine News.")
        return
    for h in headlines:
        sent = sentiment_inline(h.get("sentiment"))
        url = h.get("url") or "#"
        provider = h.get("provider", "")
        st.markdown(
            f"`{h['date']}` · *{h['source']}* "
            f":gray[[{provider}]] — [{h['headline'][:130]}]({url}){sent}"
        )


def render_reddit_block(buzz: dict):
    if buzz["mentions"] == 0:
        st.caption("Keine Mentions.")
        return
    velo_str = ""
    if buzz.get("velocity", 0) >= 2:
        velo_str = f" · :red[**Spike {buzz['velocity']}x**]"
    elif buzz.get("velocity", 0) >= 1.3:
        velo_str = f" · :orange[Trend {buzz['velocity']}x]"
    st.caption(
        f"{buzz['mentions']} Mentions · {buzz.get('mentions_24h', 0)} in 24h "
        f"· {buzz['upvotes']:,} Upvotes{velo_str}"
    )
    for p in buzz["posts"]:
        st.markdown(
            f"`{p['date']}` · [r/{p['subreddit']}]({p['url']}) · "
            f"+{p['score']} ups / {p['num_comments']}c — {p['title'][:110]}"
        )


def render_ticker_card(
    label: str, ticker: str, name: str, logo: str,
    price: float, signals: str, sparkline_data: pd.Series | None,
    extra_metric: str = "",
    buzz: dict | None = None,
    df_ohlc: pd.DataFrame | None = None,
):
    """Eine Pro-Trader-Karte pro Ticker: Logo + Name + Label-Badge + Sparkline."""
    with st.container(border=True):
        col_logo, col_info, col_chart, col_label = st.columns([1, 3, 2, 1.3])

        with col_logo:
            if logo:
                st.markdown(
                    f"<img src='{logo}' style='width:48px;height:48px;"
                    f"border-radius:8px;object-fit:contain;background:#f5f5f5;'>",
                    unsafe_allow_html=True,
                )

        with col_info:
            st.markdown(f"### {ticker}")
            st.caption(f"{name} · ${price:,.4f}".rstrip("0").rstrip(".") if price < 10 else f"{name} · ${price:,.2f}")
            st.markdown(f":gray[{signals}]" if signals else "")
            if extra_metric:
                st.caption(extra_metric)

        with col_chart:
            if sparkline_data is not None and len(sparkline_data) > 1:
                st.plotly_chart(
                    render_sparkline(sparkline_data, label),
                    width="stretch",
                    config={"displayModeBar": False},
                )

        with col_label:
            st.markdown(render_label_badge(label, big=True), unsafe_allow_html=True)
            if buzz and buzz.get("velocity", 0) >= 2:
                st.markdown(
                    f"<div style='color:#dc2626; font-size:0.8rem; "
                    f"margin-top:0.3rem; font-weight:600;'>"
                    f"Reddit-Spike {buzz['velocity']}x</div>",
                    unsafe_allow_html=True,
                )

        # Expander für Details
        with st.expander("Details: Chart, News, Reddit"):
            if df_ohlc is not None and not df_ohlc.empty:
                render_full_chart(df_ohlc, ticker)
            col_news, col_reddit = st.columns(2)
            with col_news:
                st.markdown("**News**")
                render_news_block(ticker, name if name != ticker else None)
            with col_reddit:
                st.markdown("**Reddit-Buzz**")
                if buzz is not None:
                    render_reddit_block(buzz)
                else:
                    st.caption("(wird beim Reddit-Bulk-Refresh geladen)")


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
    label_filter = st.multiselect(
        "Empfehlung",
        ["BUY", "WATCH", "HOLD", "REDUCE", "SELL"],
        default=["BUY", "WATCH", "HOLD", "REDUCE", "SELL"],
    )
    min_score = st.slider("Min. Score", -5, 5, -5)
    load_reddit = st.checkbox(
        "Reddit-Buzz mitladen", value=True,
        help="Holt parallel Reddit-Mentions für alle Ticker (~5-10s extra beim ersten Lauf).",
    )

    st.divider()
    st.subheader("Provider")
    news_provider = (
        ":green[Marketaux + Yahoo + Google]" if MARKETAUX_API_KEY
        else ":orange[Yahoo + Google + Finnhub]" if FINNHUB_API_KEY
        else ":blue[Yahoo + Google News]"
    )
    reddit_mode = (
        ":green[PRAW auth]" if (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)
        else ":blue[anonym/public]"
    )
    st.markdown(f"**News:** {news_provider}")
    st.markdown(f"**Reddit:** {reddit_mode}")
    st.caption(f"Lookback: {NEWS_LOOKBACK_DAYS} Tage")
    st.caption(f"Headlines pro Ticker: {MAX_HEADLINES_PER_TICKER}")


# ----------------------------------------------------------------------------
# Daten laden
# ----------------------------------------------------------------------------
with st.spinner("Lade Aktien-Daten (Bulk)..."):
    stock_rows, ohlc_pickle = cached_all_stocks()

with st.spinner("Lade Top-40 Kryptos..."):
    crypto_rows = cached_crypto()

# OHLC zurück zu DataFrames (für Charts)
ohlc_dfs: dict[str, pd.DataFrame] = {}
for t, dct in ohlc_pickle.items():
    try:
        ohlc_dfs[t] = pd.DataFrame(dct)
    except Exception:
        continue

# Reddit parallel laden (alle Tickers gleichzeitig)
stock_buzz: dict[str, dict] = {}
crypto_buzz: dict[str, dict] = {}
if load_reddit:
    with st.spinner("Lade Reddit-Buzz parallel..."):
        stock_buzz = cached_reddit_bulk(tuple(US_STOCKS), REDDIT_STOCK_SUBS)
        crypto_buzz = cached_reddit_bulk(
            tuple(c["ticker"] for c in crypto_rows), REDDIT_CRYPTO_SUBS
        )

# Empfehlungen berechnen
for s in stock_rows:
    buzz = stock_buzz.get(s["ticker"], {})
    s["label"] = recommendation_label(
        s["score"], buzz.get("mentions_24h", 0), buzz.get("velocity", 0)
    )
    s["buzz"] = buzz

for c in crypto_rows:
    buzz = crypto_buzz.get(c["ticker"], {})
    c["label"] = recommendation_label(
        c["score"], buzz.get("mentions_24h", 0), buzz.get("velocity", 0)
    )
    c["buzz"] = buzz

# Filter
stock_rows = [s for s in stock_rows
              if s["score"] >= min_score and s["label"] in label_filter]
crypto_rows = [c for c in crypto_rows
               if c["score"] >= min_score and c["label"] in label_filter]
stock_rows.sort(key=lambda x: x["score"], reverse=True)
crypto_rows.sort(key=lambda x: x["score"], reverse=True)

# Summary-Metrics
m1, m2, m3, m4 = st.columns(4)
total_buy = sum(1 for s in stock_rows if s["label"] == "BUY") + sum(
    1 for c in crypto_rows if c["label"] == "BUY")
total_watch = sum(1 for s in stock_rows if s["label"] == "WATCH") + sum(
    1 for c in crypto_rows if c["label"] == "WATCH")
total_assets = len(stock_rows) + len(crypto_rows)
hype_spikes = sum(
    1 for s in stock_rows + crypto_rows
    if s.get("buzz", {}).get("velocity", 0) >= 2
)
m1.metric("Gescannt", total_assets)
m2.metric("BUY-Signale", total_buy)
m3.metric("WATCH", total_watch)
m4.metric("Reddit-Spikes", hype_spikes)


# ----------------------------------------------------------------------------
# Top-5-Empfehlungen ganz oben
# ----------------------------------------------------------------------------
all_assets = (
    [{**s, "type": "stock"} for s in stock_rows]
    + [{**c, "type": "crypto"} for c in crypto_rows]
)
all_assets.sort(
    key=lambda x: (
        # BUY-Label vor anderem, dann Score, dann Velocity
        0 if x["label"] == "BUY" else 1 if x["label"] == "WATCH" else 2,
        -x["score"],
        -x.get("buzz", {}).get("velocity", 0),
    )
)
top5 = all_assets[:5]

if top5:
    st.markdown("### Top-Empfehlungen")
    cols = st.columns(5)
    for col, asset in zip(cols, top5):
        with col:
            with st.container(border=True):
                if asset.get("logo"):
                    st.markdown(
                        f"<img src='{asset['logo']}' style='width:36px;height:36px;"
                        f"border-radius:6px;object-fit:contain;background:#f5f5f5;'>",
                        unsafe_allow_html=True,
                    )
                st.markdown(f"#### {asset['ticker']}")
                st.markdown(render_label_badge(asset["label"], big=True), unsafe_allow_html=True)
                st.caption(asset.get("signals", ""))
                buzz = asset.get("buzz") or {}
                if buzz.get("velocity", 0) >= 2:
                    st.markdown(
                        f":red[Reddit-Spike **{buzz['velocity']}x**]"
                    )


# ----------------------------------------------------------------------------
# Tabs
# ----------------------------------------------------------------------------
tab_stocks, tab_crypto, tab_all = st.tabs(
    [f"US-Aktien ({len(stock_rows)})",
     f"Krypto ({len(crypto_rows)})",
     "Alle Empfehlungen"]
)

# ---- Tab: Aktien ----
with tab_stocks:
    if not stock_rows:
        st.info("Keine Aktien matchen die Filter.")
    for s in stock_rows:
        ohlc = ohlc_dfs.get(s["ticker"])
        sparkline = ohlc["Close"].squeeze() if ohlc is not None else None
        render_ticker_card(
            label=s["label"],
            ticker=s["ticker"],
            name="",  # Aktien-Namen kommen aus yfinance, optional
            logo=s.get("logo", ""),
            price=s["price"],
            signals=f"RSI {s['rsi']:.1f}  ·  {s['signals']}",
            sparkline_data=sparkline,
            extra_metric=f"Score {s['score']:+d}",
            buzz=s.get("buzz"),
            df_ohlc=ohlc,
        )

# ---- Tab: Krypto ----
with tab_crypto:
    if not crypto_rows:
        st.info("Keine Kryptos matchen die Filter.")
    for c in crypto_rows:
        render_ticker_card(
            label=c["label"],
            ticker=c["ticker"],
            name=c["name"],
            logo=c.get("logo", ""),
            price=c["price"],
            signals=(
                f"24h {c['change_24h']:+.1f}%  ·  "
                f"7d {c['change_7d']:+.1f}%  ·  "
                f"30d {c['change_30d']:+.1f}%  ·  {c['signals']}"
            ),
            sparkline_data=None,  # Kryptos haben kein OHLC im Bulk
            extra_metric=f"Score {c['score']:+d}",
            buzz=c.get("buzz"),
            df_ohlc=None,
        )

# ---- Tab: Alle Empfehlungen ----
with tab_all:
    st.subheader("Alle Assets sortiert nach Empfehlung")
    table = []
    for a in all_assets:
        buzz = a.get("buzz") or {}
        table.append({
            "Ticker": a["ticker"],
            "Typ": "Aktie" if a["type"] == "stock" else "Krypto",
            "Label": a["label"],
            "Score": a["score"],
            "Preis": f"${a['price']:,.2f}" if a["price"] >= 1 else f"${a['price']:.6f}",
            "Reddit-Mentions": buzz.get("mentions", 0),
            "Velocity": buzz.get("velocity", 0),
            "Signale": a["signals"],
        })
    if table:
        st.dataframe(
            pd.DataFrame(table),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("Keine Daten nach Filterung.")


st.divider()
st.caption(
    "Daten: yfinance, CoinGecko, Marketaux/Finnhub/NewsAPI, Yahoo Finance, "
    "Google News RSS, Reddit. Cache: 5 Min · "
    f"Lookback: {NEWS_LOOKBACK_DAYS}d"
)
