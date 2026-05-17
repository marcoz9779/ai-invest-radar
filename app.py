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

from storage import (
    backtest_from_snapshots,
    get_consecutive_score_changes,
    get_history,
    get_recent_strong_signals,
    get_stats as get_storage_stats,
    save_snapshot_batch,
)
from swissquote import (
    apply_whitelist,
    compute_watchlist_pnl,
    load_whitelist,
    parse_swissquote_csv,
    record_watchlist_entry,
    remove_watchlist_entry,
    save_whitelist,
)
from main import (
    ANTHROPIC_API_KEY,
    CRYPTOPANIC_API_KEY,
    FINNHUB_API_KEY,
    MARKETAUX_API_KEY,
    MAX_HEADLINES_PER_TICKER,
    NEWSAPI_KEY,
    NEWS_LOOKBACK_DAYS,
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_CRYPTO_SUBS,
    REDDIT_STOCK_SUBS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    US_STOCKS,
    analyze_all_stocks,
    analyze_crypto,
    backtest_ticker,
    build_morning_digest,
    claude_sentiment_fusion,
    compute_correlation_matrix,
    fetch_coingecko_trending,
    fetch_fear_greed_crypto,
    fetch_options_flow,
    fetch_wikipedia_pageviews_bulk,
    fetch_news,
    fetch_reddit_buzz_bulk,
    fetch_rss_topstories,
    fetch_sector_money_flow,
    fetch_sector_performance,
    load_watchlist,
    multi_signal_pattern,
    news_sentiment_ratio,
    news_velocity,
    recommendation_label,
    send_telegram,
    toggle_watchlist,
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
def cached_news(ticker: str, name: str | None = None, asset_type: str = "stock"):
    rss_pool = cached_rss_topstories()
    return fetch_news(ticker, name, asset_type, rss_pool)


@st.cache_data(ttl=600, show_spinner=False)
def cached_rss_topstories():
    return fetch_rss_topstories()


@st.cache_data(ttl=300, show_spinner=False)
def cached_reddit_bulk(tickers_tuple: tuple, subs: str):
    return fetch_reddit_buzz_bulk(list(tickers_tuple), subs)


@st.cache_data(ttl=600, show_spinner=False)
def cached_fear_greed():
    return fetch_fear_greed_crypto()


@st.cache_data(ttl=900, show_spinner=False)
def cached_sectors():
    return fetch_sector_performance()


@st.cache_data(ttl=900, show_spinner=False)
def cached_sector_money_flow():
    return fetch_sector_money_flow()


@st.cache_data(ttl=3600, show_spinner=False)
def cached_wiki_pageviews(tickers_tuple: tuple):
    return fetch_wikipedia_pageviews_bulk(list(tickers_tuple))


@st.cache_data(ttl=900, show_spinner=False)
def cached_trending_crypto():
    return fetch_coingecko_trending()


@st.cache_data(ttl=900, show_spinner=False)
def cached_options_flow(ticker: str):
    return fetch_options_flow(ticker)


@st.cache_data(ttl=600, show_spinner=False)
def cached_correlation_matrix(tickers_tuple: tuple, ohlc_pickle: dict):
    """Korrelation auf täglichen Returns, 30 Tage."""
    ohlc = {t: pd.DataFrame(d) for t, d in ohlc_pickle.items() if t in tickers_tuple}
    return compute_correlation_matrix(ohlc, days=30)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_claude(ticker: str, news_key: str, reddit_key: str,
                  headlines: tuple, posts: tuple):
    """Cache Claude-Resultate aggressiv (~$ kostet pro Call)."""
    return claude_sentiment_fusion(ticker, list(headlines), list(posts))


# Watchlist init (lädt einmal pro Session aus JSON)
if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist()


def fmt_marketcap(mc: float | None) -> str:
    if not mc:
        return "—"
    if mc >= 1e12:
        return f"${mc / 1e12:.2f}T"
    if mc >= 1e9:
        return f"${mc / 1e9:.1f}B"
    if mc >= 1e6:
        return f"${mc / 1e6:.0f}M"
    return f"${mc:,.0f}"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%" if abs(v) < 5 else f"{v:.1f}%"


# ----------------------------------------------------------------------------
# Styling-Helpers
# ----------------------------------------------------------------------------
LABEL_STYLES = {
    "STRONG BUY":  ("#15803d", "white"),  # dark-green, BIG
    "BUY":         ("#16a34a", "white"),  # green
    "WATCH":       ("#eab308", "black"),  # yellow
    "HOLD":        ("#6b7280", "white"),  # gray
    "REDUCE":      ("#f97316", "white"),  # orange
    "SELL":        ("#dc2626", "white"),  # red
    "STRONG SELL": ("#991b1b", "white"),  # dark-red, BIG
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


def render_news_block(ticker: str, name: str | None = None, asset_type: str = "stock"):
    headlines = cached_news(ticker, name, asset_type)
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
    fundamentals: dict | None = None,
    insider: dict | None = None,
    key_suffix: str = "main",
    news_sentiment: dict | None = None,
    pattern_reasons: list[str] | None = None,
    earnings_surprise: dict | None = None,
    anomaly: dict | None = None,
    wiki: dict | None = None,
):
    """Karte pro Ticker mit Logo, Sparkline, Label, Earnings/Insider/News-Velocity-Badges."""
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
            if insider and insider.get("buys", 0) >= 2 and insider.get("net_shares", 0) > 0:
                badges.append(f":green[💼 Insider-Käufe ({insider['buys']})]")
            elif insider and insider.get("sells", 0) >= 3 and insider.get("net_shares", 0) < 0:
                badges.append(f":red[💼 Insider-Verkäufe ({insider['sells']})]")
            # Score-Trend-Badge aus History (Phase 6)
            try:
                trend = get_consecutive_score_changes(ticker, days=7)
                if trend >= 3:
                    badges.append(f":green[📈 Score steigt {trend} Tage in Folge]")
                elif trend <= -3:
                    badges.append(f":red[📉 Score fällt {-trend} Tage in Folge]")
            except Exception:
                pass
            # Earnings-Surprise-Badge
            if earnings_surprise and earnings_surprise.get("beat_rate") is not None:
                br = earnings_surprise["beat_rate"]
                trend = earnings_surprise.get("trend") or ""
                if br >= 0.75:
                    icon = "🎯" if trend != "deteriorating" else "⚠️"
                    badges.append(f":green[{icon} Beat-Rate {int(br*100)}% ({len(earnings_surprise.get('quarters', []))}Q)]")
                elif br <= 0.25:
                    badges.append(f":red[💀 Beat-Rate nur {int(br*100)}%]")
            # Anomalie-Badge
            if anomaly:
                if anomaly.get("is_volume_outlier"):
                    badges.append(f":orange[🚨 Volume-Outlier z={anomaly['volume_zscore']}]")
                if anomaly.get("is_return_outlier"):
                    z = anomaly.get("return_zscore", 0)
                    arrow = "📈" if z > 0 else "📉"
                    badges.append(f":orange[{arrow} Return-Outlier z={z}]")
            # Wikipedia-Page-View-Spike (Aufmerksamkeit)
            if wiki and wiki.get("spike_ratio", 0) >= 1.8:
                badges.append(
                    f":blue[📚 Wiki-Aufmerksamkeit {wiki['spike_ratio']:.1f}x "
                    f"({wiki['today']:,} Views)]"
                )
            if news_sentiment and news_sentiment.get("total_scored", 0) >= 3:
                ratio = news_sentiment.get("ratio")
                if ratio is not None:
                    if ratio >= 0.65:
                        badges.append(
                            f":green[📰 News {int(ratio*100)}% bullish "
                            f"({news_sentiment['bullish']}/{news_sentiment['total_scored']})]"
                        )
                    elif ratio <= 0.35:
                        badges.append(
                            f":red[📰 News {int((1-ratio)*100)}% bearish "
                            f"({news_sentiment['bearish']}/{news_sentiment['total_scored']})]"
                        )
            if badges:
                st.markdown(" · ".join(badges))
            # STRONG-Pattern-Reasons als Hinweis
            if pattern_reasons:
                st.markdown(
                    "**Multi-Signal:** " + " · ".join(f":violet[{r}]" for r in pattern_reasons)
                )

        with col_chart:
            if sparkline_data is not None and len(sparkline_data) > 1:
                st.plotly_chart(
                    render_sparkline(sparkline_data, label),
                    width="stretch",
                    config={"displayModeBar": False},
                    key=f"spark_{asset_type}_{ticker}_{key_suffix}",
                )

        with col_label:
            st.markdown(render_label_badge(label, big=True), unsafe_allow_html=True)
            if extra_metric:
                st.caption(extra_metric)
            # Star-Button für Watchlist (mit Entry-Price-Tracking)
            is_starred = ticker in st.session_state.watchlist
            star_label = "★ in Watchlist" if is_starred else "☆ Watch"
            if st.button(star_label, key=f"star_{asset_type}_{ticker}_{key_suffix}",
                         width="stretch"):
                new_wl = toggle_watchlist(ticker)
                st.session_state.watchlist = new_wl
                if ticker in new_wl:
                    # Frisch hinzugefügt → Entry tracken
                    record_watchlist_entry(ticker, price, label)
                else:
                    # Entfernt → Entry weg
                    remove_watchlist_entry(ticker)
                st.rerun()

        # Fundamentaldaten-Streifen (nur für Aktien)
        if fundamentals and asset_type == "stock":
            f = fundamentals
            fund_items = []
            if f.get("market_cap"):
                fund_items.append(f"**MCap:** {fmt_marketcap(f['market_cap'])}")
            if f.get("pe"):
                fund_items.append(f"**P/E:** {f['pe']:.1f}")
            if f.get("forward_pe"):
                fund_items.append(f"**fwd P/E:** {f['forward_pe']:.1f}")
            if f.get("dividend_yield"):
                fund_items.append(f"**Div:** {fmt_pct(f['dividend_yield'])}")
            if f.get("beta"):
                fund_items.append(f"**β:** {f['beta']:.2f}")
            if f.get("revenue_growth"):
                fund_items.append(f"**Rev-Growth:** {fmt_pct(f['revenue_growth'])}")
            if fund_items:
                st.caption(" · ".join(fund_items))

        with st.expander("Details: TradingView-Chart · History · News · Reddit · AI-Sentiment"):
            # TradingView Chart
            if tv_symbol:
                container_id = (
                    f"tv_{asset_type}_{ticker.replace('-', '_').replace('.', '_')}_{key_suffix}"
                )
                render_tradingview(tv_symbol, container_id, height=420)
            else:
                st.caption("(Kein TradingView-Symbol verfügbar)")

            # Score-History (Phase 6 — wird mit jedem Tag interessanter)
            try:
                history = get_history(ticker, days=30)
                if len(history) >= 2:
                    st.markdown("**Score-History (letzte 30 Tage)**")
                    hist_df = pd.DataFrame(history)[["date", "score", "mentions", "vol_spike"]]
                    hist_fig = go.Figure()
                    hist_fig.add_trace(go.Scatter(
                        x=hist_df["date"], y=hist_df["score"],
                        mode="lines+markers", name="Score",
                        line=dict(color="#16a34a", width=2),
                        fill="tozeroy", fillcolor="rgba(22,163,74,0.15)",
                    ))
                    hist_fig.add_hline(y=0, line=dict(color="#888", dash="dash"))
                    hist_fig.update_layout(
                        height=200, template="plotly_dark",
                        margin=dict(t=10, b=20, l=20, r=20),
                        yaxis_title="Score",
                    )
                    st.plotly_chart(
                        hist_fig, width="stretch",
                        key=f"hist_{asset_type}_{ticker}_{key_suffix}",
                    )
                elif len(history) == 1:
                    st.caption(
                        f"📊 History: 1 Datenpunkt vom {history[0]['date']}. "
                        "Ab 2 Tagen wird hier ein Score-Trend-Chart angezeigt."
                    )
                else:
                    st.caption("📊 Noch keine History. Wird ab dem zweiten Run gebaut.")
            except Exception:
                pass

            col_news, col_reddit = st.columns(2)
            with col_news:
                st.markdown("**News (Multi-Source, dedupliziert)**")
                headlines = render_news_block(
                    ticker, name if name and name != ticker else None, asset_type,
                )
            with col_reddit:
                st.markdown("**Reddit-Buzz**")
                posts = render_reddit_block(buzz) if buzz else []

            # Claude AI-Sentiment-Fusion
            if ANTHROPIC_API_KEY and (headlines or posts):
                with st.spinner("Claude analysiert News + Reddit..."):
                    ai = cached_claude(ticker, str(len(headlines)), str(len(posts)),
                                       tuple(headlines[:8]), tuple(posts[:5]))
                if ai:
                    score = ai.get("score", 0)
                    color = "green" if score > 0.15 else "red" if score < -0.15 else "gray"
                    st.markdown(
                        f"**🤖 Claude AI-Sentiment:** :{color}[{ai.get('label', '?').upper()}] "
                        f"({score:+.2f})"
                    )
                    st.caption(ai.get("summary", ""))

            # Options-Flow (Aktien) und Wiki-Daily-Series
            if asset_type == "stock":
                with st.expander("Options-Flow + Wiki-Aufmerksamkeit"):
                    opt = cached_options_flow(ticker)
                    if opt and opt.get("expiry"):
                        col_o1, col_o2, col_o3 = st.columns(3)
                        col_o1.metric("Call-Volume", f"{opt['total_call_volume']:,}")
                        col_o2.metric("Put-Volume", f"{opt['total_put_volume']:,}")
                        pcr = opt.get("put_call_ratio")
                        col_o3.metric("Put/Call-Ratio",
                                      f"{pcr:.2f}" if pcr is not None else "—")
                        st.caption(f"Next expiry: {opt['expiry']}")
                        unusual = opt.get("unusual_strikes") or []
                        if unusual:
                            st.markdown("**Unusual Activity:**")
                            for u in unusual[:5]:
                                arrow = "🟢" if u["type"] == "CALL" else "🔴"
                                st.markdown(
                                    f"{arrow} {u['type']} ${u['strike']:.0f} · "
                                    f"Vol {u['volume']:,} vs OI {u['open_interest']:,} "
                                    f"(**{u['vol_oi_ratio']}x**)"
                                )
                        else:
                            st.caption("Keine unusual options activity.")
                    if wiki and wiki.get("daily_series"):
                        st.markdown("**Wikipedia-Page-Views (letzte 14 Tage)**")
                        wd = pd.DataFrame(wiki["daily_series"], columns=["date", "views"])
                        wd["date"] = pd.to_datetime(wd["date"], format="%Y%m%d")
                        fig_w = go.Figure()
                        fig_w.add_trace(go.Bar(
                            x=wd["date"], y=wd["views"],
                            marker=dict(color="#3b82f6"),
                        ))
                        fig_w.update_layout(
                            height=180, template="plotly_dark",
                            margin=dict(t=10, b=10, l=20, r=10),
                        )
                        st.plotly_chart(fig_w, width="stretch",
                                        key=f"wiki_{ticker}_{key_suffix}")


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
    telegram_status = (
        ":green[✓ konfiguriert]" if (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        else ":orange[Token fehlt]"
    )
    st.markdown(f"**Reddit:** {reddit_mode}")
    st.markdown(f"**Telegram:** {telegram_status}")
    st.caption(f"Lookback: {NEWS_LOOKBACK_DAYS}d · {MAX_HEADLINES_PER_TICKER} Headlines/Ticker")

    # Storage-Stats (Phase 6)
    try:
        stats = get_storage_stats()
        if stats["snapshots"] > 0:
            st.markdown(
                f"**History:** {stats['days_tracked']} Tage · "
                f"{stats['snapshots']} Snapshots · "
                f"{stats['strong_signals']} STRONG-Events"
            )
    except Exception:
        pass

    # Swissquote-Whitelist (Phase 7)
    st.divider()
    st.subheader("Swissquote-Filter")
    current_wl = load_whitelist()
    if current_wl:
        st.markdown(f":green[✓ {len(current_wl)} Aktien in Whitelist aktiv]")
        with st.expander("Whitelist anzeigen"):
            st.code(", ".join(current_wl), language=None)
        if st.button("Whitelist löschen", key="wl_clear"):
            save_whitelist([])
            st.rerun()
    else:
        st.caption("Keine Whitelist aktiv — alle 40 Aktien werden gezeigt.")
    csv_file = st.file_uploader(
        "Swissquote-CSV hochladen", type=["csv", "txt"],
        help="Spalten-Header 'Symbol' oder 'Ticker' wird erkannt. "
             "Filtert dann das Dashboard auf nur diese Aktien.",
        key="wl_upload",
    )
    if csv_file is not None:
        tickers = parse_swissquote_csv(csv_file)
        if tickers:
            save_whitelist(tickers)
            st.success(f"{len(tickers)} Tickers in Whitelist gespeichert. Refresh in 2 Sek...")
            st.rerun()
        else:
            st.error("Keine Tickers im CSV erkannt. Prüfe Spalten-Name.")


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

# Labels berechnen + News-Sentiment-Ratio aggregieren (für Multi-Signal-Pattern)
for s in stock_rows:
    buzz = stock_buzz.get(s["ticker"], {})
    s["label"] = recommendation_label(
        s["score"],
        buzz.get("mentions_24h", 0),
        buzz.get("velocity", 0),
        has_earnings_soon=bool(s.get("earnings_date")),
    )
    s["buzz"] = buzz
    s["type"] = "stock"

for c in crypto_rows:
    buzz = crypto_buzz.get(c["ticker"], {})
    c["label"] = recommendation_label(
        c["score"],
        buzz.get("mentions_24h", 0),
        buzz.get("velocity", 0),
    )
    c["buzz"] = buzz
    c["type"] = "crypto"


# News-Sentiment + Multi-Signal-Pattern wird gleich beim Header berechnet,
# aber für alle Assets cheaply: leere news_sentiment dict, Pattern wird im
# Expander oder im Header neu berechnet wenn user es will. Hier Quick-Aggregation
# nur für die Top-Assets (statt 80 News-Calls beim Page-Load).

@st.cache_data(ttl=600, show_spinner=False)
def cached_news_sentiment_top(tickers_tuple: tuple, asset_type: str = "stock") -> dict:
    """Aggregiert news_sentiment_ratio nur für die Top-N (sonst zu viele Calls)."""
    out: dict[str, dict] = {}
    for t in tickers_tuple:
        try:
            headlines = cached_news(t, None, asset_type)
            out[t] = news_sentiment_ratio(headlines)
        except Exception:
            out[t] = {"ratio": None, "bullish": 0, "bearish": 0, "neutral": 0, "total_scored": 0}
    return out


# Top-Kandidaten ermitteln (nach simplem Label-Score) und news-sentiment nur für die holen
top_stock_tickers = tuple(s["ticker"] for s in
                          sorted(stock_rows, key=lambda x: -x["score"])[:15])
top_crypto_tickers = tuple(c["ticker"] for c in
                           sorted(crypto_rows, key=lambda x: -x["score"])[:15])

with st.spinner("Lade News-Sentiment für Top-Picks (Pattern-Detection)..."):
    stock_news_sent = cached_news_sentiment_top(top_stock_tickers, "stock")
    crypto_news_sent = cached_news_sentiment_top(top_crypto_tickers, "crypto")

# Multi-Signal-Pattern attachen
for s in stock_rows:
    s["news_sentiment"] = stock_news_sent.get(s["ticker"], {})
    pattern, reasons = multi_signal_pattern(s)
    s["pattern_label"] = pattern
    s["pattern_reasons"] = reasons
    if pattern:
        s["label"] = pattern  # Pattern-Label überschreibt regular label

for c in crypto_rows:
    c["news_sentiment"] = crypto_news_sent.get(c["ticker"], {})
    pattern, reasons = multi_signal_pattern(c)
    c["pattern_label"] = pattern
    c["pattern_reasons"] = reasons
    if pattern:
        c["label"] = pattern


# Wikipedia-Page-Views für Top-Aktien (gratis, parallel)
top_wiki_tickers = tuple(s["ticker"] for s in
                          sorted(stock_rows, key=lambda x: -x["score"])[:15])
with st.spinner("Lade Wikipedia-Aufmerksamkeit..."):
    wiki_data = cached_wiki_pageviews(top_wiki_tickers)
for s in stock_rows:
    s["wiki"] = wiki_data.get(s["ticker"])


# Snapshot in SQLite speichern (Phase 6 — History/Trend-Tracking)
try:
    save_snapshot_batch(stock_rows + crypto_rows)
except Exception as e:
    st.warning(f"Storage-Fehler: {e}")

# Whitelist anwenden (nur Aktien — Kryptos sind eh dynamisch top-40)
whitelist = load_whitelist()
stock_rows_filtered_by_wl = apply_whitelist(stock_rows, whitelist)

# Filter
stock_rows_f = [s for s in stock_rows_filtered_by_wl
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
wl_count = len([t for t in st.session_state.watchlist
                if t in [s["ticker"] for s in stock_rows] + [c["ticker"] for c in crypto_rows]])
tab_stocks, tab_crypto, tab_all, tab_watchlist, tab_backtest, tab_corr, tab_flow = st.tabs(
    [f"US-Aktien ({len(stock_rows_f)})",
     f"Krypto ({len(crypto_rows_f)})",
     "Alle Empfehlungen",
     f"★ Watchlist ({wl_count})",
     "Backtest",
     "Korrelation",
     "Money-Flow"]
)

def render_stock_card(s: dict, key_suffix: str = "main"):
    """Wrapper für eine Aktie."""
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
        fundamentals=s.get("fundamentals"),
        insider=s.get("insider"),
        key_suffix=key_suffix,
        news_sentiment=s.get("news_sentiment"),
        pattern_reasons=s.get("pattern_reasons"),
        earnings_surprise=s.get("earnings_surprise"),
        anomaly=s.get("anomaly"),
        wiki=s.get("wiki"),
    )


def render_crypto_card(c: dict, key_suffix: str = "main"):
    """Wrapper für eine Krypto."""
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
        key_suffix=key_suffix,
        news_sentiment=c.get("news_sentiment"),
        pattern_reasons=c.get("pattern_reasons"),
    )


with tab_stocks:
    if not stock_rows_f:
        st.info("Keine Aktien matchen die Filter.")
    for s in stock_rows_f:
        render_stock_card(s, key_suffix="main")

with tab_crypto:
    # CoinGecko Trending oben anzeigen
    trending = cached_trending_crypto()
    if trending:
        st.markdown("### 🔥 Trending (letzte 24h, CoinGecko)")
        cols = st.columns(min(7, len(trending)))
        for col, t in zip(cols, trending):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{t['ticker']}**")
                    st.caption(t["name"][:18])
                    if t.get("rank"):
                        st.caption(f"#{t['rank']}")
        st.divider()

    if not crypto_rows_f:
        st.info("Keine Kryptos matchen die Filter.")
    for c in crypto_rows_f:
        render_crypto_card(c, key_suffix="main")

with tab_all:
    st.subheader("Alle Assets sortiert nach Empfehlung")
    table = []
    for a in all_assets:
        buzz = a.get("buzz") or {}
        ins = a.get("insider") or {}
        table.append({
            "Ticker": a["ticker"],
            "Typ": "Aktie" if a["type"] == "stock" else "Krypto",
            "Label": a["label"],
            "Score": a["score"],
            "Preis": f"${a['price']:,.2f}" if a["price"] >= 1 else f"${a['price']:.6f}",
            "Mentions": buzz.get("mentions", 0),
            "Velocity": buzz.get("velocity", 0),
            "Insider-Käufe": ins.get("buys", 0),
            "Earnings": a.get("earnings_date") or "",
            "Signale": a["signals"],
        })
    if table:
        st.dataframe(pd.DataFrame(table), width="stretch", hide_index=True)
    else:
        st.info("Keine Daten nach Filterung.")


with tab_watchlist:
    st.subheader("Watchlist · Deine gestarteten Picks")
    wl = st.session_state.watchlist
    if not wl:
        st.info("Watchlist leer. Klick auf '☆ Watch' bei einem Ticker, um ihn zur Watchlist hinzuzufügen.")
    else:
        wl_stocks = [s for s in stock_rows if s["ticker"] in wl]
        wl_crypto = [c for c in crypto_rows if c["ticker"] in wl]

        # P&L-Übersicht (Phase 7)
        watched_all = wl_stocks + wl_crypto
        pnl_rows = compute_watchlist_pnl(watched_all)
        if pnl_rows:
            st.markdown("### Performance seit Entry")
            total_pnl_pct = sum(p["pnl_pct"] for p in pnl_rows) / len(pnl_rows)
            wins = sum(1 for p in pnl_rows if p["pnl_pct"] > 0)
            losses = sum(1 for p in pnl_rows if p["pnl_pct"] < 0)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Ø Return", f"{total_pnl_pct:+.2f}%")
            m2.metric("Gewinner", f"{wins}/{len(pnl_rows)}")
            m3.metric("Verlierer", f"{losses}/{len(pnl_rows)}")
            best = max(pnl_rows, key=lambda x: x["pnl_pct"])
            m4.metric("Bester Pick", f"{best['ticker']} {best['pnl_pct']:+.1f}%")

            pnl_df = pd.DataFrame([{
                "Ticker": p["ticker"],
                "Entry-Datum": p["entry_date"],
                "Entry-Label": p["entry_label"],
                "Entry-Preis": f"${p['entry_price']:,.2f}" if p["entry_price"] >= 1 else f"${p['entry_price']:.6f}",
                "Aktuell": f"${p['price']:,.2f}" if p["price"] >= 1 else f"${p['price']:.6f}",
                "P&L %": p["pnl_pct"],
                "Tage": p["days_held"],
                "Aktuelles Label": p["label"],
            } for p in pnl_rows])
            st.dataframe(pnl_df, width="stretch", hide_index=True,
                         column_config={
                             "P&L %": st.column_config.NumberColumn("P&L %", format="%+.2f"),
                         })

        if wl_stocks:
            st.markdown(f"#### Aktien-Details ({len(wl_stocks)})")
            for s in wl_stocks:
                render_stock_card(s, key_suffix="wl")
        if wl_crypto:
            st.markdown(f"#### Krypto-Details ({len(wl_crypto)})")
            for c in wl_crypto:
                render_crypto_card(c, key_suffix="wl")
        st.divider()
        if st.button("Watchlist komplett leeren", type="secondary"):
            for t in list(wl):
                toggle_watchlist(t)
                remove_watchlist_entry(t)
            st.session_state.watchlist = []
            st.rerun()


with tab_backtest:
    st.subheader("Backtest · zwei Modi")

    bt_mode = st.radio(
        "Backtest-Quelle",
        ["Tech-Indikatoren (90d OHLC, immer verfügbar)",
         "SQLite-Snapshot-History (echte Multi-Signal-Scores, wird mit jedem Run reicher)"],
        horizontal=False,
        key="bt_mode",
    )
    is_sql_mode = "SQLite" in bt_mode

    st.caption(
        ("Strategie: BUY bei Score ≥ +3 (aus SQLite-Verlauf), SELL bei Score ≤ -1. "
         f"Snapshots in DB: {get_storage_stats().get('snapshots', 0)} aus "
         f"{get_storage_stats().get('days_tracked', 0)} Tagen.")
        if is_sql_mode else
        "Strategie: Tech-Score ≥ +3 → BUY, ≤ -3 → SELL. Startkapital $10'000. Vergleich gegen Buy-and-Hold."
    )

    bt_universe = ["– Aktien –"] + [s["ticker"] for s in stock_rows] + \
                  ["– Krypto –"] + [c["ticker"] for c in crypto_rows]
    bt_selection = st.selectbox(
        "Asset wählen",
        bt_universe,
        index=1 if len(bt_universe) > 1 else 0,
    )

    if is_sql_mode and bt_selection and not bt_selection.startswith("–"):
        result = backtest_from_snapshots(bt_selection)
        if result["days_in_db"] < 2:
            st.warning(
                f"Nur {result['days_in_db']} Tage History in der DB. "
                "Lass das Tool ein paar Tage laufen — pro Refresh wird ein Snapshot gespeichert."
            )
        else:
            m_a, m_b, m_c, m_d = st.columns(4)
            m_a.metric("Strategie-Return", f"{result['total_return_pct']:+.2f}%")
            m_b.metric("Trades", result["n_trades"])
            m_c.metric("Win-Rate", f"{result['win_rate']:.1f}%")
            m_d.metric("DB-Tage", result["days_in_db"])
            if result["trades"]:
                st.markdown("#### Trade-Historie (echte Multi-Signal-Scores)")
                st.dataframe(pd.DataFrame(result["trades"]),
                             width="stretch", hide_index=True)
            else:
                st.info("Keine Trade-Signale im DB-Verlauf.")

    elif not is_sql_mode and bt_selection and not bt_selection.startswith("–"):
        # Suche OHLC für gewählten Ticker (Aktien oder Krypto)
        bt_df = stock_ohlc.get(bt_selection)
        if bt_df is None:
            bt_df = crypto_ohlc.get(bt_selection)
        if bt_df is None or len(bt_df) < 60:
            st.warning("Nicht genug OHLC-Daten für Backtest.")
        else:
            result = backtest_ticker(bt_df, bt_selection, initial_capital=10000)

            # Header-Metrics
            m_a, m_b, m_c, m_d, m_e = st.columns(5)
            m_a.metric("Strategie-Return", f"{result['total_return_pct']:+.2f}%")
            m_b.metric("Buy-and-Hold", f"{result['buy_hold_pct']:+.2f}%")
            alpha = result["alpha_pct"]
            m_c.metric("Alpha", f"{alpha:+.2f}%",
                       delta_color="normal" if alpha >= 0 else "inverse")
            m_d.metric("Trades", result["n_trades"])
            m_e.metric("Win-Rate", f"{result['win_rate']:.1f}%")

            # Equity-Curve
            if result["equity_curve"]:
                ec_df = pd.DataFrame({
                    "day": list(range(len(result["equity_curve"]))),
                    "equity": result["equity_curve"],
                })
                fig_ec = go.Figure()
                fig_ec.add_trace(go.Scatter(
                    x=ec_df["day"], y=ec_df["equity"],
                    line=dict(color="#16a34a", width=2),
                    fill="tozeroy", fillcolor="rgba(22,163,74,0.15)",
                    name="Strategie-Equity",
                ))
                fig_ec.add_hline(
                    y=10000, line=dict(color="#888", dash="dash"),
                    annotation_text="Startkapital",
                )
                fig_ec.update_layout(
                    height=320, template="plotly_dark",
                    margin=dict(t=20, b=20, l=20, r=20),
                    yaxis_title="Equity ($)", xaxis_title="Trading-Tag",
                )
                st.plotly_chart(fig_ec, width="stretch")

            # Trades-Tabelle
            if result["trades"]:
                st.markdown("#### Trade-Historie")
                trades_df = pd.DataFrame(result["trades"])
                st.dataframe(trades_df, width="stretch", hide_index=True)
            else:
                st.info("Keine BUY/SELL-Signale im Backtest-Zeitraum.")


with tab_corr:
    st.subheader("Korrelations-Matrix — tägliche Returns (30 Tage)")
    st.caption(
        "Wer bewegt sich zusammen? Werte zwischen -1 (gegenläufig) und +1 (gleichläufig). "
        "Diversifikation = niedrige Korrelationen."
    )
    corr_choice = st.radio(
        "Asset-Klasse",
        ["US-Aktien", "Krypto", "Top 20 BUY/WATCH (gemischt)"],
        horizontal=True,
        key="corr_choice",
    )
    if corr_choice == "US-Aktien":
        corr_tickers = tuple(s["ticker"] for s in stock_rows[:25])
        ohlc_src = stock_ohlc_p
    elif corr_choice == "Krypto":
        corr_tickers = tuple(c["ticker"] for c in crypto_rows[:25])
        ohlc_src = crypto_ohlc_p
    else:
        # Mixed: top score
        mix_assets = sorted(stock_rows + crypto_rows,
                            key=lambda x: -x["score"])[:20]
        corr_tickers = tuple(a["ticker"] for a in mix_assets)
        ohlc_src = {**stock_ohlc_p, **crypto_ohlc_p}

    with st.spinner("Berechne Korrelationen..."):
        corr_df = cached_correlation_matrix(corr_tickers, ohlc_src)

    if not corr_df.empty:
        fig_corr = go.Figure(data=go.Heatmap(
            z=corr_df.values, x=corr_df.columns, y=corr_df.index,
            colorscale="RdBu_r", zmin=-1, zmax=1,
            colorbar=dict(title="ρ"),
            hovertemplate="%{y} ↔ %{x}: <b>%{z}</b><extra></extra>",
        ))
        fig_corr.update_layout(
            height=min(80 + 30 * len(corr_df), 700),
            template="plotly_dark",
            margin=dict(t=20, b=20, l=20, r=20),
        )
        st.plotly_chart(fig_corr, width="stretch", key="corr_heatmap")
    else:
        st.info("Keine Daten für die Korrelation verfügbar.")


with tab_flow:
    st.subheader("Sektor-Money-Flow — wo läuft das Geld rein?")
    st.caption(
        "5d-Performance + Volumen-Trend. Positive Werte = Geld fließt rein. "
        "Folge dem Geld."
    )
    flow_data = cached_sector_money_flow()
    if flow_data:
        flow_df = pd.DataFrame(flow_data)
        fig_flow = go.Figure(data=go.Bar(
            x=flow_df["money_flow_score"], y=flow_df["sector"],
            orientation="h",
            marker=dict(
                color=flow_df["money_flow_score"],
                colorscale="RdYlGn", cmin=-5, cmax=5,
                showscale=False,
            ),
            text=[f"{s['change_5d']:+.1f}% | Vol {s['vol_trend_pct']:+.0f}%"
                  for s in flow_data],
            textposition="auto",
        ))
        fig_flow.update_layout(
            height=400, template="plotly_dark",
            margin=dict(t=20, b=20, l=20, r=20),
            xaxis_title="Money-Flow-Score (Performance + Volume × 0.3)",
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig_flow, width="stretch", key="sector_flow")

        st.dataframe(flow_df[["etf", "sector", "change_5d", "change_30d",
                              "vol_trend_pct", "money_flow_score"]],
                     width="stretch", hide_index=True,
                     column_config={
                         "etf": "ETF",
                         "sector": "Sektor",
                         "change_5d": st.column_config.NumberColumn("5d %", format="%+.2f"),
                         "change_30d": st.column_config.NumberColumn("30d %", format="%+.2f"),
                         "vol_trend_pct": st.column_config.NumberColumn("Vol-Trend %", format="%+.0f"),
                         "money_flow_score": st.column_config.NumberColumn("Money-Flow", format="%+.2f"),
                     })


st.divider()
st.caption(
    "Daten: yfinance, Binance, CoinGecko, Marketaux/Finnhub/NewsAPI/StockTwits/CryptoPanic, "
    "Yahoo Finance, Google News RSS, MarketWatch/CNBC/Reuters RSS, Reddit, alternative.me Fear&Greed. "
    f"Cache: 5-30 Min · Lookback: {NEWS_LOOKBACK_DAYS}d"
)


# Sidebar-Telegram-Buttons (am Ende rendern, weil Datenzugriff nötig)
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    with st.sidebar:
        st.divider()
        st.subheader("Telegram-Aktionen")
        if st.button("📤 Morning-Digest senden", width="stretch", key="tg_digest"):
            with st.spinner("Baue Digest..."):
                msg = build_morning_digest(stock_rows, crypto_rows, fg)
            if send_telegram(msg):
                st.success("Digest gesendet!")
            else:
                st.error("Senden fehlgeschlagen.")
        if st.button("🔔 Test-Alert senden", width="stretch", key="tg_test"):
            ok = send_telegram(f"*Test* via Dashboard — {datetime.now():%H:%M}")
            if ok:
                st.success("Test gesendet!")
            else:
                st.error("Senden fehlgeschlagen.")
        # Diff-Alerts manuell triggern (sendet nur NEUE STRONG-Signale)
        if st.button("🚀 Diff-Alerts senden (nur Neue)", width="stretch", key="tg_diff"):
            from main import load_last_signals, save_signals, format_alert_signal
            last = load_last_signals().get("patterns", {})
            new_signals = {}
            sent = 0
            for a in stock_rows + crypto_rows:
                pattern = a.get("pattern_label")
                if pattern:
                    new_signals[a["ticker"]] = pattern
                    if last.get(a["ticker"]) != pattern:
                        if send_telegram(format_alert_signal(a, pattern, a.get("pattern_reasons", []))):
                            sent += 1
            save_signals({"timestamp": datetime.now().isoformat(),
                          "patterns": new_signals})
            if sent > 0:
                st.success(f"{sent} neue Alert(s) gesendet.")
            else:
                st.info("Keine neuen STRONG-Signale.")
