"""
alerts.py — Telegram-Alert-Dispatcher (Phase 5)

Berechnet aktuelle Signale und schickt Alerts via Telegram für:
- Neue STRONG BUY / STRONG SELL Signale
- Reddit-Spikes (Velocity ≥ 3.0)
- Insider-Massen-Käufe oder -Verkäufe
- Earnings <3 Tage

Vergleicht mit last_signals.json damit nicht jeden Lauf das Gleiche gemeldet wird.

Verwendung:
  python alerts.py             # Diff-basierte Alerts (nur Neues)
  python alerts.py --digest    # Tägliche Morning-Zusammenfassung
  python alerts.py --test      # Test-Nachricht "Bot lebt"
"""

import argparse
import sys
from datetime import datetime

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
    recommendation_label,
    save_signals,
    send_telegram,
    REDDIT_CRYPTO_SUBS,
    REDDIT_STOCK_SUBS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)


def collect_signals() -> list[dict]:
    """Lädt alle Daten und berechnet pattern_label für jedes Asset."""
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
        c["label"] = recommendation_label(
            c["score"],
            c["buzz"].get("mentions_24h", 0),
            c["buzz"].get("velocity", 0),
        )
        pattern_label, reasons = multi_signal_pattern(c)
        c["pattern_label"] = pattern_label
        c["pattern_reasons"] = reasons

    return stocks + cryptos, stocks, cryptos


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
    args = parser.parse_args()

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
    else:
        n = run_diff_alerts(assets)
        print(f"{n} neue Alert(s) gesendet.")
