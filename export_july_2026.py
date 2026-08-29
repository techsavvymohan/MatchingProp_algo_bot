"""Export 1-month market data for July 2026 directly from MT5 / TradingView.

Saves data/july_2026_xauusd.json and data/july_2026_eurusd.json.
"""
from datetime import datetime, timezone
import json
import logging
import os
import MetaTrader5 as mt5

from xauusd_bot.config import Config
from xauusd_bot.broker.mt5_connector import MT5Connector

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("export_july_2026")


def export_symbol_july_2026(symbol: str, output_path: str):
    cfg = Config.load()
    conn = MT5Connector(cfg.mt5)
    if not conn.connect():
        raise RuntimeError("Failed to connect to MT5")

    # Start from June 20 to allow indicator warm-up for July 1st
    dt_from = datetime(2026, 6, 20, tzinfo=timezone.utc)
    dt_to = datetime(2026, 8, 1, tzinfo=timezone.utc)

    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
    }

    result = {}
    for tf_name, mt5_tf in tf_map.items():
        rates = mt5.copy_rates_range(symbol, mt5_tf, dt_from, dt_to)
        if rates is None or len(rates) == 0:
            log.warning("No rates for %s %s", symbol, tf_name)
            continue

        times = [datetime.fromtimestamp(r[0], tz=timezone.utc).isoformat() for r in rates]
        opens = [float(r[1]) for r in rates]
        highs = [float(r[2]) for r in rates]
        lows = [float(r[3]) for r in rates]
        closes = [float(r[4]) for r in rates]
        vols = [int(r[5]) for r in rates]
        spreads = [int(r[7]) if len(r) > 7 else 20 for r in rates]

        result[tf_name] = {
            "tf": tf_name,
            "time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": vols,
            "spread": spreads,
        }
        log.info("[%s %s] Loaded %d bars (first: %s, last: %s)",
                 symbol, tf_name, len(rates), times[0], times[-1])

    conn.disconnect()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f)
    log.info("Saved %s July 2026 data to %s", symbol, output_path)


if __name__ == "__main__":
    export_symbol_july_2026("XAUUSD", "data/july_2026_xauusd.json")
    export_symbol_july_2026("EURUSD", "data/july_2026_eurusd.json")
