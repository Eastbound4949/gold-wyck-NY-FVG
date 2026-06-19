"""
worker.py — APScheduler for 4-strategy paper bot.
Gold:   daily 22:05 UTC (after COMEX close)
BTC:    every hour at :05 (after 1H bar closes)
SPY:    every 5 min, self-guards 9:35-16:00 ET Mon-Fri
FVG:    every 15 min, self-guards 07:00-18:00 ET Mon-Fri
"""
import sys
import logging
import pytz
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

from common import init_db, get_balance, bot_started, INITIAL_BALANCE, load_state, DB_PATH, STATE_DIR
import bot_gold
import bot_wyckoff_btc
import bot_ny_open
import bot_fvg

UTC = pytz.UTC
log = logging.getLogger("worker")

STRATEGIES = {
    "GOLD_TRADE_PRO": "Gold Trade Pro v1.31 | GC=F Daily | RR=4.0 | Risk 1%",
    "WYCKOFF_BTC":    "Wyckoff BTC          | BTC-USD 1H | RR=2.0 | Risk 2%",
    "NY_OPEN_BR":     "ig_06 NY Open B&R    | SPY 1H     | RR=4.0 | Risk 1%",
    "FVG_XAUUSD":     "TJR FVG Gap-Fill     | GC=F 15m   | RR=5.0 | Risk 1%",
}


def _run(name: str, fn):
    try:
        fn()
    except Exception as e:
        log.error("[%s] %s", name, e, exc_info=True)


def main():
    print("=" * 60)
    print(f"4-Strategy Paper Bot | {datetime.now(UTC):%Y-%m-%d %H:%M UTC}")
    print(f"  DB: {DB_PATH} | States: {STATE_DIR}")
    print("=" * 60)

    init_db()

    for key, desc in STRATEGIES.items():
        bal = get_balance(key)
        roi = (bal - INITIAL_BALANCE) / INITIAL_BALANCE * 100
        state = load_state(key)
        pos_info = ""
        if state.get("position"):
            p = state["position"]
            pos_info = f" | OPEN {p['direction'].upper()} @ {p['entry']} (sl={p['sl']})"
        print(f"  {key}: ${bal:,.2f} ({roi:+.1f}%){pos_info}")
        bot_started(key, bal)

    print("=" * 60)

    scheduler = BlockingScheduler(timezone=UTC)

    # Gold Trade Pro — once daily at 22:05 UTC
    scheduler.add_job(lambda: _run("GOLD",    bot_gold.run),
                      "cron", hour=22, minute=5, id="gold")

    # Wyckoff BTC — every hour at :05
    scheduler.add_job(lambda: _run("BTC",     bot_wyckoff_btc.run),
                      "cron", minute=5, id="btc")

    # NY Open B&R — every 5 min (self-guards session window)
    scheduler.add_job(lambda: _run("NY_OPEN", bot_ny_open.run),
                      "cron", minute="*/5", id="ny_open")

    # FVG XAUUSD — every 15 min (self-guards session window)
    scheduler.add_job(lambda: _run("FVG",     bot_fvg.run),
                      "cron", minute="*/15", id="fvg")

    log.info("Scheduler running. Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Stopped.")


if __name__ == "__main__":
    main()
