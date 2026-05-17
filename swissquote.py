"""
swissquote.py — Swissquote-Whitelist + Watchlist-P&L (Phase 7)

Lädst du deine handelbare Aktien-Liste als CSV hoch und das Dashboard filtert
nur diese Tickers. Plus: Watchlist-P&L simuliert was passiert wäre, wenn du
beim ersten BUY-Signal gekauft hättest.
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

WHITELIST_PATH = Path(__file__).parent / "swissquote_whitelist.json"
WATCHLIST_ENTRIES_PATH = Path(__file__).parent / "watchlist_entries.json"


# ============================================================================
# Whitelist
# ============================================================================
def load_whitelist() -> list[str]:
    if not WHITELIST_PATH.exists():
        return []
    try:
        return json.loads(WHITELIST_PATH.read_text())
    except Exception:
        return []


def save_whitelist(tickers: list[str]) -> None:
    cleaned = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    WHITELIST_PATH.write_text(json.dumps(cleaned, indent=2))


def parse_swissquote_csv(file) -> list[str]:
    """Extrahiert Ticker-Symbols aus einem hochgeladenen CSV.

    Akzeptiert verschiedene Formate: Spalten-Name 'Symbol', 'Ticker', 'symbol',
    'ISIN' (wird ignoriert), oder erste Spalte falls Header unbekannt.
    """
    try:
        df = pd.read_csv(file, sep=None, engine="python")
    except Exception:
        try:
            file.seek(0)
        except Exception:
            pass
        try:
            df = pd.read_csv(file, sep=";")
        except Exception:
            return []

    # Try common column names
    candidate_cols = ["Symbol", "Ticker", "symbol", "ticker", "TICKER", "SYMBOL"]
    found_col = None
    for c in candidate_cols:
        if c in df.columns:
            found_col = c
            break
    if found_col is None:
        # Fallback: erste Spalte
        found_col = df.columns[0]

    tickers = (
        df[found_col].dropna().astype(str).str.strip().str.upper().tolist()
    )
    # Filter: keine ISINs (12-chars all alphanum) oder leere Strings
    return [t for t in tickers if t and 1 <= len(t) <= 6 and t.isalnum()]


def apply_whitelist(stocks: list[dict], whitelist: list[str]) -> list[dict]:
    """Filtert die Aktien-Liste auf Whitelist-Tickers (oder pass-through wenn leer)."""
    if not whitelist:
        return stocks
    wl_set = set(whitelist)
    return [s for s in stocks if s["ticker"] in wl_set]


# ============================================================================
# Watchlist-Entries (P&L-Tracking)
# ============================================================================
def load_watchlist_entries() -> dict:
    """Liefert dict {ticker: {entry_price, entry_date, label_at_entry}}."""
    if not WATCHLIST_ENTRIES_PATH.exists():
        return {}
    try:
        return json.loads(WATCHLIST_ENTRIES_PATH.read_text())
    except Exception:
        return {}


def save_watchlist_entries(entries: dict) -> None:
    WATCHLIST_ENTRIES_PATH.write_text(json.dumps(entries, indent=2))


def record_watchlist_entry(ticker: str, price: float, label: str) -> dict:
    """Wenn Ticker neu in Watchlist → entry-price + Datum festhalten."""
    entries = load_watchlist_entries()
    if ticker not in entries:
        entries[ticker] = {
            "entry_price": float(price),
            "entry_date": datetime.now().strftime("%Y-%m-%d"),
            "label_at_entry": label,
        }
        save_watchlist_entries(entries)
    return entries


def remove_watchlist_entry(ticker: str) -> dict:
    entries = load_watchlist_entries()
    if ticker in entries:
        del entries[ticker]
        save_watchlist_entries(entries)
    return entries


def compute_watchlist_pnl(watched_assets: list[dict]) -> list[dict]:
    """Für jeden watchlist-Eintrag: berechne P&L vs aktuellem Preis.

    Liefert eine angereicherte Liste mit pnl_pct, pnl_usd, days_held.
    """
    entries = load_watchlist_entries()
    today = datetime.now()
    out = []
    for a in watched_assets:
        ticker = a["ticker"]
        entry = entries.get(ticker)
        if not entry:
            continue
        current = float(a.get("price", 0))
        entry_price = float(entry.get("entry_price", 0))
        if entry_price <= 0:
            continue
        pnl_pct = (current - entry_price) / entry_price * 100
        try:
            entry_dt = datetime.strptime(entry["entry_date"], "%Y-%m-%d")
            days_held = (today - entry_dt).days
        except Exception:
            days_held = None
        out.append({
            **a,
            "entry_price": round(entry_price, 4),
            "entry_date": entry["entry_date"],
            "entry_label": entry.get("label_at_entry", ""),
            "pnl_pct": round(pnl_pct, 2),
            "days_held": days_held,
        })
    return out
