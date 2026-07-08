"""Direct Yahoo v8 chart API fetch.

Bypasses yfinance entirely: from Railway containers, yfinance's timezone
lookup hits fc.yahoo.com which fails TLS ("no alternative certificate subject
name matches target hostname") and index tickers like ^GSPC come back
"possibly delisted". The v8 chart endpoint (query1) is the same host SPY
price data was already flowing through, so it is reachable in-container.
"""
import time

import pandas as pd
import requests

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch(symbol: str, interval: str, days: int, tz: str = "America/New_York") -> pd.DataFrame:
    """OHLCV DataFrame (cols Open/High/Low/Close/Volume, tz-aware index).

    interval: 1m/5m/1h/1d (Yahoo granularities). days: lookback window,
    sent as period1/period2 epochs (Yahoo range= only takes fixed presets).
    Empty DataFrame on no data; raises on persistent transport errors.
    """
    now = int(time.time())
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + requests.utils.quote(symbol)
    params = {"interval": interval, "period1": now - days * 86400, "period2": now,
              "includePrePost": "false"}
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=_UA, timeout=20)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts = res.get("timestamp")
            if not ts:
                return pd.DataFrame()
            q = res["indicators"]["quote"][0]
            df = pd.DataFrame(
                {"Open": q["open"], "High": q["high"], "Low": q["low"],
                 "Close": q["close"], "Volume": q.get("volume")},
                index=pd.to_datetime(ts, unit="s", utc=True))
            df.index = df.index.tz_convert(tz)
            return df.dropna(subset=["Open", "High", "Low", "Close"])
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"yahoo_chart fetch failed for {symbol} {interval}: {last_err}")
