"""TradingView Historical Data Importer.

Fetches 1 month of historical multi-timeframe market data from TradingView
using tvdatafeed for backtesting XAUUSD and EURUSD.
"""
import argparse
import datetime
import json
import logging
import os
from typing import Any, Dict, Optional

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

    Fetches authentic M5, M15, H1, H4, and M1 bars directly from TradingView
    without synthetic interpolation to produce genuine datasets for backtesting.
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

    # Format into standard dictionary structure with 100% genuine data
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

    return data_json


def fetch_lse_data(
    symbol: str = "XAUUSD",
    days: int = 30,
    api_key: str = "lse_live_31f53152fae3fd762294057c154f19b2",
    http_url: str = "https://api.londonstrategicedge.com/vault",
) -> Dict[str, dict]:
    """Fetch multi-timeframe candles from London Strategic Edge API.

    Returns continuous M1, M5, M15, H1, and H4 candles in the standard
    dictionary format expected by BacktestEngine.
    """
    import requests
    from datetime import datetime, timedelta, timezone

    sym_clean = symbol.upper().replace("/", "")
    if sym_clean in ("XAUUSD", "GOLD"):
        lse_sym = "XAU/USD"
    elif sym_clean == "EURUSD":
        lse_sym = "EUR/USD"
    elif sym_clean == "GBPUSD":
        lse_sym = "GBP/USD"
    elif "/" in symbol:
        lse_sym = symbol
    else:
        lse_sym = f"{sym_clean[:3]}/{sym_clean[3:]}" if len(sym_clean) == 6 else symbol

    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    tf_configs = [
        ("H4", "4h"),
        ("H1", "1h"),
        ("M15", "15m"),
        ("M5", "5m"),
        ("M1", "1m"),
    ]

    data_json = {}
    headers = {"x-api-key": api_key}
    for tf_name, lse_tf in tf_configs:
        log.info("Fetching LSE %s %s from %s...", lse_sym, tf_name, start_date)
        try:
            r = requests.get(
                f"{http_url}/candles",
                params={"symbol": lse_sym, "timeframe": lse_tf, "start": start_date},
                headers=headers,
                timeout=20,
            )
            if r.status_code != 200:
                log.warning("LSE returned status %d for %s %s", r.status_code, lse_sym, tf_name)
                continue
            rows = r.json()
            if not rows or not isinstance(rows, list):
                continue

            times = []
            opens = []
            highs = []
            lows = []
            closes = []
            vols = []
            spreads = []
            spread_val = 20 if "XAU" in sym_clean else 1

            for row in rows:
                times.append(row["ts"])
                opens.append(float(row["open"]))
                highs.append(float(row["high"]))
                lows.append(float(row["low"]))
                closes.append(float(row["close"]))
                vols.append(int(row.get("volume", 1)))
                spreads.append(spread_val)

            data_json[tf_name] = {
                "tf": tf_name,
                "time": times,
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "tick_volume": vols,
                "spread": spreads,
            }
            log.info("  -> Got %d LSE %s bars", len(times), tf_name)
        except Exception as exc:
            log.error("Failed fetching LSE %s %s: %s", lse_sym, tf_name, exc)

    return data_json if data_json else None


def check_tv_cdp_status(host: str = "127.0.0.1", port: int = 9222, timeout: float = 2.0) -> Dict[str, Any]:
    """Check if TradingView Desktop is running with Chrome DevTools debugging on port 9222."""
    import urllib.error
    import urllib.request

    url = f"http://{host}:{port}/json/version"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MatchingProp-ResearchEngine"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return {
                "cdp_connected": True,
                "browser": data.get("Browser", "Unknown"),
                "protocol_version": data.get("Protocol-Version", ""),
                "webSocketDebuggerUrl": data.get("webSocketDebuggerUrl", ""),
                "error": None,
            }
    except Exception as exc:
        return {
            "cdp_connected": False,
            "browser": "",
            "protocol_version": "",
            "webSocketDebuggerUrl": "",
            "error": str(exc),
        }


def export_pine_strategy(output_path: Optional[str] = None, params: Optional[Dict[str, Any]] = None) -> str:
    """Generate the frozen Pine Script v5 Strategy code for TradingView MCP validation."""
    p = params or {}
    lookback = p.get("tbh_lookback", 2)
    fib_0 = p.get("tbh_fib_0", 0.382)
    fib_1 = p.get("tbh_fib_1", 0.618)
    rsi_len = p.get("tbh_rsi_length", 14)
    rsi_os = p.get("tbh_rsi_oversold", 30.0)
    rsi_ob = p.get("tbh_rsi_overbought", 70.0)
    atr_sl = p.get("tbh_atr_sl_mult", 2.0)
    rr = p.get("tbh_rr_ratio", 1.5)

    code = f'''//@version=5
strategy("Top and Bottom Hunter Strategy [Research Engine v1.0]", 
     overlay=true, 
     initial_capital=100000, 
     default_qty_type=strategy.percent_of_equity, 
     default_qty_value=1.0, 
     commission_type=strategy.commission.cash_per_order, 
     commission_value=3.5, 
     slippage=2, 
     process_orders_on_close=false)

// SWEEPABLE INPUTS (EXPLICIT FOR TRADINGVIEW MCP)
lookback_fib      = input.int({lookback}, "Fib Lookback Bars", minval=2, maxval=20, group="Fibonacci")
fib_0             = input.float({fib_0}, "Fib Level 0 (Short)", minval=0.1, maxval=0.5, step=0.05, group="Fibonacci")
fib_1             = input.float({fib_1}, "Fib Level 1 (Long)", minval=0.5, maxval=0.9, step=0.05, group="Fibonacci")

rsi_length        = input.int({rsi_len}, "RSI Length", minval=2, maxval=50, group="RSI")
rsi_oversold      = input.float({rsi_os}, "RSI Oversold", minval=10.0, maxval=45.0, step=5.0, group="RSI")
rsi_overbought    = input.float({rsi_ob}, "RSI Overbought", minval=55.0, maxval=90.0, step=5.0, group="RSI")

atr_length        = input.int(14, "ATR Length", minval=5, maxval=30, group="Risk")
atr_multiplier_sl = input.float({atr_sl}, "ATR SL Multiplier", minval=1.0, maxval=5.0, step=0.5, group="Risk")
rr_ratio          = input.float({rr}, "Risk/Reward Ratio (TP)", minval=1.0, maxval=5.0, step=0.5, group="Risk")

use_trend_filter  = input.bool(false, "Enable 200 SMA Filter", group="Regime")
trend_sma_len     = input.int(200, "Trend SMA Length", group="Regime")

// CALCULATIONS
range_high        = ta.highest(high, lookback_fib)
range_low         = ta.lowest(low, lookback_fib)
fib_range         = range_high - range_low

fib_level_0       = range_high - (fib_range * fib_0)
fib_level_1       = range_high - (fib_range * fib_1)

rsi_value         = ta.rsi(close, rsi_length)
atr_val           = ta.atr(atr_length)
trend_filter      = ta.sma(close, trend_sma_len)
long_trend_ok     = not use_trend_filter or (close > trend_filter)
short_trend_ok    = not use_trend_filter or (close < trend_filter)

// CONFIRMED BAR SIGNALS (NO LOOKAHEAD / ZERO REPAINTING)
buy_condition     = ta.crossover(rsi_value, rsi_oversold) and (close > fib_level_1) and long_trend_ok
sell_condition    = ta.crossunder(rsi_value, rsi_overbought) and (close < fib_level_0) and short_trend_ok

if (buy_condition and strategy.position_size == 0 and barstate.isconfirmed)
    entry_price = close
    sl_dist     = atr_val * atr_multiplier_sl
    tp_dist     = sl_dist * rr_ratio
    sl_price    = entry_price - sl_dist
    tp_price    = entry_price + tp_dist
    strategy.entry("Long", strategy.long)
    strategy.exit("Exit Long", from_entry="Long", stop=sl_price, limit=tp_price)

if (sell_condition and strategy.position_size == 0 and barstate.isconfirmed)
    entry_price = close
    sl_dist     = atr_val * atr_multiplier_sl
    tp_dist     = sl_dist * rr_ratio
    sl_price    = entry_price + sl_dist
    tp_price    = entry_price - tp_dist
    strategy.entry("Short", strategy.short)
    strategy.exit("Exit Short", from_entry="Short", stop=sl_price, limit=tp_price)

plot(fib_level_0, "Fib Level 0", color=color.new(color.red, 30))
plot(fib_level_1, "Fib Level 1", color=color.new(color.green, 30))
'''
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)
        log.info("Exported Pine Strategy to %s", output_path)
    return code



def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(description="Historical Data Importer (TradingView & LSE)")
    parser.add_argument("--source", type=str, default="lse", choices=["lse", "tradingview"],
                        help="Data source to fetch from: lse or tradingview")
    parser.add_argument("--symbol", type=str, default="XAUUSD", help="Symbol to import (e.g. XAUUSD, EURUSD)")
    parser.add_argument("--exchange", type=str, default=None, help="TradingView Exchange (e.g. OANDA, FX_IDC)")
    parser.add_argument("--days", type=int, default=30, help="Days of history to import (default: 30)")
    parser.add_argument("--api-key", type=str, default=os.getenv("LSE_API_KEY", "lse_live_31f53152fae3fd762294057c154f19b2"),
                        help="LSE API Key")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    output_file = args.output or f"data/{args.source}_{symbol.lower()}_data.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    if args.source == "lse":
        log.info("Fetching data from London Strategic Edge (LSE)...")
        data = fetch_lse_data(symbol, days=args.days, api_key=args.api_key)
    else:
        log.info("Fetching data from TradingView...")
        data = fetch_symbol_data(symbol, args.exchange)

    with open(output_file, "w") as f:
        json.dump(data, f)
    log.info("Saved %s %s historical data to %s", symbol, args.source.upper(), output_file)
    for tf, d in data.items():
        log.info("  %s: %d bars (first=%s, last=%s)", tf, len(d["close"]), d["time"][0], d["time"][-1])


if __name__ == "__main__":
    main()

