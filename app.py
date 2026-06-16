"""
app.py — Streamlit account statement for 4-strategy paper bot.
Run: streamlit run app.py --server.port $PORT --server.address 0.0.0.0
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
import pytz

from common import (
    init_db, get_balance, get_trades_df, get_equity_log,
    calc_metrics, INITIAL_BALANCE,
)

STRATEGIES = {
    "GOLD_TRADE_PRO": {
        "label":     "Gold Trade Pro v1.31",
        "pair":      "GC=F (XAUUSD)",
        "tf":        "Daily",
        "rr":        4.0, "risk": "1%",
        "bt_cagr":   132.8, "bt_sharpe": 2.68, "bt_dd": 39.1,
        "color":     "#FFD700",
    },
    "WYCKOFF_BTC": {
        "label":     "Wyckoff Intra-Day BTC",
        "pair":      "BTC-USD",
        "tf":        "1H",
        "rr":        2.0, "risk": "2%",
        "bt_cagr":   77.6, "bt_sharpe": 3.129, "bt_dd": 19.26,
        "color":     "#F7931A",
    },
    "NY_OPEN_BR": {
        "label":     "ig_06 NY Open B&R",
        "pair":      "SPY",
        "tf":        "1H",
        "rr":        4.0, "risk": "1%",
        "bt_cagr":   61.32, "bt_sharpe": 1.497, "bt_dd": 33.2,
        "color":     "#00BFFF",
    },
    "FVG_XAUUSD": {
        "label":     "TJR FVG Gap-Fill 50pct",
        "pair":      "GC=F (XAUUSD)",
        "tf":        "15min",
        "rr":        5.0, "risk": "1%",
        "bt_cagr":   35.14, "bt_sharpe": 6.083, "bt_dd": 5.74,
        "color":     "#9B59B6",
    },
}


def _roi_badge(roi: float) -> str:
    if roi > 5:   return "🟢"
    if roi > 0:   return "🟡"
    if roi == 0:  return "⚪"
    return "🔴"


def _equity_chart(eq_df: pd.DataFrame, label: str, color: str) -> go.Figure:
    fig = go.Figure()
    if not eq_df.empty and "balance" in eq_df.columns:
        y = eq_df["balance"].values
        fig.add_trace(go.Scatter(
            x=list(range(len(y))), y=y,
            mode="lines", name="Equity",
            line=dict(color=color, width=2),
            fill="tozeroy", fillcolor=color.replace(")", ",0.08)").replace("rgb", "rgba"),
        ))
        fig.add_hline(y=INITIAL_BALANCE, line_dash="dash",
                      line_color="#888", annotation_text=f"Start ${INITIAL_BALANCE:,.0f}",
                      annotation_font_color="#888")
    fig.update_layout(
        title=dict(text=f"{label} — Equity", font=dict(size=13)),
        xaxis_title="Trade #", yaxis_title="$",
        height=280, margin=dict(l=40, r=10, t=36, b=30),
        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
        font=dict(color="#ccc"),
        xaxis=dict(gridcolor="#1e2130"), yaxis=dict(gridcolor="#1e2130"),
        showlegend=False,
    )
    return fig


def _pnl_chart(trades_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not trades_df.empty and "pnl_dollar" in trades_df.columns:
        vals   = trades_df["pnl_dollar"].values
        colors = ["#00d4aa" if v > 0 else "#ff4b4b" for v in vals]
        fig.add_trace(go.Bar(x=list(range(len(vals))), y=vals,
                             marker_color=colors, name="P&L"))
    fig.update_layout(
        title=dict(text="Per-Trade P&L ($)", font=dict(size=13)),
        xaxis_title="Trade #", yaxis_title="$",
        height=260, margin=dict(l=40, r=10, t=36, b=30),
        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
        font=dict(color="#ccc"),
        xaxis=dict(gridcolor="#1e2130"), yaxis=dict(gridcolor="#1e2130"),
        showlegend=False,
    )
    return fig


def main():
    st.set_page_config(page_title="4-Strategy Paper Bot", page_icon="📊", layout="wide")
    init_db()

    st.title("📊 4-Strategy Paper Trading Statement")
    st.caption(
        f"Paper trading · ${INITIAL_BALANCE:,.0f} starting capital per strategy · "
        f"Updated {datetime.now(pytz.UTC):%Y-%m-%d %H:%M UTC}"
    )

    # ── PORTFOLIO SUMMARY ─────────────────────────────────────────────────────
    rows       = []
    total_now  = 0.0
    total_start = INITIAL_BALANCE * len(STRATEGIES)

    for key, info in STRATEGIES.items():
        df_t  = get_trades_df(key)
        m     = calc_metrics(df_t)
        bal   = get_balance(key)
        roi   = (bal - INITIAL_BALANCE) / INITIAL_BALANCE * 100
        total_now += bal
        rows.append({
            "":          _roi_badge(roi),
            "Strategy":  info["label"],
            "Pair":      info["pair"],
            "TF":        info["tf"],
            "Balance":   f"${bal:,.2f}",
            "ROI%":      f"{roi:+.2f}%",
            "Trades":    m["trades"],
            "WR%":       f"{m['wr_pct']:.1f}%",
            "PF":        f"{m['profit_factor']:.2f}",
            "Expect(R)": f"{m['expectancy']:.2f}",
            "MaxDD%":    f"{m['max_dd_pct']:.1f}%",
        })

    total_roi = (total_now - total_start) / total_start * 100
    total_pnl = total_now - total_start

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio Balance", f"${total_now:,.2f}", f"{total_roi:+.2f}%")
    c2.metric("Total P&L",         f"${total_pnl:+,.2f}")
    c3.metric("Start Capital",     f"${total_start:,.0f}")
    c4.metric("Bots Running",      str(len(STRATEGIES)))

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── PER-STRATEGY TABS ─────────────────────────────────────────────────────
    st.subheader("Strategy Detail")
    tab_list = st.tabs([STRATEGIES[k]["label"] for k in STRATEGIES])

    for tab, key in zip(tab_list, STRATEGIES):
        info  = STRATEGIES[key]
        df_t  = get_trades_df(key)
        eq    = get_equity_log(key)
        m     = calc_metrics(df_t)
        bal   = get_balance(key)
        roi   = (bal - INITIAL_BALANCE) / INITIAL_BALANCE * 100

        with tab:
            # Live metrics
            mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
            mc1.metric("Balance",   f"${bal:,.2f}", f"{roi:+.2f}%")
            mc2.metric("Trades",    m["trades"])
            mc3.metric("Win Rate",  f"{m['wr_pct']:.1f}%")
            mc4.metric("PF",        f"{m['profit_factor']:.2f}")
            mc5.metric("MaxDD%",    f"{m['max_dd_pct']:.1f}%")
            mc6.metric("Expect(R)", f"{m['expectancy']:.2f}")

            # Backtest reference
            with st.expander("Backtest reference params"):
                bc1, bc2, bc3, bc4 = st.columns(4)
                bc1.metric("BT CAGR",   f"{info['bt_cagr']:.1f}%")
                bc2.metric("BT Sharpe", f"{info['bt_sharpe']:.3f}")
                bc3.metric("BT MaxDD",  f"{info['bt_dd']:.1f}%")
                bc4.metric("Paper Risk / RR", f"{info['risk']} / {info['rr']}R")

            # Charts
            ch1, ch2 = st.columns(2)
            with ch1:
                st.plotly_chart(_equity_chart(eq, info["label"], info["color"]),
                                use_container_width=True)
            with ch2:
                st.plotly_chart(_pnl_chart(df_t), use_container_width=True)

            # Trade log
            if not df_t.empty:
                cols = ["trade_date", "direction", "entry", "exit_price",
                        "exit_type", "pnl_r", "pnl_dollar", "balance_after"]
                show = df_t[[c for c in cols if c in df_t.columns]].copy().tail(100)
                if "pnl_dollar" in show.columns:
                    show["pnl_dollar"] = show["pnl_dollar"].map(lambda x: f"${x:+.2f}")
                if "pnl_r" in show.columns:
                    show["pnl_r"] = show["pnl_r"].map(lambda x: f"{x:+.2f}R")
                if "balance_after" in show.columns:
                    show["balance_after"] = show["balance_after"].map(lambda x: f"${x:,.2f}")
                st.dataframe(show, use_container_width=True, hide_index=True)
            else:
                st.info("No trades recorded yet.")

    st.markdown("---")
    if st.button("🔄 Refresh data"):
        st.rerun()


if __name__ == "__main__":
    main()
