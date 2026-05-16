"""
AI Invest Radar – Streamlit Dashboard (Phase 1-3, "non plus ultra"-Build)
Lokal: `streamlit run app.py` (öffnet http://localhost:8501)

Features:
- 40 Aktien + 40 Kryptos mit Logos, Sparklines, BUY/WATCH/HOLD-Badges
- Treemap-Heatmap als Top-Übersicht
- Fear & Greed Index für Krypto im Header
- Sektoren-Performance (US-Sektor-ETFs)
- Earnings-Tracker mit Badge ("Earnings in 3d")
- TradingView-Widget im Expander pro Ticker
- News aus 4 Quellen + Reddit-Buzz + Mention-Velocity
- Claude-AI-Sentiment-Fusion (optional, wenn ANTHROPIC_API_KEY gesetzt)
"""

from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from ta.trend import SMAIndicator

from main import (
    ANTHROPIC_API_KEY,
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
    claude_sentiment_fusion,
    fetch_fear_greed_crypto,
    fetch_news,
    fetch_reddit_buzz_bulk,
    fetch_sector_performance,
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
    rows, ohlc = analyze_all_stocks()
    ohlc_pickle = {t: df.to_dict() for t, df in ohlc.items()}
    return rows, ohlc_pickle


@st.cache_data(ttl=300, show_spinner=False)
def cached_crypto():
    rows, ohlc = analyze_crypto(40)
    ohlc_pickle = {t: df.to_dict() for t, df in ohlc.items()}
    return rows, ohlc_pickle


@st.cache_data(ttl=300, show_spinner=False)
def cached_news(ticker: str, name: str | None = None):
    return fetch_news(ticker, name)


@st.cache_data(ttl=300, show_spinner=False)
def cached_reddit_bulk(tickers_tuple: tuple, subs: str):
    return fetch_reddit_buzz_bulk(list(tickers_tuple), subs)


@st.cache_data(ttl=600, show_spinner=False)
def cached_fear_greed():
    return fetch_fear_greed_crypto()


@st.cache_data(ttl=900, show_spinner=False)
def cached_sectors():
    return fetch_sector_performance()


@st.cache_data(ttl=1800, show_spinner=False)
def cached_claude(ticker: str, news_key: str, reddit_key: str,
                  headlines: tuple, posts: tuple):
    """Cache Claude-Resultate aggressiv (~$ kostet pro Call)."""
    return claude_sentiment_fusion(ticker, list(headlines), list(posts))


# ----------------------------------------------------------------------------
# Styling-Helpers
# ----------------------------------------------------------------------------
LABEL_STYLES = {
    "BUY":    ("#16a34a", "white"),   # green
    "WATCH":  ("#eab308", "black"),   # yellow
    "HOLD":   ("#6b7280", "white"),   # gray
    "REDUCE": ("#f97316", "white"),   # orange
    "SELL":   ("#dc2626", "white"),   # red
}


def render_label_badge(label: str, big: bool = False) -> str:
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
    color = LABEL_STYLES.get(label, ("#6b7280", "white"))[0]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(range(len(closes))),
        y=closes.values,
        line=dict(color=color, width=2),
        showlegend=False, hoverinfo="skip",
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


def tradingview_symbol(ticker: str, asset_type: str) -> str:
    """Bildet Ticker → TradingView-Symbol."""
    if asset_type == "stock":
        # BRK-B braucht spezielles Mapping
        if ticker == "BRK-B":
            return "NYSE:BRK.B"
        return ticker  # TradingView resolves automatisch
    # Krypto: nutze BINANCE-Spotpaar gegen USDT als Default
    return f"BINANCE:{ticker}USDT"


def render_tradingview(symbol: str, container_id: str, height: int = 400):
    """Embedded TradingView Advanced Chart Widget."""
    html = f"""
    <div class="tradingview-widget-container" style="height:{height}px">
      <div id="{container_id}" style="height:100%"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
        new TradingView.widget({{
          "autosize": true,
          "symbol": "{symbol}",
          "interval": "D",
          "timezone": "Etc/UTC",
          "theme": "dark",
          "style": "1",
          "locale": "en",
          "enable_publishing": false,
          "hide_side_toolbar": false,
          "save_image": false,
          "studies": ["RSI@tv-basicstudies","MACD@tv-basicstudies"],
          "container_id": "{container_id}"
        }});
      </script>
    </div>
    """
    components.html(html, height=height + 20, scrolling=False)


def sentiment_inline(score: float | None) -> str:
    if score is None:
        return ""
    if score > 0.15:
        return f" :green[(+{score:.2f})]"
    if score < -0.15:
        return f" :red[({score:.2f})]"
    return f" :gray[({score:+.2f})]"


def render_news_block(ticker: str, name: str | None = None):
    headlines = cached_news(ticker, name)
    if not headlines:
        st.caption("Keine News.")
        return headlines
    for h in headlines:
        sent = sentiment_inline(h.get("sentiment"))
        url = h.get("url") or "#"
        provider = h.get("provider", "")
        st.markdown(
            f"`{h['date']}` · *{h['source']}* "
            f":gray[[{provider}]] — [{h['headline'][:130]}]({url}){sent}"
        )
    return headlines


def render_reddit_block(buzz: dict) -> list:
    if buzz["mentions"] == 0:
        st.caption("Keine Mentions.")
        return []
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
    return buzz["posts"]


def render_ticker_card(
    *, label: str, ticker: str, name: str, logo: str,
    price: float, signals: str, sparkline_data: pd.Series | None,
    extra_metric: str = "",
    buzz: dict | None = None,
    tv_symbol: str = "",
    asset_type: str = "stock",
    earnings_date: str | None = None,
):
    """Karte pro Ticker mit Logo, Sparkline, Label, Earnings-Badge."""
    with st.container(border=True):
        col_logo, col_info, col_chart, col_label = st.columns([1, 3.2, 2, 1.5])

        with col_logo:
            if logo:
                st.markdown(
                    f"<img src='{logo}' style='width:48px;height:48px;"
                    f"border-radius:8px;object-fit:contain;background:#f5f5f5;'>",
                    unsafe_allow_html=True,
                )

        with col_info:
            st.markdown(f"### {ticker}")
            price_str = f"${price:,.4f}".rstrip("0").rstrip(".") if price < 10 else f"${price:,.2f}"
            sub = f"{name} · {price_str}" if name else price_str
            st.caption(sub)
            st.markdown(f":gray[{signals}]" if signals else "")
            badges = []
            if earnings_date:
                today = datetime.now().date()
                try:
                    ed = datetime.fromisoformat(earnings_date).date()
                    days = (ed - today).days
                    badges.append(f":violet[📅 Earnings in {days}d]")
                except Exception:
                    pass
            if buzz and buzz.get("velocity", 0) >= 2:
                badges.append(f":red[🔥 Reddit-Spike {buzz['velocity']}x]")
            if badges:
                st.markdown(" · ".join(badges))

        with col_chart:
            if sparkline_data is not None and len(sparkline_data) > 1:
                st.plotly_chart(
                    render_sparkline(sparkline_data, label),
                    width="stretch",
                    config={"displayModeBar": False},
                )

        with col_label:
            st.markdown(render_label_badge(label, big=True), unsafe_allow_html=True)
            if extra_metric:
                st.caption(extra_metric)

        with st.expander("Details: TradingView-Chart · News · Reddit · AI-Sentiment"):
            # TradingView Chart
            if tv_symbol:
                container_id = f"tv_{asset_type}_{ticker.replace('-', '_').replace('.', '_')}"
                render_tradingview(tv_symbol, container_id, height=420)
            else:
                st.caption("(Kein TradingView-Symbol verfügbar)")

            col_news, col_reddit = st.columns(2)
            with col_news:
                st.markdown("**News (aus 4 Quellen, dedupliziert)**")
                headlines = render_news_block(ticker, name if name and name != ticker else None)
            with col_reddit:
                st.markdown("**Reddit-Buzz**")
                posts = render_reddit_block(buzz) if buzz else []

            # Claude AI-Sentiment-Fusion (wenn Key)
            if ANTHROPIC_API_KEY and (headlines or posts):
                with st.spinner("Claude analysiert News + Reddit..."):
                    headlines_t = tuple(tuple(sorted(h.items())) for h in headlines[:8])
                    posts_t = tuple(tuple(sorted(p.items())) for p in posts[:5])
                    ai = cached_claude(ticker, str(len(headlines)), str(len(posts)),
                                       tuple(headlines[:8]), tuple(posts[:5]))
                if ai:
                    score = ai.get("score", 0)
                    color = "green" if score > 0.15 else "red" if score < -0.15 else "gray"
                    st.markdown(
                        f"**Claude AI-Sentiment:** :{color}[{ai.get('label', '?').upper()}] "
                        f"({score:+.2f})"
                    )
                    st.caption(ai.get("summary", ""))


# ============================================================================
# HEADER
# ============================================================================
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


# ============================================================================
# MARKET-CONTEXT-BAR: Fear & Greed + Top-Sektor
# ============================================================================
fg = cached_fear_greed()
sectors = cached_sectors()
sector_top = sectors[0] if sectors else None
sector_bottom = sectors[-1] if sectors else None

ctx1, ctx2, ctx3, ctx4 = st.columns(4)
with ctx1:
    if fg:
        val = fg["value"]
        cls = fg["classification"]
        cls_color = (
            "#dc2626" if val < 25 else
            "#f97316" if val < 45 else
            "#eab308" if val < 55 else
            "#84cc16" if val < 75 else
            "#16a34a"
        )
        st.markdown(
            f"<div style='border:1px solid #333; padding:0.75rem; border-radius:8px;'>"
            f"<div style='color:#888; font-size:0.8rem;'>FEAR &amp; GREED (Krypto)</div>"
            f"<div style='font-size:2rem; font-weight:700; color:{cls_color};'>{val}/100</div>"
            f"<div style='color:{cls_color}; font-size:0.9rem;'>{cls}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.caption("Fear & Greed: nicht verfügbar")

with ctx2:
    if sector_top:
        st.markdown(
            f"<div style='border:1px solid #333; padding:0.75rem; border-radius:8px;'>"
            f"<div style='color:#888; font-size:0.8rem;'>STÄRKSTER SEKTOR (5d)</div>"
            f"<div style='font-size:1.4rem; font-weight:700;'>{sector_top['sector']}</div>"
            f"<div style='color:#16a34a; font-size:1rem;'>+{sector_top['change_5d']:.2f}% · {sector_top['etf']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

with ctx3:
    if sector_bottom:
        color = "#dc2626" if sector_bottom["change_5d"] < 0 else "#16a34a"
        sign = "" if sector_bottom["change_5d"] < 0 else "+"
        st.markdown(
            f"<div style='border:1px solid #333; padding:0.75rem; border-radius:8px;'>"
            f"<div style='color:#888; font-size:0.8rem;'>SCHWÄCHSTER SEKTOR (5d)</div>"
            f"<div style='font-size:1.4rem; font-weight:700;'>{sector_bottom['sector']}</div>"
            f"<div style='color:{color}; font-size:1rem;'>{sign}{sector_bottom['change_5d']:.2f}% · {sector_bottom['etf']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

with ctx4:
    news_provider = (
        "Marketaux+Yahoo+Google+Finnhub" if MARKETAUX_API_KEY
        else "Yahoo+Google+Finnhub" if FINNHUB_API_KEY
        else "Yahoo+Google" if NEWSAPI_KEY
        else "Yahoo+Google News"
    )
    ai_status = "ON" if ANTHROPIC_API_KEY else "off"
    st.markdown(
        f"<div style='border:1px solid #333; padding:0.75rem; border-radius:8px; font-size:0.85rem;'>"
        f"<div style='color:#888; font-size:0.8rem;'>SYSTEM</div>"
        f"<div>📰 {news_provider}</div>"
        f"<div>🤖 Claude AI: {ai_status}</div>"
        f"<div>⏰ Cache 5min</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ============================================================================
# Sidebar
# ============================================================================
with st.sidebar:
    st.header("Filter")
    label_filter = st.multiselect(
        "Empfehlung",
        ["BUY", "WATCH", "HOLD", "REDUCE", "SELL"],
        default=["BUY", "WATCH", "HOLD", "REDUCE", "SELL"],
    )
    min_score = st.slider("Min. Score", -5, 8, -5)
    load_reddit = st.checkbox("Reddit-Buzz mitladen", value=True)

    st.divider()
    st.subheader("Sektoren-Performance (5d)")
    if sectors:
        for s in sectors:
            color = "green" if s["change_5d"] > 0 else "red"
            st.markdown(
                f"`{s['etf']}` {s['sector']}  "
                f":{color}[{s['change_5d']:+.2f}%]"
            )

    st.divider()
    st.subheader("Status")
    reddit_mode = (
        ":green[PRAW auth]" if (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)
        else ":blue[anonym/public]"
    )
    st.markdown(f"**Reddit:** {reddit_mode}")
    st.caption(f"Lookback: {NEWS_LOOKBACK_DAYS}d · {MAX_HEADLINES_PER_TICKER} Headlines/Ticker")


# ============================================================================
# DATEN LADEN
# ============================================================================
with st.spinner("Lade Aktien-Daten (Bulk + Earnings)..."):
    stock_rows, stock_ohlc_p = cached_all_stocks()
with st.spinner("Lade Top-40 Kryptos (OHLC + Indikatoren)..."):
    crypto_rows, crypto_ohlc_p = cached_crypto()

# OHLC zurück zu DataFrames
stock_ohlc: dict[str, pd.DataFrame] = {}
for t, dct in stock_ohlc_p.items():
    try:
        stock_ohlc[t] = pd.DataFrame(dct)
    except Exception:
        pass
crypto_ohlc: dict[str, pd.DataFrame] = {}
for t, dct in crypto_ohlc_p.items():
    try:
        crypto_ohlc[t] = pd.DataFrame(dct)
    except Exception:
        pass

# Reddit parallel
stock_buzz: dict[str, dict] = {}
crypto_buzz: dict[str, dict] = {}
if load_reddit:
    with st.spinner("Lade Reddit-Buzz parallel..."):
        stock_buzz = cached_reddit_bulk(tuple(US_STOCKS), REDDIT_STOCK_SUBS)
        crypto_buzz = cached_reddit_bulk(
            tuple(c["ticker"] for c in crypto_rows), REDDIT_CRYPTO_SUBS
        )

# Labels berechnen
for s in stock_rows:
    buzz = stock_buzz.get(s["ticker"], {})
    s["label"] = recommendation_label(
        s["score"],
        buzz.get("mentions_24h", 0),
        buzz.get("velocity", 0),
        has_earnings_soon=bool(s.get("earnings_date")),
    )
    s["buzz"] = buzz

for c in crypto_rows:
    buzz = crypto_buzz.get(c["ticker"], {})
    c["label"] = recommendation_label(
        c["score"],
        buzz.get("mentions_24h", 0),
        buzz.get("velocity", 0),
    )
    c["buzz"] = buzz

# Filter
stock_rows_f = [s for s in stock_rows
                if s["score"] >= min_score and s["label"] in label_filter]
crypto_rows_f = [c for c in crypto_rows
                 if c["score"] >= min_score and c["label"] in label_filter]
stock_rows_f.sort(key=lambda x: x["score"], reverse=True)
crypto_rows_f.sort(key=lambda x: x["score"], reverse=True)


# ============================================================================
# TREEMAP-HEATMAP (Top-Übersicht)
# ============================================================================
st.markdown("### Heatmap · alle Assets (Score-Stärke = Fläche · Empfehlung = Farbe)")
treemap_data = []
for s in stock_rows_f:
    treemap_data.append({
        "label": s["ticker"], "parent": "Aktien",
        "value": max(abs(s["score"]) + 1, 1),
        "rec": s["label"], "score": s["score"],
        "signals": s["signals"],
    })
for c in crypto_rows_f:
    treemap_data.append({
        "label": c["ticker"], "parent": "Krypto",
        "value": max(abs(c["score"]) + 1, 1),
        "rec": c["label"], "score": c["score"],
        "signals": c["signals"],
    })

if treemap_data:
    tdf = pd.DataFrame(treemap_data)
    color_map = {k: v[0] for k, v in LABEL_STYLES.items()}
    fig_tree = px.treemap(
        tdf, path=["parent", "label"], values="value",
        color="rec", color_discrete_map=color_map,
        custom_data=["score", "signals", "rec"],
    )
    fig_tree.update_traces(
        hovertemplate="<b>%{label}</b><br>Score: %{customdata[0]:+d}<br>"
                      "Empfehlung: %{customdata[2]}<br>Signale: %{customdata[1]}<extra></extra>",
        textposition="middle center",
        textfont=dict(size=14, color="white"),
    )
    fig_tree.update_layout(height=380, margin=dict(t=20, b=10, l=10, r=10),
                           template="plotly_dark")
    st.plotly_chart(fig_tree, width="stretch", config={"displayModeBar": False})


# ============================================================================
# TOP-5-EMPFEHLUNGEN
# ============================================================================
all_assets = (
    [{**s, "type": "stock"} for s in stock_rows_f]
    + [{**c, "type": "crypto"} for c in crypto_rows_f]
)
all_assets.sort(
    key=lambda x: (
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
                st.caption(asset.get("signals", "")[:80])
                buzz = asset.get("buzz") or {}
                badges_top = []
                if asset.get("earnings_date"):
                    try:
                        ed = datetime.fromisoformat(asset["earnings_date"]).date()
                        days = (ed - datetime.now().date()).days
                        badges_top.append(f":violet[📅 {days}d]")
                    except Exception:
                        pass
                if buzz.get("velocity", 0) >= 2:
                    badges_top.append(f":red[🔥 {buzz['velocity']}x]")
                if badges_top:
                    st.markdown(" · ".join(badges_top))


# Metrics
m1, m2, m3, m4 = st.columns(4)
total_buy = sum(1 for x in all_assets if x["label"] == "BUY")
total_watch = sum(1 for x in all_assets if x["label"] == "WATCH")
hype_spikes = sum(1 for x in all_assets if x.get("buzz", {}).get("velocity", 0) >= 2)
earnings_soon = sum(1 for x in stock_rows_f if x.get("earnings_date"))
m1.metric("BUY-Signale", total_buy)
m2.metric("WATCH", total_watch)
m3.metric("Reddit-Spikes", hype_spikes)
m4.metric("Earnings <14d", earnings_soon)


# ============================================================================
# TABS
# ============================================================================
tab_stocks, tab_crypto, tab_all = st.tabs(
    [f"US-Aktien ({len(stock_rows_f)})",
     f"Krypto ({len(crypto_rows_f)})",
     "Alle Empfehlungen"]
)

with tab_stocks:
    if not stock_rows_f:
        st.info("Keine Aktien matchen die Filter.")
    for s in stock_rows_f:
        ohlc = stock_ohlc.get(s["ticker"])
        sparkline = ohlc["Close"].squeeze() if ohlc is not None else None
        render_ticker_card(
            label=s["label"],
            ticker=s["ticker"],
            name="",
            logo=s.get("logo", ""),
            price=s["price"],
            signals=f"RSI {s['rsi']:.1f}  ·  {s['signals']}",
            sparkline_data=sparkline,
            extra_metric=f"Score {s['score']:+d}",
            buzz=s.get("buzz"),
            tv_symbol=tradingview_symbol(s["ticker"], "stock"),
            asset_type="stock",
            earnings_date=s.get("earnings_date"),
        )

with tab_crypto:
    if not crypto_rows_f:
        st.info("Keine Kryptos matchen die Filter.")
    for c in crypto_rows_f:
        ohlc = crypto_ohlc.get(c["ticker"])
        sparkline = ohlc["Close"].squeeze() if ohlc is not None else None
        rsi_str = f"RSI {c['rsi']:.1f}  ·  " if c.get("rsi") else ""
        render_ticker_card(
            label=c["label"],
            ticker=c["ticker"],
            name=c["name"],
            logo=c.get("logo", ""),
            price=c["price"],
            signals=(
                f"{rsi_str}24h {c['change_24h']:+.1f}%  ·  "
                f"7d {c['change_7d']:+.1f}%  ·  30d {c['change_30d']:+.1f}%"
            ),
            sparkline_data=sparkline,
            extra_metric=f"Score {c['score']:+d}",
            buzz=c.get("buzz"),
            tv_symbol=tradingview_symbol(c["ticker"], "crypto"),
            asset_type="crypto",
        )

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
            "Mentions": buzz.get("mentions", 0),
            "Velocity": buzz.get("velocity", 0),
            "Earnings": a.get("earnings_date") or "",
            "Signale": a["signals"],
        })
    if table:
        st.dataframe(pd.DataFrame(table), width="stretch", hide_index=True)
    else:
        st.info("Keine Daten nach Filterung.")


st.divider()
st.caption(
    "Daten: yfinance, CoinGecko, Marketaux/Finnhub/NewsAPI, Yahoo Finance, "
    "Google News RSS, Reddit, alternative.me Fear&Greed. "
    f"Cache: 5-30 Min · Lookback: {NEWS_LOOKBACK_DAYS}d"
)
