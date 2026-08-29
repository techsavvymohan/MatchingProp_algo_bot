"""Export historical market data for January 2026 to July 2026 from MT5.

Saves:
- data/jan_jul_2026_xauusd.json
- data/jan_jul_2026_eurusd.json
"""
from datetime import datetime, timezone
import json
import logging
import os
import MetaTrader5 as mt5

from xauusd_bot.config import Config
from xauusd_bot.broker.mt5_connector import MT5Connector

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("export_jan_jul")


def export_symbol_jan_jul(symbol: str, output_path: str):
    cfg = Config.load()
    conn = MT5Connector(cfg.mt5)
    if not conn.connect():
        raise RuntimeError("Failed to connect to MT5")

    # Start from Dec 15, 2025 for indicator warm-up so Jan 1, 2026 is fully ready
    dt_from = datetime(2025, 12, 15, tzinfo=timezone.utc)
    dt_to = datetime(2026, 8, 1, tzinfo=timezone.utc)

    tf_map = {
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

    # Now create synthesized continuous M1 from M5 so M1 is available across the full 7-month range
    m5_data = result.get("M5")
    if m5_data:
        m1_times = []
        m1_opens = []
        m1_highs = []
        m1_lows = []
        m1_closes = []
        m1_vols = []
        m1_spreads = []

        m5_times = m5_data["time"]
        m5_o = m5_data["open"]
        m5_h = m5_data["high"]
        m5_l = m5_data["low"]
        m5_c = m5_data["close"]
        m5_v = m5_data["tick_volume"]
        m5_s = m5_data["spread"]

        for idx in range(len(m5_times)):
            t0 = datetime.fromisoformat(m5_times[idx])
            o = m5_o[idx]
            h = m5_h[idx]
            l = m5_l[idx]
            c = m5_c[idx]
            v = max(1, m5_v[idx] // 5)
            sp = m5_s[idx]

            sub_prices = [
                (o, o + (h - o) * 0.5, min(o, o + (h - o) * 0.5), o + (h - o) * 0.5),
                (o + (h - o) * 0.5, h, o + (h - o) * 0.4, h),
                (h, h, l, l + (h - l) * 0.3),
                (l + (h - l) * 0.3, max(l + (h - l) * 0.3, c), l, (l + c) * 0.5),
                ((l + c) * 0.5, max((l + c) * 0.5, c), min((l + c) * 0.5, c), c),
            ]
            for step, (so, sh, sl, sc) in enumerate(sub_prices):
                m1_t = datetime.fromtimestamp(t0.timestamp() + step * 60, tz=timezone.utc).isoformat()
                m1_times.append(m1_t)
                m1_opens.append(round(so, 5 if symbol == "EURUSD" else 2))
                m1_highs.append(round(sh, 5 if symbol == "EURUSD" else 2))
                m1_lows.append(round(sl, 5 if symbol == "EURUSD" else 2))
                m1_closes.append(round(sc, 5 if symbol == "EURUSD" else 2))
                m1_vols.append(v)
                m1_spreads.append(sp)

        result["M1"] = {
            "tf": "M1",
            "time": m1_times,
            "open": m1_opens,
            "high": m1_highs,
            "low": m1_lows,
            "close": m1_closes,
            "tick_volume": m1_vols,
            "spread": m1_spreads,
        }
        log.info("[%s M1] Generated %d continuous M1 bars for Jan-Jul 2026", symbol, len(m1_times))

    conn.disconnect()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f)
    log.info("Saved %s Jan-Jul 2026 dataset to %s", symbol, output_path)


if __name__ == "__main__":
    export_symbol_jan_jul("XAUUSD", "data/jan_jul_2026_xauusd.json")
    export_symbol_jan_jul("EURUSD", "data/jan_jul_2026_eurusd.json")
