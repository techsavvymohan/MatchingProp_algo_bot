"""TradingView Historical Data Importer.

Fetches 1 month of historical multi-timeframe market data from TradingView
using tvdatafeed for backtesting XAUUSD and EURUSD.
"""
import argparse
import datetime
import json
import logging
import os
from typing import Dict, Optional

try:
    from tvDatafeed import Interval, TvDatafeed
    TV_DATAFEED_AVAILABLE = True
except ImportError:
    TV_DATAFEED_AVAILABLE = False
    Interval = None
    TvDatafeed = None

log = logging.getLogger("xauusd_bot.data.tv_importer")


def fetch_symbol_data(symbol: str = "XAUUSD", exchange: Optional[str] = None) -> Dict[str, dict]:
    """Fetch multi-timeframe data from TradingView for symbol.

    Fetches M5, M15, H1, H4, and M1 bars, synthesizing earlier M1 bars from M5
    to produce a continuous 1-month dataset for the backtest engine.
    """
    if not TV_DATAFEED_AVAILABLE:
        raise RuntimeError("tvdatafeed library is not installed.")

    sym = symbol.upper()
    if exchange is None:
        exchange = "OANDA" if "XAU" in sym or "GOLD" in sym else "FX_IDC"

    log.info("Connecting to TradingView for %s on %s...", sym, exchange)
    tv = TvDatafeed()

    # Define bars requested to cover ~1 month (22 trading days)
    # M5: 5000 bars is ~17-20 trading days
    # M15: 2000 bars is ~25-30 trading days (1 full month)
    # H1: 800 bars is ~1.5 months
    # H4: 300 bars is ~3 months
    tf_configs = [
        ("H4", Interval.in_4_hour, 300),
        ("H1", Interval.in_1_hour, 800),
        ("M15", Interval.in_15_minute, 2000),
        ("M5", Interval.in_5_minute, 5000),
        ("M1", Interval.in_1_minute, 5000),
    ]

    raw_dfs = {}
    for tf_name, interval, n_bars in tf_configs:
        log.info("Fetching %s %s (%d bars)...", sym, tf_name, n_bars)
        try:
            df = tv.get_hist(sym, exchange, interval=interval, n_bars=n_bars)
            if df is not None and not df.empty:
                raw_dfs[tf_name] = df
                log.info("  -> Got %d %s bars from %s to %s", len(df), tf_name, df.index[0], df.index[-1])
            else:
                log.warning("  -> No data returned for %s", tf_name)
        except Exception as exc:
            log.warning("  -> Failed to fetch %s: %s", tf_name, exc)

    if not raw_dfs:
        raise ValueError(f"Failed to fetch any data for {sym} from TradingView.")

    # Format into standard dictionary structure
    data_json: Dict[str, dict] = {}
    for tf_name, df in raw_dfs.items():
        times = [ts.isoformat() for ts in df.index]
        data_json[tf_name] = {
            "tf": tf_name,
            "time": times,
            "open": [float(x) for x in df["open"]],
            "high": [float(x) for x in df["high"]],
            "low": [float(x) for x in df["low"]],
            "close": [float(x) for x in df["close"]],
            "tick_volume": [int(x) if not str(x).lower().startswith('nan') else 100 for x in df["volume"]],
            "spread": [20 if "XAU" in sym else 1] * len(df),
        }

    # Ensure continuous M1 bars across the full M5/M15 span
    # If M1 has fewer bars than M5, synthesize earlier M1 bars from M5
    if "M5" in raw_dfs:
        m5_df = raw_dfs["M5"]
        m1_df = raw_dfs.get("M1")

        if m1_df is None or m1_df.empty:
            m1_start = m5_df.index[-1] + datetime.timedelta(days=1)
        else:
            m1_start = m1_df.index[0]

        # Earlier M5 bars before M1 start
        earlier_m5 = m5_df[m5_df.index < m1_start]
        if not earlier_m5.empty:
            syn_times = []
            syn_opens = []
            syn_highs = []
            syn_lows = []
            syn_closes = []
            syn_vols = []

            for ts, row in earlier_m5.iterrows():
                o, h, l, c = row["open"], row["high"], row["low"], row["close"]
                v = int(row["volume"] / 5) if row["volume"] > 0 else 20
                for minute_offset in range(5):
                    sub_ts = ts + datetime.timedelta(minutes=minute_offset)
                    frac = (minute_offset + 1) / 5.0
                    sub_close = o + (c - o) * frac
                    sub_open = o + (c - o) * (minute_offset / 5.0)
                    sub_high = max(sub_open, sub_close, h if minute_offset == 2 else sub_close)
                    sub_low = min(sub_open, sub_close, l if minute_offset == 3 else sub_open)

                    syn_times.append(sub_ts.isoformat())
                    syn_opens.append(float(sub_open))
                    syn_highs.append(float(sub_high))
                    syn_lows.append(float(sub_low))
                    syn_closes.append(float(sub_close))
                    syn_vols.append(v)

            # Combine synthesized with real M1
            existing_m1 = data_json.get("M1", {"time": [], "open": [], "high": [], "low": [], "close": [], "tick_volume": [], "spread": []})
            combined_times = syn_times + existing_m1["time"]
            combined_opens = syn_opens + existing_m1["open"]
            combined_highs = syn_highs + existing_m1["high"]
            combined_lows = syn_lows + existing_m1["low"]
            combined_closes = syn_closes + existing_m1["close"]
            combined_vols = syn_vols + existing_m1["tick_volume"]
            combined_spread = [20 if "XAU" in sym else 1] * len(combined_times)

            data_json["M1"] = {
                "tf": "M1",
                "time": combined_times,
                "open": combined_opens,
                "high": combined_highs,
                "low": combined_lows,
                "close": combined_closes,
                "tick_volume": combined_vols,
                "spread": combined_spread,
            }
            log.info("Synthesized %d continuous M1 bars covering full M5 month history (total M1: %d)",
                     len(syn_times), len(combined_times))

    return data_json


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(description="TradingView Historical Data Importer")
    parser.add_argument("--symbol", type=str, default="XAUUSD", help="Symbol to import (e.g. XAUUSD, EURUSD)")
    parser.add_argument("--exchange", type=str, default=None, help="TradingView Exchange (e.g. OANDA, FX_IDC)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    output_file = args.output or f"data/tv_{symbol.lower()}_1m.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    data = fetch_symbol_data(symbol, args.exchange)
    with open(output_file, "w") as f:
        json.dump(data, f)
    log.info("Saved %s TradingView historical data to %s", symbol, output_file)
    for tf, d in data.items():
        log.info("  %s: %d bars (first=%s, last=%s)", tf, len(d["close"]), d["time"][0], d["time"][-1])


if __name__ == "__main__":
    main()
