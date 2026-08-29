#!/usr/bin/env python3
"""Generate sample backtest data for XAUUSD Digger Bot."""
import json
import math
import random
from datetime import datetime, timedelta

random.seed(42)


def generate_ohlcv(
    days=90,
    start_price=2000.0,
    volatility=15.0,
    trend_strength=0.3,
    spread_mean=20.0,
):
    data = {
        "M1": {"time": [], "open": [], "high": [], "low": [],
               "close": [], "tick_volume": [], "spread": []},
        "M5": {"time": [], "open": [], "high": [], "low": [],
               "close": [], "tick_volume": [], "spread": []},
        "M15": {"time": [], "open": [], "high": [], "low": [],
                "close": [], "tick_volume": [], "spread": []},
        "H1": {"time": [], "open": [], "high": [], "low": [],
               "close": [], "tick_volume": [], "spread": []},
        "H4": {"time": [], "open": [], "high": [], "low": [],
               "close": [], "tick_volume": [], "spread": []},
    }

    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    total_m1_bars = days * 24 * 60
    price = start_price

    # Create daily volatility regimes
    daily_vol = []
    for d in range(days):
        base_vol = volatility * (0.5 + random.random())
        trend = math.sin(d * 0.1) * trend_strength * 5
        daily_vol.append((base_vol, trend))

    for i in range(total_m1_bars):
        day_idx = i // 1440
        vol, trend = daily_vol[min(day_idx, days - 1)]

        minute_of_day = i % 1440

        # Session-based volatility boost
        session_boost = 1.0
        if 480 <= minute_of_day <= 600:
            session_boost = 1.5
        if 780 <= minute_of_day <= 1020:
            session_boost = 2.0

        change = random.gauss(0, vol / 100) * session_boost + trend / 1440
        price += change
        price = max(price, start_price * 0.7)

        o = price
        h = o + abs(random.gauss(0, vol / 200)) * session_boost + 0.1
        l = o - abs(random.gauss(0, vol / 200)) * session_boost - 0.1
        c = o + random.gauss(0, vol / 300) * session_boost
        v = int(random.randint(100, 5000) * session_boost)
        sp = max(int(random.gauss(spread_mean, 5)), 5)

        ts = now + timedelta(minutes=i)
        ts_iso = ts.isoformat()

        data["M1"]["time"].append(ts_iso)
        data["M1"]["open"].append(round(o, 2))
        data["M1"]["high"].append(round(h, 2))
        data["M1"]["low"].append(round(l, 2))
        data["M1"]["close"].append(round(c, 2))
        data["M1"]["tick_volume"].append(v)
        data["M1"]["spread"].append(sp)

        if i % 5 == 0:
            agg = _aggregate_bars(data["M1"], 5)
            for k in data["M5"]:
                data["M5"][k].append(agg[k][-1] if agg[k] else None)

        if i % 15 == 0:
            agg = _aggregate_bars(data["M1"], 15)
            for k in data["M15"]:
                data["M15"][k].append(agg[k][-1] if agg[k] else None)

        if i % 60 == 0:
            agg = _aggregate_bars(data["M1"], 60)
            for k in data["H1"]:
                data["H1"][k].append(agg[k][-1] if agg[k] else None)

        if i % 240 == 0:
            agg = _aggregate_bars(data["M1"], 240)
            for k in data["H4"]:
                data["H4"][k].append(agg[k][-1] if agg[k] else None)

    # Clean None values
    for tf in data:
        for k in data[tf]:
            data[tf][k] = [v for v in data[tf][k] if v is not None]

    return data


def _aggregate_bars(m1_data, factor):
    result = {k: [] for k in m1_data}
    n = len(m1_data["close"])
    start = (n // factor) * factor
    chunk = slice(start - factor if start >= factor else 0, start if start > 0 else factor)
    if chunk.stop > n:
        return result
    result["time"] = [m1_data["time"][chunk.start]]
    result["open"] = [m1_data["open"][chunk.start]]
    result["high"] = [max(m1_data["high"][chunk])]
    result["low"] = [min(m1_data["low"][chunk])]
    result["close"] = [m1_data["close"][chunk.stop - 1]]
    result["tick_volume"] = [sum(m1_data["tick_volume"][chunk])]
    result["spread"] = [sum(m1_data["spread"][chunk]) // factor]
    return result


def convert_to_timeframe_objects(data):
    """Convert the JSON-friendly format to the format backtesting engine expects."""
    result = {}
    for tf in data:
        d = data[tf]
        from datetime import datetime
        result[tf] = {
            "tf": tf,
            "time": [datetime.fromisoformat(t) for t in d["time"]],
            "open": d["open"],
            "high": d["high"],
            "low": d["low"],
            "close": d["close"],
            "tick_volume": d["tick_volume"],
            "spread": d["spread"],
        }
    return result


if __name__ == "__main__":
    print("Generating 90 days of XAUUSD backtest data...")
    raw = generate_ohlcv(days=90)
    data = convert_to_timeframe_objects(raw)

    # Convert datetime objects to isoformat strings for JSON serialization
    json_data = {}
    for tf in data:
        d = data[tf]
        json_data[tf] = {
            "tf": d["tf"],
            "time": [t.isoformat() for t in d["time"]],
            "open": d["open"],
            "high": d["high"],
            "low": d["low"],
            "close": d["close"],
            "tick_volume": d["tick_volume"],
            "spread": d["spread"],
        }

    with open("data/backtest_data.json", "w") as f:
        json.dump(json_data, f)
    print(f"Generated: {len(json_data['M1']['close'])} M1 bars")
    print(f"           {len(json_data['M5']['close'])} M5 bars")
    print(f"           {len(json_data['M15']['close'])} M15 bars")
    print(f"           {len(json_data['H1']['close'])} H1 bars")
    print(f"           {len(json_data['H4']['close'])} H4 bars")
    print("Saved to data/backtest_data.json")
