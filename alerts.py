"""
alerts.py — Telegram-Alert-Dispatcher (Phase 5)

Modi:
  python alerts.py --test       # Test-Nachricht
  python alerts.py --digest     # Morning-Zusammenfassung (für 08:00 etc)
  python alerts.py              # Diff-Alerts: nur neue STRONG-Signale
  python alerts.py --watcher    # Event-Driven Watcher (für 30min-Poll):
                                #   Volume-Spike >5x, News-Velocity >3x,
                                #   neue STRONG-Signale. 4h-Throttle pro Ticker.
  python alerts.py --check-hour 8  # Skip wenn aktuelle CH-Stunde nicht 8 ist
                                   # (für DST-robuste UTC-Cron-Trigger)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    CH_TZ = ZoneInfo("Europe/Zurich")
except ImportError:
    CH_TZ = None

from main import (
    analyze_all_stocks,
    analyze_crypto,
    build_morning_digest,
    fetch_fear_greed_crypto,
    fetch_news,
    fetch_reddit_buzz_bulk,
    fetch_rss_topstories,
    format_alert_signal,
    load_last_signals,
    multi_signal_pattern,
    news_sentiment_ratio,
    news_velocity,
    recommendation_label,
    save_signals,
    send_telegram,
    REDDIT_CRYPTO_SUBS,
    REDDIT_STOCK_SUBS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

WATCHER_STATE_PATH = Path(__file__).parent / "last_watcher.json"
WATCHER_THROTTLE_HOURS = 4

# Schwellwerte für moderate Aggressivität
VOLUME_SPIKE_THRESHOLD = 5.0   # Aktien-Volume heute vs 20d-Ø
NEWS_VELOCITY_THRESHOLD = 3.0  # News heute vs 7d-Ø


def collect_signals():
    """Lädt alle Daten und berechnet pattern_label + news_velocity je Asset."""
    stocks, _ = analyze_all_stocks()
    cryptos, _ = analyze_crypto(40)

    stock_buzz = fetch_reddit_buzz_bulk([s["ticker"] for s in stocks], REDDIT_STOCK_SUBS)
    crypto_buzz = fetch_reddit_buzz_bulk([c["ticker"] for c in cryptos], REDDIT_CRYPTO_SUBS)

    rss_pool = fetch_rss_topstories()

    # Aktien aufbereiten
    for s in stocks:
        s["type"] = "stock"
        s["buzz"] = stock_buzz.get(s["ticker"], {})
        headlines = fetch_news(s["ticker"], None, "stock", rss_pool)
        s["news_sentiment"] = news_sentiment_ratio(headlines)
        today_count, velocity_x = news_velocity(headlines)
        s["news_today_count"] = today_count
        s["news_velocity_x"] = velocity_x
        s["label"] = recommendation_label(
            s["score"],
            s["buzz"].get("mentions_24h", 0),
            s["buzz"].get("velocity", 0),
            has_earnings_soon=bool(s.get("earnings_date")),
        )
        pattern_label, reasons = multi_signal_pattern(s)
        s["pattern_label"] = pattern_label
        s["pattern_reasons"] = reasons

    # Kryptos aufbereiten
    for c in cryptos:
        c["type"] = "crypto"
        c["buzz"] = crypto_buzz.get(c["ticker"], {})
        headlines = fetch_news(c["ticker"], c.get("name"), "crypto", rss_pool)
        c["news_sentiment"] = news_sentiment_ratio(headlines)
        today_count, velocity_x = news_velocity(headlines)
        c["news_today_count"] = today_count
        c["news_velocity_x"] = velocity_x
        c["label"] = recommendation_label(
            c["score"],
            c["buzz"].get("mentions_24h", 0),
            c["buzz"].get("velocity", 0),
        )
        pattern_label, reasons = multi_signal_pattern(c)
        c["pattern_label"] = pattern_label
        c["pattern_reasons"] = reasons

    return stocks + cryptos, stocks, cryptos


# ============================================================================
# WATCHER (event-driven Polling, alle 30 Min)
# ============================================================================
def _load_watcher_state() -> dict:
    if not WATCHER_STATE_PATH.exists():
        return {}
    try:
        return json.loads(WATCHER_STATE_PATH.read_text())
    except Exception:
        return {}


def _save_watcher_state(state: dict) -> None:
    WATCHER_STATE_PATH.write_text(json.dumps(state, indent=2))


def _watcher_alert_reason(asset: dict) -> str | None:
    """Findet den dringlichsten Grund für einen Live-Alert, sonst None."""
    # 1) Massive Volume-Spike (nur Aktien — hat vol_spike)
    vs = asset.get("vol_spike")
    if vs is not None and vs >= VOLUME_SPIKE_THRESHOLD:
        return f"📊 Volume-Spike: *{vs:.1f}x* normales Tages-Volumen"

    # 2) News-Velocity-Spike (mind. 2 News heute UND deutlich überdurchschnittlich)
    nv = asset.get("news_velocity_x", 0)
    if nv >= NEWS_VELOCITY_THRESHOLD and asset.get("news_today_count", 0) >= 2:
        return f"📰 News-Velocity: *{nv}x* normale Frequenz ({asset.get('news_today_count', 0)} News heute)"

    # 3) Neues STRONG-Signal
    if asset.get("pattern_label"):
        reasons = ", ".join(asset.get("pattern_reasons", [])[:3])
        return f"🚀 *{asset['pattern_label']}*: {reasons}"

    # 4) Reddit-Mega-Spike (>3x)
    buzz = asset.get("buzz") or {}
    if buzz.get("velocity", 0) >= 3.0 and buzz.get("mentions_24h", 0) >= 5:
        return f"🔥 Reddit-Spike: *{buzz['velocity']}x* ({buzz['mentions_24h']} Mentions in 24h)"

    return None


def run_watcher() -> int:
    """Event-driven Watcher: Live-Alerts bei Volume/News/Pattern-Spikes.

    Throttle: gleicher Ticker max 1 Alert / 4h.
    """
    assets, _stocks, _cryptos = collect_signals()
    state = _load_watcher_state()
    last_alerts: dict[str, str] = state.get("alerts", {})

    now_utc = datetime.now(timezone.utc)
    throttle_seconds = WATCHER_THROTTLE_HOURS * 3600
    alerts_sent = 0

    for a in assets:
        ticker = a["ticker"]

        # Throttle-Check
        last_str = last_alerts.get(ticker)
        if last_str:
            try:
                last_dt = datetime.fromisoformat(last_str)
                if (now_utc - last_dt).total_seconds() < throttle_seconds:
                    continue
            except Exception:
                pass

        reason = _watcher_alert_reason(a)
        if not reason:
            continue

        price = a.get("price", 0)
        price_str = f"${price:,.2f}" if price >= 1 else f"${price:.6f}"
        msg = (
            f"⚡ *AI Invest Radar — Live-Alert*\n"
            f"`{ticker}` ({a.get('type', 'asset')})  ·  {price_str}  ·  Score {a.get('score', 0):+d}\n\n"
            f"{reason}"
        )
        if send_telegram(msg):
            alerts_sent += 1
            last_alerts[ticker] = now_utc.isoformat()
            print(f"[OK] Live-Alert: {ticker} — {reason[:80]}")

    state["alerts"] = last_alerts
    state["last_check"] = now_utc.isoformat()
    _save_watcher_state(state)
    return alerts_sent


def run_diff_alerts(assets: list[dict]) -> int:
    """Sendet Alerts nur für NEUE STRONG-Signale (Diff zu last_signals.json)."""
    last = load_last_signals()
    last_patterns = last.get("patterns", {})
    new_signals = {}
    alerts_sent = 0

    for a in assets:
        ticker = a["ticker"]
        pattern = a.get("pattern_label")
        if pattern:
            new_signals[ticker] = pattern
            if last_patterns.get(ticker) != pattern:
                # Neuer/geänderter pattern_label → Alert
                msg = format_alert_signal(a, pattern, a.get("pattern_reasons", []))
                if send_telegram(msg):
                    alerts_sent += 1
                    print(f"[OK] Alert gesendet: {ticker} → {pattern}")
                else:
                    print(f"[FAIL] Alert nicht gesendet für {ticker}", file=sys.stderr)

    # Speichern für nächsten Lauf
    save_signals({
        "timestamp": datetime.now().isoformat(),
        "patterns": new_signals,
    })
    return alerts_sent


def run_morning_digest(stocks: list[dict], cryptos: list[dict]) -> bool:
    fg = fetch_fear_greed_crypto()
    msg = build_morning_digest(stocks, cryptos, fg)
    return send_telegram(msg)


def run_test() -> bool:
    return send_telegram(
        f"*Test*: AI Invest Radar Bot lebt. ({datetime.now():%Y-%m-%d %H:%M})"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--digest", action="store_true", help="Morning-Zusammenfassung")
    parser.add_argument("--test", action="store_true", help="Test-Nachricht")
    parser.add_argument("--watcher", action="store_true",
                        help="Event-Driven Watcher: Volume/News/Pattern-Spikes")
    parser.add_argument("--check-hour", type=int, default=None,
                        help="Skip wenn aktuelle CH-Stunde nicht diese ist "
                             "(für DST-robuste UTC-Cron-Trigger)")
    args = parser.parse_args()

    # DST-robuster Hour-Check (für GitHub Actions UTC-Cron)
    if args.check_hour is not None and CH_TZ is not None:
        ch_now = datetime.now(CH_TZ)
        if ch_now.hour != args.check_hour:
            print(f"Skip: CH-Zeit {ch_now.strftime('%H:%M')} ≠ Target {args.check_hour}:00")
            sys.exit(0)

    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("FEHLER: TELEGRAM_BOT_TOKEN oder TELEGRAM_CHAT_ID nicht in .env gesetzt.",
              file=sys.stderr)
        sys.exit(1)

    if args.test:
        ok = run_test()
        print("Test-Alert gesendet." if ok else "Fehler beim Senden.")
        sys.exit(0 if ok else 1)

    print("Lade Daten und berechne Signale...")
    assets, stocks, cryptos = collect_signals()

    if args.digest:
        ok = run_morning_digest(stocks, cryptos)
        print("Morning-Digest gesendet." if ok else "Fehler beim Senden.")
    elif args.watcher:
        n = run_watcher()
        print(f"{n} Live-Alert(s) gesendet.")
    else:
        n = run_diff_alerts(assets)
        print(f"{n} neue Alert(s) gesendet.")
