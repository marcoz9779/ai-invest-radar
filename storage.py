"""
storage.py — SQLite-Persistenz für Tages-Snapshots (Phase 6)

Schema:
- snapshots: pro Tag pro Ticker ein Eintrag (Score, Indikatoren, Mentions, News)
- strong_signals: jede Pattern-Erkennung mit Zeitstempel

Wird genutzt für:
- Score-Trend-Charts ("steigt 3 Tage in Folge")
- echte Mention-Velocity (heute vs gestern statt heute vs 7d-Ø)
- Earnings/Surprise-Tracking (Phase B)
- Backtest auf realen historischen Score-Werten
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    score INTEGER,
    label TEXT,
    pattern_label TEXT,
    price REAL,
    rsi REAL,
    vol_spike REAL,
    mentions INTEGER,
    mentions_24h INTEGER,
    velocity REAL,
    news_today INTEGER,
    news_velocity REAL,
    news_bullish_ratio REAL,
    signals TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticker_ts ON snapshots(ticker, timestamp);
CREATE INDEX IF NOT EXISTS idx_ticker_date ON snapshots(ticker, date);
CREATE INDEX IF NOT EXISTS idx_date ON snapshots(date);
CREATE INDEX IF NOT EXISTS idx_pattern ON snapshots(pattern_label) WHERE pattern_label IS NOT NULL;

CREATE TABLE IF NOT EXISTS strong_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    ticker TEXT NOT NULL,
    pattern_label TEXT NOT NULL,
    score INTEGER,
    price REAL,
    reasons TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON strong_signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_time ON strong_signals(timestamp);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def save_snapshot_batch(assets: list[dict]) -> int:
    """INSERT pro Run einen neuen Row (echte intra-day-History).

    Min-Abstand 10 Min: wenn der letzte Snapshot eines Tickers <10 Min alt ist,
    überschreiben wir ihn statt einen neuen Row anzulegen (verhindert Spam
    durch rapid Page-Refreshes).
    """
    init_db()
    today = datetime.now().strftime("%Y-%m-%d")
    now_dt = datetime.now()
    now = now_dt.isoformat()
    count = 0
    with get_conn() as conn:
        for a in assets:
            buzz = a.get("buzz") or {}
            ns = a.get("news_sentiment") or {}
            try:
                # Check ob letzter Snapshot <10 Min alt
                last = conn.execute(
                    "SELECT id, timestamp FROM snapshots "
                    "WHERE ticker = ? ORDER BY id DESC LIMIT 1",
                    (a["ticker"],),
                ).fetchone()
                replace_last = False
                if last:
                    try:
                        last_dt = datetime.fromisoformat(last["timestamp"])
                        if (now_dt - last_dt).total_seconds() < 600:
                            replace_last = True
                    except Exception:
                        pass

                if replace_last:
                    conn.execute("""
                        UPDATE snapshots SET
                            timestamp=?, date=?, asset_type=?, score=?, label=?,
                            pattern_label=?, price=?, rsi=?, vol_spike=?,
                            mentions=?, mentions_24h=?, velocity=?,
                            news_today=?, news_velocity=?, news_bullish_ratio=?,
                            signals=?
                        WHERE id=?
                    """, (
                        now, today, a.get("type", "stock"),
                        a.get("score", 0), a.get("label"), a.get("pattern_label"),
                        a.get("price"), a.get("rsi"), a.get("vol_spike"),
                        buzz.get("mentions", 0), buzz.get("mentions_24h", 0),
                        buzz.get("velocity", 0),
                        a.get("news_today_count", 0), a.get("news_velocity_x", 0),
                        ns.get("ratio"), a.get("signals", ""),
                        last["id"],
                    ))
                    count += 1
                    continue

                conn.execute("""
                    INSERT INTO snapshots (
                        timestamp, date, ticker, asset_type, score, label, pattern_label,
                        price, rsi, vol_spike, mentions, mentions_24h, velocity,
                        news_today, news_velocity, news_bullish_ratio, signals
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now, today, a["ticker"], a.get("type", "stock"),
                    a.get("score", 0), a.get("label"), a.get("pattern_label"),
                    a.get("price"), a.get("rsi"), a.get("vol_spike"),
                    buzz.get("mentions", 0), buzz.get("mentions_24h", 0),
                    buzz.get("velocity", 0),
                    a.get("news_today_count", 0), a.get("news_velocity_x", 0),
                    ns.get("ratio"), a.get("signals", "")
                ))
                count += 1

                # Bei neuem STRONG-Signal in separater Tabelle loggen
                if a.get("pattern_label"):
                    last = conn.execute("""
                        SELECT pattern_label FROM strong_signals
                        WHERE ticker = ?
                        ORDER BY id DESC LIMIT 1
                    """, (a["ticker"],)).fetchone()
                    if not last or last["pattern_label"] != a["pattern_label"]:
                        conn.execute("""
                            INSERT INTO strong_signals
                                (timestamp, ticker, pattern_label, score, price, reasons)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            now, a["ticker"], a["pattern_label"],
                            a.get("score", 0), a.get("price", 0),
                            ", ".join(a.get("pattern_reasons", []) or [])
                        ))
            except Exception:
                continue
    return count


def get_history(ticker: str, days: int = 30) -> list[dict]:
    """Letzte N Tage Snapshot-History für einen Ticker (sortiert nach timestamp)."""
    init_db()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM snapshots
            WHERE ticker = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (ticker, since)).fetchall()
    return [dict(r) for r in rows]


def get_score_trend(ticker: str, days: int = 7) -> list[tuple[str, int]]:
    """Liefert [(date, score), ...] für die letzten N Tage."""
    return [(r["date"], r["score"]) for r in get_history(ticker, days)]


def get_consecutive_score_changes(ticker: str, days: int = 7) -> int:
    """+N = N Tage in Folge gestiegen, -N = N Tage in Folge gefallen.

    Liefert 0 wenn keine Konsistenz oder zu wenig Daten.
    """
    trend = get_score_trend(ticker, days)
    if len(trend) < 2:
        return 0
    scores = [s for _, s in trend]
    consecutive_up = consecutive_down = 0
    for i in range(len(scores) - 1, 0, -1):
        if scores[i] > scores[i - 1]:
            if consecutive_down > 0:
                break
            consecutive_up += 1
        elif scores[i] < scores[i - 1]:
            if consecutive_up > 0:
                break
            consecutive_down += 1
        else:
            break
    return consecutive_up if consecutive_up > 0 else -consecutive_down


def get_recent_strong_signals(days: int = 30) -> list[dict]:
    """Liefert STRONG-Signal-Events der letzten N Tage."""
    init_db()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM strong_signals
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
        """, (since,)).fetchall()
    return [dict(r) for r in rows]


def backtest_from_snapshots(ticker: str, initial_capital: float = 10000) -> dict:
    """Backtest auf echten historischen Scores aus SQLite (statt nur Tech-Indikatoren).

    Strategie: BUY wenn Score >=3 (zum ersten Mal), SELL wenn Score <=-1.
    Liefert {trades, total_return_pct, win_rate, final_equity, days_in_db}.
    """
    history = get_history(ticker, days=365)
    if len(history) < 2:
        return {"trades": [], "total_return_pct": 0, "win_rate": 0,
                "final_equity": initial_capital, "days_in_db": len(history)}

    equity = initial_capital
    shares = 0
    entry_price = 0
    entry_date = None
    trades: list[dict] = []

    for i, row in enumerate(history):
        price = row.get("price") or 0
        if price <= 0:
            continue
        score = row.get("score") or 0
        date = row["date"]

        # SELL bei Score <=-1 mit offener Position
        if score <= -1 and shares > 0:
            sell_value = shares * price
            pnl_pct = (price - entry_price) / entry_price * 100
            trades.append({
                "entry_date": entry_date, "exit_date": date,
                "entry_price": round(entry_price, 4),
                "exit_price": round(price, 4),
                "pnl_pct": round(pnl_pct, 2),
                "pnl_usd": round(sell_value - shares * entry_price, 2),
            })
            equity = sell_value
            shares = 0

        # BUY bei Score >=3 ohne Position
        elif score >= 3 and shares == 0:
            shares = equity / price
            entry_price = price
            entry_date = date

    # Offene Position schließen
    if shares > 0:
        final_price = history[-1].get("price") or entry_price
        pnl_pct = (final_price - entry_price) / entry_price * 100
        trades.append({
            "entry_date": entry_date, "exit_date": "open",
            "entry_price": round(entry_price, 4),
            "exit_price": round(final_price, 4),
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(shares * final_price - shares * entry_price, 2),
        })
        equity = shares * final_price

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    win_rate = (wins / len(trades) * 100) if trades else 0
    total_return = (equity / initial_capital - 1) * 100
    return {
        "trades": trades,
        "n_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "total_return_pct": round(total_return, 2),
        "final_equity": round(equity, 2),
        "days_in_db": len(history),
    }


def get_stats() -> dict:
    """Allgemeine DB-Stats für Dashboard-Footer."""
    init_db()
    with get_conn() as conn:
        snap_count = conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"]
        days_count = conn.execute("SELECT COUNT(DISTINCT date) AS n FROM snapshots").fetchone()["n"]
        signal_count = conn.execute("SELECT COUNT(*) AS n FROM strong_signals").fetchone()["n"]
        oldest = conn.execute("SELECT MIN(date) AS d FROM snapshots").fetchone()
        newest = conn.execute("SELECT MAX(date) AS d FROM snapshots").fetchone()
    return {
        "snapshots": snap_count,
        "days_tracked": days_count,
        "strong_signals": signal_count,
        "oldest_date": oldest["d"] if oldest else None,
        "newest_date": newest["d"] if newest else None,
    }
