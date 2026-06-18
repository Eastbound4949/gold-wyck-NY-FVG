"""
bot_wyckoff_btc.py — Wyckoff Intra-Day BTC
Strategy: Prev-day H/L reversal + EMA20 daily bias on BTC-USD 1H.
Params: RR=2.0, touch_buffer=0.002, sl_atr_mult=0.5, BIAS_FILTER=ON. Risk 2%.
"""
import logging
import numpy as np
import pandas as pd
from datetime import timedelta
import pytz

from common import (
    RISK_PCT, fetch_btc_1h, get_balance, update_balance,
    log_trade, load_state, save_state, trade_opened, trade_closed,
)

STRATEGY     = "WYCKOFF_BTC"
INSTRUMENT   = "BTC-USD"
RR           = 2.0
TOUCH_BUFFER = 0.002
SL_ATR_MULT  = 0.5
EMA_PERIOD   = 20

UTC = pytz.UTC
log = logging.getLogger(STRATEGY)


def _build_daily(df: pd.DataFrame):
    daily = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    daily["ema20"] = daily["close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    daily["bias"]  = np.where(daily["close"] >= daily["ema20"], "UP", "DOWN")
    return daily


def _close(state: dict, pos: dict, exit_price: float, reason: str, date_str: str):
    direction   = pos["direction"]
    risk_dollar = pos.get("risk_dollar", 0.0)
    if direction == "long":
        pnl_r = (exit_price - pos["entry"]) / (pos["entry"] - pos["sl"])
    else:
        pnl_r = (pos["entry"] - exit_price) / (pos["sl"] - pos["entry"])
    pnl_dollar = risk_dollar * pnl_r
    new_bal    = pos["balance_at_entry"] + pnl_dollar
    update_balance(STRATEGY, new_bal)
    log_trade(
        strategy=STRATEGY, trade_date=pos["entry_date"],
        entry_time=pos["entry_time"], exit_time=date_str,
        direction=direction, instrument=INSTRUMENT,
        entry=pos["entry"], sl=pos["sl"], tp=pos["tp"],
        exit_price=exit_price, exit_type=reason,
        pnl_dollar=pnl_dollar, pnl_r=round(pnl_r, 4),
        risk_dollar=risk_dollar, balance_after=new_bal,
    )
    trade_closed(STRATEGY, direction, pos["entry"], exit_price, pnl_dollar, pnl_r, new_bal, reason)
    state.pop("position", None)
    log.info("%s closed %s exit=%.2f pnl=$%.2f (%.2fR)", STRATEGY, reason, exit_price, pnl_dollar, pnl_r)


def run():
    state = load_state(STRATEGY)
    df    = fetch_btc_1h(days=6)
    if df.empty or len(df) < 24:
        log.warning("Insufficient BTC-USD 1H data")
        return

    now_utc   = pd.Timestamp.now(tz=UTC)
    today     = now_utc.date()
    yesterday = today - timedelta(days=1)

    daily     = _build_daily(df)
    today_ts  = pd.Timestamp(today, tz=UTC)
    yest_ts   = pd.Timestamp(yesterday, tz=UTC)

    if yest_ts not in daily.index:
        log.info("No prior-day data")
        return

    prev_high = float(daily.loc[yest_ts, "high"])
    prev_low  = float(daily.loc[yest_ts, "low"])
    bias      = daily.loc[today_ts, "bias"] if today_ts in daily.index else "UP"

    # ATR from last 48 bars
    recent = df.tail(48)
    atr    = float((recent["high"] - recent["low"]).mean())
    if atr == 0:
        return

    balance = get_balance(STRATEGY)

    # ── EXIT CHECK: scan bars since entry ────────────────────────────────────
    if state.get("position"):
        pos = state["position"]
        try:
            entry_ts = pd.Timestamp(pos["entry_time"])
            if entry_ts.tzinfo is None:
                entry_ts = entry_ts.tz_localize(UTC)
        except Exception:
            entry_ts = pd.Timestamp.min.tz_localize(UTC)

        since = df[df.index > entry_ts]
        for idx, bar in since.iterrows():
            date_str = str(idx.date())
            if pos["direction"] == "long":
                if bar["low"] <= pos["sl"]:
                    _close(state, pos, pos["sl"], "SL", date_str)
                    state["last_trade_date"] = str(today)
                    save_state(STRATEGY, state)
                    return
                if bar["high"] >= pos["tp"]:
                    _close(state, pos, pos["tp"], "TP", date_str)
                    state["last_trade_date"] = str(today)
                    save_state(STRATEGY, state)
                    return
            else:
                if bar["high"] >= pos["sl"]:
                    _close(state, pos, pos["sl"], "SL", date_str)
                    state["last_trade_date"] = str(today)
                    save_state(STRATEGY, state)
                    return
                if bar["low"] <= pos["tp"]:
                    _close(state, pos, pos["tp"], "TP", date_str)
                    state["last_trade_date"] = str(today)
                    save_state(STRATEGY, state)
                    return
        save_state(STRATEGY, state)
        return  # hold

    # One trade per day
    if state.get("last_trade_date") == str(today):
        return

    # ── ENTRY SCAN: today's completed bars ───────────────────────────────────
    today_bars = df[df.index.date == today]
    if today_bars.empty:
        return

    risk_dollar = balance * RISK_PCT[STRATEGY]

    for idx, bar in today_bars.iterrows():
        # LONG: touches prev_low + closes above (bias UP)
        if (bar["low"] <= prev_low * (1 + TOUCH_BUFFER) and
                bar["close"] > prev_low and bias == "UP"):
            entry = float(bar["close"])
            sl    = prev_low - atr * SL_ATR_MULT
            risk  = entry - sl
            if risk <= 0:
                continue
            tp  = entry + RR * risk
            pos = {
                "direction": "long", "entry": entry, "sl": sl, "tp": tp,
                "entry_date": str(today), "entry_time": str(idx),
                "risk_dollar": risk_dollar, "balance_at_entry": balance,
            }
            state["position"]        = pos
            state["last_trade_date"] = str(today)
            save_state(STRATEGY, state)
            trade_opened(STRATEGY, "long", entry, sl, tp, risk_dollar)
            log.info("LONG open entry=%.2f sl=%.2f tp=%.2f bias=%s", entry, sl, tp, bias)
            return

        # SHORT: touches prev_high + closes below (bias DOWN)
        if (bar["high"] >= prev_high * (1 - TOUCH_BUFFER) and
                bar["close"] < prev_high and bias == "DOWN"):
            entry = float(bar["close"])
            sl    = prev_high + atr * SL_ATR_MULT
            risk  = sl - entry
            if risk <= 0:
                continue
            tp  = entry - RR * risk
            pos = {
                "direction": "short", "entry": entry, "sl": sl, "tp": tp,
                "entry_date": str(today), "entry_time": str(idx),
                "risk_dollar": risk_dollar, "balance_at_entry": balance,
            }
            state["position"]        = pos
            state["last_trade_date"] = str(today)
            save_state(STRATEGY, state)
            trade_opened(STRATEGY, "short", entry, sl, tp, risk_dollar)
            log.info("SHORT open entry=%.2f sl=%.2f tp=%.2f bias=%s", entry, sl, tp, bias)
            return
