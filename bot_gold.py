"""
bot_gold.py — Gold Trade Pro v1.31
Strategy: D1 fractal breakout on GC=F. 5-bar fractal (left=2, right=2).
Entry at close that breaks fractal. SL = fractal ± ATR(14). RR=4.0. Risk 1%.
"""
import logging
import numpy as np
import pandas as pd

from common import (
    RISK_PCT, fetch_gc_daily, get_balance, update_balance,
    log_trade, load_state, save_state, trade_opened, trade_closed,
)

STRATEGY   = "GOLD_TRADE_PRO"
INSTRUMENT = "GC=F"
RR         = 4.0
ATR_PERIOD = 14
LEFT       = 2
RIGHT      = 2

log = logging.getLogger(STRATEGY)


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    if len(tr) < period:
        return float(tr.mean()) if len(tr) > 0 else 1.0
    atr = tr[:period].mean()
    for v in tr[period:]:
        atr = (atr * (period - 1) + v) / period
    return float(atr)


def _find_fractals(df: pd.DataFrame):
    h = df["high"].values
    l = df["low"].values
    n = len(h)
    frac_hi, frac_lo = [], []
    for i in range(LEFT, n - RIGHT):
        if all(h[i] > h[i - j] for j in range(1, LEFT + 1)) and \
           all(h[i] > h[i + j] for j in range(1, RIGHT + 1)):
            frac_hi.append((i, float(h[i]), str(df.index[i].date())))
        if all(l[i] < l[i - j] for j in range(1, LEFT + 1)) and \
           all(l[i] < l[i + j] for j in range(1, RIGHT + 1)):
            frac_lo.append((i, float(l[i]), str(df.index[i].date())))
    return frac_hi, frac_lo


def _close(state: dict, pos: dict, exit_price: float, reason: str, date_str: str):
    direction   = pos["direction"]
    risk_dollar = pos.get("risk_dollar", 0.0)
    if direction == "long":
        pnl_r = (exit_price - pos["entry"]) / (pos["entry"] - pos["sl"])
    else:
        pnl_r = (pos["entry"] - exit_price) / (pos["sl"] - pos["entry"])
    pnl_dollar  = risk_dollar * pnl_r
    new_bal     = pos["balance_at_entry"] + pnl_dollar
    update_balance(STRATEGY, new_bal)
    log_trade(
        strategy=STRATEGY, trade_date=pos["entry_date"],
        entry_time=pos["entry_date"], exit_time=date_str,
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
    state   = load_state(STRATEGY)
    df      = fetch_gc_daily()
    if df.empty or len(df) < 20:
        log.warning("Insufficient GC=F daily data")
        return

    today_str = str(df.index[-1].date())
    if state.get("last_checked") == today_str:
        log.info("Already ran today")
        return

    balance = get_balance(STRATEGY)
    atr     = _atr(df, ATR_PERIOD)

    # ── EXIT CHECK: scan all bars since entry ────────────────────────────────
    if state.get("position"):
        pos      = state["position"]
        entry_dt = pd.Timestamp(pos["entry_date"])
        since    = df[pd.to_datetime(df.index).normalize() > entry_dt]
        for _, bar in since.iterrows():
            if pos["direction"] == "long":
                if bar["low"] <= pos["sl"]:
                    _close(state, pos, pos["sl"], "SL", today_str)
                    state["last_checked"] = today_str
                    save_state(STRATEGY, state)
                    return
                if bar["high"] >= pos["tp"]:
                    _close(state, pos, pos["tp"], "TP", today_str)
                    state["last_checked"] = today_str
                    save_state(STRATEGY, state)
                    return
            else:
                if bar["high"] >= pos["sl"]:
                    _close(state, pos, pos["sl"], "SL", today_str)
                    state["last_checked"] = today_str
                    save_state(STRATEGY, state)
                    return
                if bar["low"] <= pos["tp"]:
                    _close(state, pos, pos["tp"], "TP", today_str)
                    state["last_checked"] = today_str
                    save_state(STRATEGY, state)
                    return
        state["last_checked"] = today_str
        save_state(STRATEGY, state)
        return  # still open, hold

    # ── ENTRY SCAN ────────────────────────────────────────────────────────────
    # Fractals need RIGHT=2 confirmed bars; use df[:-2] so last 2 bars serve as right-bar proof.
    lookup = df.iloc[:-2]
    if len(lookup) < LEFT + RIGHT + 1:
        state["last_checked"] = today_str
        save_state(STRATEGY, state)
        return

    frac_hi, frac_lo = _find_fractals(lookup)
    close = float(df.iloc[-1]["close"])

    if frac_hi:
        last_h_idx, last_h_price, last_h_date = frac_hi[-1]
        if close > last_h_price and state.get("last_long_frac") != last_h_date:
            entry       = close
            sl          = last_h_price - atr
            risk        = entry - sl
            risk_dollar = balance * RISK_PCT[STRATEGY]
            if risk > 0 and risk_dollar > 0:
                tp  = entry + RR * risk
                pos = {
                    "direction": "long", "entry": entry, "sl": sl, "tp": tp,
                    "entry_date": today_str, "risk_dollar": risk_dollar,
                    "balance_at_entry": balance,
                }
                state["position"]      = pos
                state["last_long_frac"] = last_h_date
                state["last_checked"]  = today_str
                save_state(STRATEGY, state)
                trade_opened(STRATEGY, "long", entry, sl, tp, risk_dollar)
                log.info("LONG open entry=%.2f sl=%.2f tp=%.2f", entry, sl, tp)
                return

    if frac_lo:
        last_l_idx, last_l_price, last_l_date = frac_lo[-1]
        if close < last_l_price and state.get("last_short_frac") != last_l_date:
            entry       = close
            sl          = last_l_price + atr
            risk        = sl - entry
            risk_dollar = balance * RISK_PCT[STRATEGY]
            if risk > 0 and risk_dollar > 0:
                tp  = entry - RR * risk
                pos = {
                    "direction": "short", "entry": entry, "sl": sl, "tp": tp,
                    "entry_date": today_str, "risk_dollar": risk_dollar,
                    "balance_at_entry": balance,
                }
                state["position"]       = pos
                state["last_short_frac"] = last_l_date
                state["last_checked"]   = today_str
                save_state(STRATEGY, state)
                trade_opened(STRATEGY, "short", entry, sl, tp, risk_dollar)
                log.info("SHORT open entry=%.2f sl=%.2f tp=%.2f", entry, sl, tp)
                return

    state["last_checked"] = today_str
    save_state(STRATEGY, state)
