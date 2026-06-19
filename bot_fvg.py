"""
bot_fvg.py — TJR FVG Gap-Fill 50pct
Strategy: ICT FVG 50% fill entry on GC=F 15min.
Params: RR=5.0, fill_threshold=50%, max_wait=16 bars, atr_buf=0. Risk 1%.
"""
import logging
import pandas as pd
from datetime import datetime
import pytz

from common import (
    RISK_PCT, fetch_gc_15m, get_balance, update_balance,
    log_trade, load_state, save_state, trade_opened, trade_closed,
)

STRATEGY       = "FVG_XAUUSD"
INSTRUMENT     = "GC=F"
RR             = 5.0
FILL_THRESHOLD = 0.50
MAX_WAIT       = 16
MIN_SL_PTS     = 2.0   # XAUUSD 15m typical spread ~$0.30-0.50; <2pt SL = noise

ET  = pytz.timezone("America/New_York")
log = logging.getLogger(STRATEGY)


def _detect_fvgs(df: pd.DataFrame) -> list:
    h, l = df["high"].values, df["low"].values
    fvgs  = []
    for i in range(2, len(df)):
        if h[i - 2] < l[i]:                         # Bullish FVG
            gap_bot    = float(h[i - 2])
            gap_top    = float(l[i])
            gap_size   = gap_top - gap_bot
            fill_level = gap_top - FILL_THRESHOLD * gap_size
            fvgs.append({"side": "long", "gap_bot": gap_bot, "gap_top": gap_top,
                          "fill_level": fill_level, "bar_idx": i})
        if l[i - 2] > h[i]:                         # Bearish FVG
            gap_bot    = float(h[i])
            gap_top    = float(l[i - 2])
            gap_size   = gap_top - gap_bot
            fill_level = gap_bot + FILL_THRESHOLD * gap_size
            fvgs.append({"side": "short", "gap_bot": gap_bot, "gap_top": gap_top,
                          "fill_level": fill_level, "bar_idx": i})
    return fvgs


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
    state.pop("wait_bars", None)
    log.info("%s closed %s exit=%.2f pnl=$%.2f (%.2fR)", STRATEGY, reason, exit_price, pnl_dollar, pnl_r)


def run():
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return
    if not (7 <= now_et.hour < 18):
        return

    state = load_state(STRATEGY)
    df    = fetch_gc_15m(days=5)
    if df.empty or len(df) < 10:
        log.warning("Insufficient GC=F 15m data")
        return

    today     = now_et.date()
    today_str = str(today)
    balance   = get_balance(STRATEGY)

    # ── EXIT CHECK ────────────────────────────────────────────────────────────
    if state.get("position"):
        pos = state["position"]
        try:
            entry_ts = pd.Timestamp(pos["entry_time"])
            if entry_ts.tzinfo is None:
                entry_ts = entry_ts.tz_localize(ET)
        except Exception:
            entry_ts = pd.Timestamp.min.tz_localize(ET)

        since    = df[df.index > entry_ts]
        wait_cnt = len(since)   # bars elapsed since entry; don't accumulate state (causes double-count)

        for idx, bar in since.iterrows():
            date_str = str(idx.date())
            if pos["direction"] == "long":
                if bar["low"] <= pos["sl"]:
                    _close(state, pos, pos["sl"], "SL", date_str)
                    state["last_trade_date"] = today_str
                    save_state(STRATEGY, state)
                    return
                if bar["high"] >= pos["tp"]:
                    _close(state, pos, pos["tp"], "TP", date_str)
                    state["last_trade_date"] = today_str
                    save_state(STRATEGY, state)
                    return
            else:
                if bar["high"] >= pos["sl"]:
                    _close(state, pos, pos["sl"], "SL", date_str)
                    state["last_trade_date"] = today_str
                    save_state(STRATEGY, state)
                    return
                if bar["low"] <= pos["tp"]:
                    _close(state, pos, pos["tp"], "TP", date_str)
                    state["last_trade_date"] = today_str
                    save_state(STRATEGY, state)
                    return

        # Max-wait expiry
        if wait_cnt >= MAX_WAIT:
            last_bar = df.iloc[-1]
            date_str = str(df.index[-1].date())
            _close(state, pos, float(last_bar["close"]), "EXPIRED", date_str)
            state["last_trade_date"] = today_str
            save_state(STRATEGY, state)
            return

        state["wait_bars"] = wait_cnt
        save_state(STRATEGY, state)
        return  # hold

    # One trade per day
    if state.get("last_trade_date") == today_str:
        return

    # ── FVG SCAN ──────────────────────────────────────────────────────────────
    fvgs = _detect_fvgs(df.tail(80))
    if not fvgs:
        return

    fvg  = fvgs[-1]   # most recent FVG
    bar  = df.iloc[-1]
    risk_dollar = balance * RISK_PCT[STRATEGY]

    if fvg["side"] == "long":
        if bar["low"] <= fvg["fill_level"] and bar["close"] >= fvg["gap_bot"]:
            entry = fvg["fill_level"]
            sl    = fvg["gap_bot"]
            risk  = entry - sl
            if risk < MIN_SL_PTS:
                log.info("FVG LONG skipped: SL dist %.2f < MIN_SL_PTS %.2f", risk, MIN_SL_PTS)
                return
            tp  = entry + RR * risk
            pos = {
                "direction": "long", "entry": entry, "sl": sl, "tp": tp,
                "entry_date": today_str, "entry_time": str(df.index[-1]),
                "risk_dollar": risk_dollar, "balance_at_entry": balance,
            }
            state["position"]        = pos
            state["wait_bars"]       = 0
            state["last_trade_date"] = today_str
            save_state(STRATEGY, state)
            trade_opened(STRATEGY, "long", entry, sl, tp, risk_dollar)
            log.info("FVG LONG open entry=%.2f sl=%.2f tp=%.2f", entry, sl, tp)

    elif fvg["side"] == "short":
        if bar["high"] >= fvg["fill_level"] and bar["close"] <= fvg["gap_top"]:
            entry = fvg["fill_level"]
            sl    = fvg["gap_top"]
            risk  = sl - entry
            if risk < MIN_SL_PTS:
                log.info("FVG SHORT skipped: SL dist %.2f < MIN_SL_PTS %.2f", risk, MIN_SL_PTS)
                return
            tp  = entry - RR * risk
            pos = {
                "direction": "short", "entry": entry, "sl": sl, "tp": tp,
                "entry_date": today_str, "entry_time": str(df.index[-1]),
                "risk_dollar": risk_dollar, "balance_at_entry": balance,
            }
            state["position"]        = pos
            state["wait_bars"]       = 0
            state["last_trade_date"] = today_str
            save_state(STRATEGY, state)
            trade_opened(STRATEGY, "short", entry, sl, tp, risk_dollar)
            log.info("FVG SHORT open entry=%.2f sl=%.2f tp=%.2f", entry, sl, tp)
