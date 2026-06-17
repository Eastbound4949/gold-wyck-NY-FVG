"""
common.py — Shared utilities: DB, state, data fetchers, Telegram, metrics.
"""
import os
import sys
import json
import sqlite3
import logging
import requests
import numpy as np
import pandas as pd
import time as _time
import random as _random
import yfinance as yf
from contextlib import contextmanager

# ── CONFIG ───────────────────────────────────────────────────────────────────
DB_PATH         = os.getenv("DB_PATH",          "data/trades.db")
STATE_DIR       = os.getenv("STATE_DIR",        "data/state")
INITIAL_BALANCE = float(os.getenv("STARTING_CAPITAL", "10000.0"))

RISK_PCT = {
    "GOLD_TRADE_PRO": float(os.getenv("GOLD_RISK_PCT", "0.010")),  # 1%
    "WYCKOFF_BTC":    float(os.getenv("BTC_RISK_PCT",  "0.020")),  # 2%
    "NY_OPEN_BR":     float(os.getenv("SPY_RISK_PCT",  "0.010")),  # 1%
    "FVG_XAUUSD":     float(os.getenv("FVG_RISK_PCT",  "0.010")),  # 1%
}

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID",   "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("common")

# ── DB ────────────────────────────────────────────────────────────────────────
@contextmanager
def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    with _db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy      TEXT,
                trade_date    TEXT,
                entry_time    TEXT,
                exit_time     TEXT,
                direction     TEXT,
                instrument    TEXT,
                entry         REAL,
                sl            REAL,
                tp            REAL,
                exit_price    REAL,
                exit_type     TEXT,
                pnl_dollar    REAL,
                pnl_r         REAL,
                risk_dollar   REAL,
                balance_after REAL,
                created_at    TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS balances (
                strategy   TEXT PRIMARY KEY,
                balance    REAL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS equity_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy   TEXT,
                balance    REAL,
                logged_at  TEXT DEFAULT (datetime('now'))
            );
        """)
    log.info("DB ready: %s", DB_PATH)


def get_balance(strategy: str) -> float:
    try:
        with _db() as con:
            row = con.execute(
                "SELECT balance FROM balances WHERE strategy=?", (strategy,)
            ).fetchone()
        return row[0] if row else INITIAL_BALANCE
    except Exception:
        return INITIAL_BALANCE


def update_balance(strategy: str, val: float):
    with _db() as con:
        con.execute("""
            INSERT INTO balances (strategy, balance, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(strategy) DO UPDATE SET
                balance=excluded.balance, updated_at=excluded.updated_at
        """, (strategy, val))
        con.execute(
            "INSERT INTO equity_log (strategy, balance) VALUES (?, ?)",
            (strategy, val),
        )


def log_trade(strategy, trade_date, entry_time, exit_time, direction, instrument,
              entry, sl, tp, exit_price, exit_type, pnl_dollar, pnl_r, risk_dollar, balance_after):
    with _db() as con:
        con.execute("""
            INSERT INTO trades
                (strategy, trade_date, entry_time, exit_time, direction, instrument,
                 entry, sl, tp, exit_price, exit_type, pnl_dollar, pnl_r, risk_dollar, balance_after)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (strategy, trade_date, entry_time, exit_time, direction, instrument,
              entry, sl, tp, exit_price, exit_type, pnl_dollar, pnl_r, risk_dollar, balance_after))


def get_trades_df(strategy: str = None) -> pd.DataFrame:
    sql    = "SELECT * FROM trades"
    params = ()
    if strategy:
        sql    += " WHERE strategy=?"
        params  = (strategy,)
    sql += " ORDER BY id"
    try:
        with _db() as con:
            return pd.read_sql_query(sql, con, params=params)
    except Exception:
        return pd.DataFrame()


def get_equity_log(strategy: str) -> pd.DataFrame:
    try:
        with _db() as con:
            return pd.read_sql_query(
                "SELECT * FROM equity_log WHERE strategy=? ORDER BY id",
                con, params=(strategy,),
            )
    except Exception:
        return pd.DataFrame()


def calc_metrics(df: pd.DataFrame) -> dict:
    empty = {"trades": 0, "wins": 0, "losses": 0, "wr_pct": 0.0,
             "profit_factor": 0.0, "net_dollar": 0.0, "roi_pct": 0.0,
             "max_dd_pct": 0.0, "expectancy": 0.0, "balance": INITIAL_BALANCE}
    if df.empty or "pnl_dollar" not in df.columns:
        return empty

    wins        = int((df["pnl_dollar"] > 0).sum())
    losses      = int((df["pnl_dollar"] <= 0).sum())
    net         = df["pnl_dollar"].sum()
    bal         = df["balance_after"].iloc[-1]
    wr          = wins / len(df) * 100
    gross_wins  = df[df["pnl_dollar"] > 0]["pnl_dollar"].sum()
    gross_loss  = abs(df[df["pnl_dollar"] < 0]["pnl_dollar"].sum())
    pf          = round(gross_wins / gross_loss, 2) if gross_loss > 0 else 999.0
    expectancy  = round(df["pnl_r"].mean(), 3)

    eq   = df["balance_after"].values
    peak = np.maximum.accumulate(eq)
    dd   = ((eq - peak) / peak).min() * 100 if len(eq) > 1 else 0.0
    roi  = (bal - INITIAL_BALANCE) / INITIAL_BALANCE * 100

    return {
        "trades": len(df), "wins": wins, "losses": losses,
        "wr_pct": round(wr, 1), "profit_factor": pf,
        "net_dollar": round(net, 2), "roi_pct": round(roi, 2),
        "max_dd_pct": round(dd, 2), "expectancy": expectancy,
        "balance": round(bal, 2),
    }

# ── STATE ─────────────────────────────────────────────────────────────────────
def load_state(name: str) -> dict:
    path = os.path.join(STATE_DIR, f"{name}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(name: str, state: dict):
    path = os.path.join(STATE_DIR, f"{name}.json")
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)

# ── DATA FETCHERS ─────────────────────────────────────────────────────────────
def _flatten(raw: pd.DataFrame) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.columns = [str(c).lower() for c in raw.columns]
    return raw.dropna()


def _yf_download(ticker: str, period: str, interval: str, **kwargs) -> pd.DataFrame:
    """yfinance download with jitter + 3 retries (30/60/90s backoff) on rate-limit errors.
    Jitter spreads concurrent bot requests across a 20s window to avoid simultaneous hits."""
    _time.sleep(_random.uniform(0, 20))  # stagger vs other Railway bots on same IP
    for attempt in range(4):
        try:
            raw = yf.download(ticker, period=period, interval=interval, **kwargs)
            if raw is not None and not raw.empty:
                return raw
        except Exception as e:
            if attempt == 3:
                log.warning("yfinance %s failed after 4 attempts: %s", ticker, e)
                return pd.DataFrame()
            wait = 30 * (attempt + 1)  # 30s, 60s, 90s
            log.warning("yfinance %s attempt %d failed (%s) — retry in %ds", ticker, attempt + 1, e, wait)
            _time.sleep(wait)
    return pd.DataFrame()


def fetch_gc_daily(bars: int = 60) -> pd.DataFrame:
    raw = _yf_download("GC=F", period="90d", interval="1d", progress=False, auto_adjust=True)
    df  = _flatten(raw)
    return df.iloc[:-1] if len(df) > 1 else df   # drop potentially incomplete bar


def fetch_btc_1h(days: int = 5) -> pd.DataFrame:
    raw = _yf_download("BTC-USD", period=f"{days}d", interval="1h", progress=False, auto_adjust=True)
    df  = _flatten(raw)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.iloc[:-1] if len(df) > 1 else df


def fetch_spy_1h(days: int = 5) -> pd.DataFrame:
    import pytz
    ET  = pytz.timezone("America/New_York")
    raw = _yf_download("SPY", period=f"{days}d", interval="1h", progress=False, auto_adjust=True)
    df  = _flatten(raw)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    return df.iloc[:-1] if len(df) > 1 else df


def fetch_gc_15m(days: int = 5) -> pd.DataFrame:
    import pytz
    ET  = pytz.timezone("America/New_York")
    raw = _yf_download("GC=F", period=f"{days}d", interval="15m", progress=False, auto_adjust=True)
    df  = _flatten(raw)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    return df.iloc[:-1] if len(df) > 1 else df

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning("Telegram: %s", e)


def bot_started(strategy: str, equity: float):
    send_telegram(f"🟢 <b>{strategy}</b> online | ${equity:,.2f}")


def trade_opened(strategy: str, direction: str, entry: float, sl: float, tp: float, risk_dollar: float):
    emoji = "📈" if direction == "long" else "📉"
    send_telegram(
        f"{emoji} <b>{strategy}</b> {direction.upper()} OPEN\n"
        f"Entry: {entry:.4f} | SL: {sl:.4f} | TP: {tp:.4f}\n"
        f"Risk: ${risk_dollar:.2f}"
    )


def trade_closed(strategy: str, direction: str, entry: float, exit_price: float,
                 pnl_dollar: float, pnl_r: float, balance: float, reason: str):
    emoji = "✅" if pnl_dollar > 0 else "❌"
    send_telegram(
        f"{emoji} <b>{strategy}</b> {direction.upper()} {reason}\n"
        f"Entry: {entry:.4f} → Exit: {exit_price:.4f}\n"
        f"P&L: ${pnl_dollar:+.2f} ({pnl_r:+.2f}R) | Bal: ${balance:,.2f}"
    )
