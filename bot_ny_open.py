"""
bot_ny_open.py — ig_06 NY Open Break & Retest
Strategy: SPY 1H ORB state machine (break → retest → entry). Long-only.
Params: ATR(20), RR=4.0, retest_tol=0.6, sl_mult=0.75. Risk 1%.
"""
import logging
import numpy as np
import pandas as pd
from datetime import datetime
import pytz

from common import (
    RISK_PCT, fetch_spy_1h, get_balance, update_balance,
    log_trade, load_state, save_state, trade_opened, trade_closed,
)

STRATEGY   = "NY_OPEN_BR"
INSTRUMENT = "SPY"
RR         = 4.0
ATR_PERIOD = 20
RETEST_TOL = 0.6
SL_MULT    = 0.75

ET  = pytz.timezone("America/New_York")
log = logging.getLogger(STRATEGY)


def _atr(df: pd.DataFrame, period: int = 20) -> float:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h[1:]-l[1:], np.maximum(abs(h[1:]-c[:-1]), abs(l[1:]-c[:-1])))
    return float(tr[-period:].mean()) if len(tr) >= period else float(tr.mean() if len(tr) else 1.0)


def _close(state: dict, pos: dict, exit_price: float, reason: str, date_str: str):
    risk_dollar = pos.get("risk_dollar", 0.0)
    pnl_r       = (exit_price - pos["entry"]) / (pos["entry"] - pos["sl"])
    pnl_dollar  = risk_dollar * pnl_r
    new_bal     = pos["balance_at_entry"] + pnl_dollar
    update_balance(STRATEGY, new_bal)
    log_trade(
        strategy=STRATEGY, trade_date=pos["entry_date"],
        entry_time=pos["entry_time"], exit_time=date_str,
        direction="long", instrument=INSTRUMENT,
        entry=pos["entry"], sl=pos["sl"], tp=pos["tp"],
        exit_price=exit_price, exit_type=reason,
        pnl_dollar=pnl_dollar, pnl_r=round(pnl_r, 4),
        risk_dollar=risk_dollar, balance_after=new_bal,
    )
    trade_closed(STRATEGY, "long", pos["entry"], exit_price, pnl_dollar, pnl_r, new_bal, reason)
    state.pop("position", None)
    log.info("%s closed %s exit=%.2f pnl=$%.2f (%.2fR)", STRATEGY, reason, exit_price, pnl_dollar, pnl_r)


def run():
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return
    if not (9 <= now_et.hour < 16):
        return
    if now_et.hour == 9 and now_et.minute < 35:
        return

    state = load_state(STRATEGY)
    df    = fetch_spy_1h(days=5)
    if df.empty or len(df) < 5:
        log.warning("Insufficient SPY 1H data")
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

        since = df[df.index > entry_ts]
        for idx, bar in since.iterrows():
            date_str = str(idx.date())
            if bar["low"] <= pos["sl"]:
                _close(state, pos, pos["sl"], "SL", date_str)
                state["day_state"] = {"date": today_str, "traded_today": True}
                save_state(STRATEGY, state)
                return
            if bar["high"] >= pos["tp"]:
                _close(state, pos, pos["tp"], "TP", date_str)
                state["day_state"] = {"date": today_str, "traded_today": True}
                save_state(STRATEGY, state)
                return
        save_state(STRATEGY, state)
        return  # hold

    # ── DAILY STATE MACHINE ───────────────────────────────────────────────────
    ds = state.get("day_state", {})
    if ds.get("date") != today_str:
        ds = {
            "date": today_str,
            "orb_high": None, "orb_low": None,
            "long_broken": False,
            "long_retest_ready": False,
            "traded_today": False,
        }

    if ds["traded_today"]:
        state["day_state"] = ds
        save_state(STRATEGY, state)
        return

    today_bars = df[df.index.date == today]
    if today_bars.empty:
        return

    # ORB bar = first 1H bar with hour==9 (9:30–10:30 ET)
    orb_candidates = today_bars[today_bars.index.hour == 9]
    if orb_candidates.empty:
        state["day_state"] = ds
        save_state(STRATEGY, state)
        return

    orb_bar  = orb_candidates.iloc[0]
    orb_high = float(orb_bar["high"])
    orb_low  = float(orb_bar["low"])
    ds["orb_high"] = orb_high
    ds["orb_low"]  = orb_low

    atr         = _atr(df, ATR_PERIOD)
    post_orb    = today_bars.iloc[1:]     # bars after ORB bar
    risk_dollar = balance * RISK_PCT[STRATEGY]

    for idx, bar in post_orb.iterrows():
        bar_h, bar_l, bar_c = float(bar["high"]), float(bar["low"]), float(bar["close"])

        # Step 1: break above ORB high
        if not ds["long_broken"] and bar_h > orb_high:
            ds["long_broken"] = True

        # Step 2: retest — low touches zone below ORB high
        if ds["long_broken"] and not ds["long_retest_ready"]:
            retest_floor = orb_high - RETEST_TOL * atr
            if bar_l <= orb_high and bar_l >= retest_floor:
                ds["long_retest_ready"] = True

        # Step 3: trigger — close back above ORB high after retest
        if ds["long_broken"] and ds["long_retest_ready"] and bar_c > orb_high:
            entry = bar_c
            sl    = orb_high - SL_MULT * atr
            risk  = entry - sl
            if risk <= 0:
                continue
            tp  = entry + RR * risk
            pos = {
                "direction": "long", "entry": entry, "sl": sl, "tp": tp,
                "entry_date": today_str, "entry_time": str(idx),
                "risk_dollar": risk_dollar, "balance_at_entry": balance,
            }
            state["position"]  = pos
            ds["traded_today"] = True
            state["day_state"] = ds
            save_state(STRATEGY, state)
            trade_opened(STRATEGY, "long", entry, sl, tp, risk_dollar)
            log.info("LONG open entry=%.2f sl=%.2f tp=%.2f orb_h=%.2f", entry, sl, tp, orb_high)
            return

    state["day_state"] = ds
    save_state(STRATEGY, state)
