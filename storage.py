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
    signals TEXT,
    UNIQUE(date, ticker)
);
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
    """UPSERT je (date, ticker). Liefert Anzahl gespeicherter Rows."""
    init_db()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()
    count = 0
    with get_conn() as conn:
        for a in assets:
            buzz = a.get("buzz") or {}
            ns = a.get("news_sentiment") or {}
            try:
                conn.execute("""
                    INSERT INTO snapshots (
                        timestamp, date, ticker, asset_type, score, label, pattern_label,
                        price, rsi, vol_spike, mentions, mentions_24h, velocity,
                        news_today, news_velocity, news_bullish_ratio, signals
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, ticker) DO UPDATE SET
                        timestamp=excluded.timestamp,
                        score=excluded.score,
                        label=excluded.label,
                        pattern_label=excluded.pattern_label,
                        price=excluded.price,
                        rsi=excluded.rsi,
                        vol_spike=excluded.vol_spike,
                        mentions=excluded.mentions,
                        mentions_24h=excluded.mentions_24h,
                        velocity=excluded.velocity,
                        news_today=excluded.news_today,
                        news_velocity=excluded.news_velocity,
                        news_bullish_ratio=excluded.news_bullish_ratio,
                        signals=excluded.signals
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
    """Letzte N Tage Snapshot-History für einen Ticker."""
    init_db()
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM snapshots
            WHERE ticker = ? AND date >= ?
            ORDER BY date ASC
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
