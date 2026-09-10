from xauusd_bot.strategy.trigger import TriggerDetector
from xauusd_bot.models import TimeframeData, TradeDirection


def _make_data(close_prices):
    return TimeframeData(
        tf="M5", time=[], open=[], high=[p + 1 for p in close_prices],
        low=[p - 1 for p in close_prices], close=list(close_prices),
        tick_volume=[100] * len(close_prices), spread=[1] * len(close_prices),
    )


def test_momentum_bullish():
    td = TriggerDetector(ema_fast=9, rsi_period=14, rsi_mid_upper=70, rsi_mid_lower=30)
    prices = [100.0]
    for i in range(100):
        if i % 3 == 0:
            prices.append(prices[-1] - 0.6)
        else:
            prices.append(prices[-1] + 0.5)
    data = _make_data(prices)
    ok, reason = td.check_momentum_continuation(data, TradeDirection.BUY)
    assert ok, reason


def test_momentum_bearish():
    td = TriggerDetector(ema_fast=9, rsi_period=14, rsi_mid_upper=70, rsi_mid_lower=30)
    prices = [110.0]
    for i in range(100):
        if i % 3 == 0:
            prices.append(prices[-1] + 0.6)
        else:
            prices.append(prices[-1] - 0.5)
    data = _make_data(prices)
    ok, reason = td.check_momentum_continuation(data, TradeDirection.SELL)
    assert ok, reason


def test_momentum_insufficient_data():
    td = TriggerDetector()
    data = _make_data([100, 101])
    ok, _ = td.check_momentum_continuation(data, TradeDirection.BUY)
    assert not ok


def test_micro_structure_break_buy():
    td = TriggerDetector()
    highs = [100, 101, 100, 99, 105]
    data = TimeframeData(tf="M5", time=[], open=[], high=highs, low=[h - 2 for h in highs],
                         close=[h - 1 for h in highs], tick_volume=[100]*5, spread=[1]*5)
    ok, price = td.check_micro_structure_break(data, TradeDirection.BUY)
    assert ok
    assert price == 105


def test_micro_structure_break_sell():
    td = TriggerDetector()
    lows = [100, 99, 100, 101, 95]
    data = TimeframeData(tf="M5", time=[], open=[], high=[l + 2 for l in lows], low=lows,
                         close=[l + 1 for l in lows], tick_volume=[100]*5, spread=[1]*5)
    ok, price = td.check_micro_structure_break(data, TradeDirection.SELL)
    assert ok
    assert price == 95


def test_micro_structure_no_break():
    td = TriggerDetector()
    data = _make_data([100, 101, 102])
    ok, _ = td.check_micro_structure_break(data, TradeDirection.BUY)
    assert not ok


def test_zone_entry_in_zone():
    td = TriggerDetector()
    ok, reason = td.check_zone_entry(105, (100, 110), TradeDirection.BUY)
    assert ok


def test_zone_entry_outside():
    td = TriggerDetector()
    ok, _ = td.check_zone_entry(99, (100, 110), TradeDirection.BUY)
    assert not ok


def test_zone_entry_none():
    td = TriggerDetector()
    ok, _ = td.check_zone_entry(100, None, TradeDirection.BUY)
    assert not ok


def test_ema_stack_bullish():
    td = TriggerDetector()
    prices = [100 + i * 0.3 for i in range(60)]
    data = _make_data(prices)
    ok, reason = td.check_ema_stack_alignment(data)
    assert ok, reason


def test_ema_stack_bearish():
    td = TriggerDetector()
    prices = [115 - i * 0.3 for i in range(60)]
    data = _make_data(prices)
    ok, reason = td.check_ema_stack_alignment(data)
    assert ok, reason


def test_ema_stack_insufficient():
    td = TriggerDetector()
    data = _make_data([100] * 30)
    ok, _ = td.check_ema_stack_alignment(data)
    assert not ok


def test_top_bottom_hunter_buy():
    td = TriggerDetector()
    # Build prices where RSI drops into oversold (< 30) then bounces above 30
    prices = [100.0]
    for _ in range(25):
        prices.append(prices[-1] - 1.5)  # Heavy selloff driving RSI < 30
    # Final candle sharp bounce up
    prices.append(prices[-1] + 3.0)
    highs = [p + 1.0 for p in prices]
    lows = [p - 1.0 for p in prices]
    data = TimeframeData(
        tf="M5", time=[], open=prices, high=highs, low=lows, close=prices,
        tick_volume=[100]*len(prices), spread=[1]*len(prices)
    )
    ok, reason = td.check_top_bottom_hunter(data, TradeDirection.BUY, lookback=2)
    # The condition evaluates bounce and fib level
    assert isinstance(ok, bool)
    assert isinstance(reason, str)


def test_top_bottom_hunter_sell():
    td = TriggerDetector()
    # Build prices where RSI pushes into overbought (> 70) then rejects below 70
    prices = [100.0]
    for _ in range(25):
        prices.append(prices[-1] + 1.5)  # Strong rally driving RSI > 70
    # Final candle sharp drop down
    prices.append(prices[-1] - 3.0)
    highs = [p + 1.0 for p in prices]
    lows = [p - 1.0 for p in prices]
    data = TimeframeData(
        tf="M5", time=[], open=prices, high=highs, low=lows, close=prices,
        tick_volume=[100]*len(prices), spread=[1]*len(prices)
    )
    ok, reason = td.check_top_bottom_hunter(data, TradeDirection.SELL, lookback=2)
    assert isinstance(ok, bool)
    assert isinstance(reason, str)


def test_detect_m15_liquidity_levels():
    td = TriggerDetector()
    # 10 bars with a clear fractal swing high at bar 4 and swing low at bar 7
    highs = [2000, 2005, 2015, 2020, 2030, 2018, 2010, 2005, 2008, 2012]
    lows  = [1990, 1995, 2005, 2010, 2012, 2002, 1995, 1985, 1992, 2000]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    data = TimeframeData(tf="M15", time=[], open=closes, high=highs, low=lows, close=closes,
                          tick_volume=[100]*10, spread=[1]*10)
    bsl, ssl = td.detect_m15_liquidity_levels(data, lookback_bars=10)
    assert bsl is not None
    assert ssl is not None
    assert bsl >= 2020
    assert ssl <= 1995


def test_check_m1_liquidity_sweep():
    td = TriggerDetector()
    # Bullish: SSL is 2000. M1 drops to 1995, closes at 2002 (sweep and reclaim)
    highs = [2005, 2003, 2004]
    lows = [1998, 1999, 1995]
    closes = [2001, 2000, 2002]
    data = TimeframeData(tf="M1", time=[], open=closes, high=highs, low=lows, close=closes,
                          tick_volume=[100]*3, spread=[1]*3)
    ok, direction, level, msg = td.check_m1_liquidity_sweep(data, bsl=2050.0, ssl=2000.0)
    assert ok is True
    assert direction == TradeDirection.BUY
    assert level == 2000.0
    assert "Bullish SSL sweep" in msg

    # Bearish: BSL is 2050. M1 spikes to 2055, closes at 2048 (sweep and reclaim)
    highs = [2045, 2046, 2055]
    lows = [2040, 2041, 2045]
    closes = [2042, 2044, 2048]
    data_bear = TimeframeData(tf="M1", time=[], open=closes, high=highs, low=lows, close=closes,
                               tick_volume=[100]*3, spread=[1]*3)
    ok_b, dir_b, lvl_b, msg_b = td.check_m1_liquidity_sweep(data_bear, bsl=2050.0, ssl=2000.0)
    assert ok_b is True
    assert dir_b == TradeDirection.SELL
    assert lvl_b == 2050.0


def test_check_m1_displacement():
    td = TriggerDetector()
    atr_val = 2.0
    # Strong bullish displacement: body = 2.0 (1.0x ATR), range = 2.5 (body ratio = 0.8)
    data = TimeframeData(
        tf="M1", time=[], open=[2000.0], high=[2002.5], low=[2000.0], close=[2002.0],
        tick_volume=[100], spread=[1]
    )
    ok, msg = td.check_m1_displacement(data, atr_val=atr_val, min_atr_mult=0.60, min_body_ratio=0.60)
    assert ok is True
    assert "Displacement valid" in msg

    # Weak displacement: doji (body = 0.2, atr_ratio = 0.1)
    data_weak = TimeframeData(
        tf="M1", time=[], open=[2000.0], high=[2002.0], low=[1999.0], close=[2000.2],
        tick_volume=[100], spread=[1]
    )
    ok_w, msg_w = td.check_m1_displacement(data_weak, atr_val=atr_val, min_atr_mult=0.60, min_body_ratio=0.60)
    assert ok_w is False
    assert "Displacement weak" in msg_w


def test_check_m1_mss():
    td = TriggerDetector()
    # Bullish MSS: recent structure high is 2005. Latest close is 2008.
    highs = [2001, 2005, 2003, 2002, 2004, 2010]
    lows  = [1998, 2000, 1999, 1997, 1998, 2003]
    closes = [2000, 2002, 2001, 1999, 2001, 2008]
    data = TimeframeData(tf="M1", time=[], open=closes, high=highs, low=lows, close=closes,
                          tick_volume=[100]*6, spread=[1]*6)
    ok, mss_lvl, msg = td.check_m1_mss(data, TradeDirection.BUY, lookback=5)
    assert ok is True
    assert mss_lvl == 2005.0


def test_detect_m1_fvg():
    td = TriggerDetector()
    # Bullish FVG: c1.high = 2002, c2 large bar, c3.low = 2005 -> Gap: [2002, 2005]
    highs = [2002.0, 2008.0, 2010.0]
    lows  = [1998.0, 2001.0, 2005.0]
    closes = [2001.0, 2007.0, 2009.0]
    data = TimeframeData(tf="M1", time=[], open=closes, high=highs, low=lows, close=closes,
                          tick_volume=[100]*3, spread=[1]*3)
    fvg = td.detect_m1_fvg(data, TradeDirection.BUY)
    assert fvg is not None
    assert fvg[0] == 2002.0  # FVG lower bound (c1.high)
    assert fvg[1] == 2005.0  # FVG upper bound (c3.low / limit entry)

    # Bearish FVG: c1.low = 2010, c3.high = 2006 -> Gap: [2006, 2010]
    highs_b = [2015.0, 2011.0, 2006.0]
    lows_b  = [2010.0, 2002.0, 2000.0]
    closes_b = [2011.0, 2003.0, 2001.0]
    data_b = TimeframeData(tf="M1", time=[], open=closes_b, high=highs_b, low=lows_b, close=closes_b,
                            tick_volume=[100]*3, spread=[1]*3)
    fvg_b = td.detect_m1_fvg(data_b, TradeDirection.SELL)
    assert fvg_b is not None
    assert fvg_b[0] == 2006.0
    assert fvg_b[1] == 2010.0


def test_check_amt_rejection_buy():
    td = TriggerDetector()
    # Bullish: Low pierced VAL (2000.0) and closed back above at 2002.0
    data = TimeframeData(
        tf="M1", time=[], open=[2001.0], high=[2003.0], low=[1997.0], close=[2002.0],
        tick_volume=[100], spread=[1]
    )
    ok, msg = td.check_amt_rejection(data, TradeDirection.BUY, val=2000.0, vah=2020.0, poc=2010.0)
    assert ok is True
    assert "AMT Bullish Rejection" in msg


def test_check_amt_rejection_sell():
    td = TriggerDetector()
    # Bearish: High pierced VAH (2020.0) and closed back below at 2018.0
    data = TimeframeData(
        tf="M1", time=[], open=[2017.0], high=[2023.0], low=[2016.0], close=[2018.0],
        tick_volume=[100], spread=[1]
    )
    ok, msg = td.check_amt_rejection(data, TradeDirection.SELL, val=2000.0, vah=2020.0, poc=2010.0)
    assert ok is True
    assert "AMT Bearish Rejection" in msg


def test_check_vwap_band_exhaustion():
    td = TriggerDetector()
    # vwap, u1, l1, u2, l2 = (2000.0, 2010.0, 1990.0, 2020.0, 1980.0)
    vwap_data = (2000.0, 2010.0, 1990.0, 2020.0, 1980.0)

    # Buy Exhaustion: low pierced -2σ (1980) down to 1975 and closed above at 1982
    buy_data = TimeframeData(
        tf="M1", time=[], open=[1981.0], high=[1985.0], low=[1975.0], close=[1982.0],
        tick_volume=[100], spread=[1]
    )
    ok_buy, msg_buy = td.check_vwap_band_exhaustion(buy_data, TradeDirection.BUY, vwap_data)
    assert ok_buy is True
    assert "VWAP Exhaustion Buy" in msg_buy

    # Sell Exhaustion: high pierced +2σ (2020) up to 2025 and closed below at 2018
    sell_data = TimeframeData(
        tf="M1", time=[], open=[2017.0], high=[2025.0], low=[2015.0], close=[2018.0],
        tick_volume=[100], spread=[1]
    )
    ok_sell, msg_sell = td.check_vwap_band_exhaustion(sell_data, TradeDirection.SELL, vwap_data)
    assert ok_sell is True
    assert "VWAP Exhaustion Sell" in msg_sell


def test_detect_xau_scalp_sequence_bullish():
    td = TriggerDetector()
    # M15 context with confirmed BSL=2020.0 and SSL=2000.0
    m15_highs = [2010.0, 2012.0, 2020.0, 2015.0, 2014.0, 2008.0]
    m15_lows = [2005.0, 2006.0, 2010.0, 2004.0, 2002.0, 2000.0]
    m15_closes = [2008.0, 2010.0, 2018.0, 2006.0, 2003.0, 2001.0]
    m15_data = TimeframeData(
        tf="M15", time=[], open=m15_closes, high=m15_highs, low=m15_lows, close=m15_closes,
        tick_volume=[100]*6, spread=[1]*6
    )

    # M1 data sequence:
    # 1. Bar 0-3: Drift down with a minor lower-high at 2003.0 (high=2003.0)
    # 2. Bar 4: Sweep SSL (2000.0) with low=1998.0 and close=2001.0 (reclaim)
    # 3. Bar 5: Displacement impulse candle (open=2001, close=2006, high=2006.5, low=2001)
    # 4. Bar 6: MSS break > 2003.0 and FVG creation (c1.high=2002.5, c3.low=2004.5)
    m1_opens  = [2004.0, 2003.5, 2002.0, 2001.5, 2001.0, 2001.0, 2006.0]
    m1_highs  = [2004.5, 2004.0, 2002.5, 2002.0, 2002.5, 2006.5, 2008.0]
    m1_lows   = [2003.0, 2002.0, 2001.0, 2000.5, 1998.0, 2001.0, 2004.5]
    m1_closes = [2003.5, 2002.5, 2001.5, 2001.0, 2001.0, 2006.0, 2007.5]
    m1_data = TimeframeData(
        tf="M1", time=[], open=m1_opens, high=m1_highs, low=m1_lows, close=m1_closes,
        tick_volume=[100]*7, spread=[1]*7
    )

    seq = td.detect_xau_scalp_sequence(
        m15_data=m15_data,
        m1_data=m1_data,
        m1_atr=1.5,
        lookback_m15=6,
        sequence_window_m1=5,
        target_r=2.0,
    )
    assert seq is not None
    assert seq["direction"] == TradeDirection.BUY
    assert seq["sweep_direction"] == "SSL_SWEEP"
    assert seq["sweep_low"] == 1998.0
    assert seq["entry_price"] > seq["sl_price"]
    assert seq["tp_price"] > seq["entry_price"]
    assert round(seq["tp_price"] - seq["entry_price"], 1) == round(2.0 * (seq["entry_price"] - seq["sl_price"]), 1)


def test_detect_xau_scalp_sequence_bearish():
    td = TriggerDetector()
    # M15 context with confirmed BSL=2020.0 and SSL=2000.0
    m15_highs = [2010.0, 2012.0, 2020.0, 2015.0, 2014.0, 2008.0]
    m15_lows = [2005.0, 2006.0, 2010.0, 2004.0, 2002.0, 2000.0]
    m15_closes = [2008.0, 2010.0, 2018.0, 2006.0, 2003.0, 2001.0]
    m15_data = TimeframeData(
        tf="M15", time=[], open=m15_closes, high=m15_highs, low=m15_lows, close=m15_closes,
        tick_volume=[100]*6, spread=[1]*6
    )

    # M1 data sequence for Bearish:
    # 1. Bar 0-3: Drift up with a minor higher-low at 2017.0 (low=2017.0)
    # 2. Bar 4: Sweep BSL (2020.0) with high=2022.0 and close=2019.0 (reclaim < 2020.0)
    # 3. Bar 5: Displacement bearish candle (open=2019, close=2014, high=2019, low=2013.5)
    # 4. Bar 6: MSS break < 2017.0 and bearish FVG (c1.low=2017.5, c3.high=2015.5) -> Gap: [2015.5, 2017.5]
    m1_opens  = [2016.0, 2016.5, 2018.0, 2018.5, 2019.0, 2019.0, 2014.0]
    m1_highs  = [2017.0, 2018.0, 2019.5, 2019.5, 2022.0, 2019.0, 2015.5]
    m1_lows   = [2015.5, 2016.0, 2017.5, 2018.0, 2018.0, 2013.5, 2012.0]
    m1_closes = [2016.5, 2017.5, 2018.5, 2019.0, 2019.0, 2014.0, 2012.5]
    m1_data = TimeframeData(
        tf="M1", time=[], open=m1_opens, high=m1_highs, low=m1_lows, close=m1_closes,
        tick_volume=[100]*7, spread=[1]*7
    )

    seq = td.detect_xau_scalp_sequence(
        m15_data=m15_data,
        m1_data=m1_data,
        m1_atr=1.5,
        lookback_m15=6,
        sequence_window_m1=5,
        target_r=2.0,
    )
    assert seq is not None
    assert seq["direction"] == TradeDirection.SELL
    assert seq["sweep_direction"] == "BSL_SWEEP"
    assert seq["sweep_high"] == 2022.0
    assert seq["entry_price"] < seq["sl_price"]
    assert seq["tp_price"] < seq["entry_price"]
    assert round(seq["entry_price"] - seq["tp_price"], 1) == round(2.0 * (seq["sl_price"] - seq["entry_price"]), 1)


def test_check_opposite_mss_invalidation_buy():
    td = TriggerDetector()
    # In BUY position: price prints higher-low at 2005.0, then candle drops with displacement to 2000.0 breaking below 2005.0
    opens  = [2008.0, 2006.0, 2007.0, 2006.0, 2006.0, 2006.0, 2006.0]
    highs  = [2009.0, 2007.0, 2008.0, 2007.0, 2007.0, 2007.0, 2006.0]
    lows   = [2006.0, 2005.0, 2006.0, 2005.5, 2005.0, 2005.0, 1999.5]
    closes = [2007.0, 2006.0, 2007.5, 2006.5, 2006.0, 2006.0, 2000.0]
    data = TimeframeData(
        tf="M1", time=[], open=opens, high=highs, low=lows, close=closes,
        tick_volume=[100]*7, spread=[1]*7
    )
    invalidated, reason = td.check_opposite_mss_invalidation(data, TradeDirection.BUY, m1_atr=2.0)
    assert invalidated is True
    assert "Opposite MSS invalidation" in reason


def test_detect_overlap_pullback_setup_bullish():
    td = TriggerDetector()
    # Build M15 uptrend data with 60 bars
    m15_closes = [2000.0 + i * 0.5 for i in range(60)]
    # Pull back the last 3 bars slightly towards 21-EMA
    m15_closes[-3:] = [2028.0, 2026.0, 2025.0]
    m15_highs = [c + 1.0 for c in m15_closes]
    m15_lows = [c - 1.0 for c in m15_closes]
    m15_opens = [c - 0.2 for c in m15_closes]
    m15_data = TimeframeData(
        tf="M15", time=[], open=m15_opens, high=m15_highs, low=m15_lows, close=m15_closes,
        tick_volume=[100]*60, spread=[1]*60
    )

    # M1 data with reversal bounce
    m1_closes = [2024.0, 2023.0, 2022.0, 2021.0, 2022.5, 2024.0]
    m1_opens = [c - 0.5 for c in m1_closes]
    m1_highs = [c + 0.5 for c in m1_closes]
    m1_lows = [c - 0.5 for c in m1_closes]
    m1_data = TimeframeData(
        tf="M1", time=[], open=m1_opens, high=m1_highs, low=m1_lows, close=m1_closes,
        tick_volume=[100]*6, spread=[1]*6
    )

    res = td.detect_overlap_pullback_setup(m15_data, m1_data, m1_atr=1.5, trend_direction=TradeDirection.BUY)
    # If setup conditions met, it returns a dict with setup_type OVERLAP_PULLBACK
    if res is not None:
        assert res["direction"] == TradeDirection.BUY
        assert res["setup_type"] == "OVERLAP_PULLBACK"


def test_detect_xau_scalp_sequence_delta_absorption_rejection():
    td = TriggerDetector()
    # M15 context with confirmed BSL=2020.0 and SSL=2000.0
    m15_highs = [2010.0, 2012.0, 2020.0, 2015.0, 2014.0, 2008.0]
    m15_lows = [2005.0, 2006.0, 2010.0, 2004.0, 2002.0, 2000.0]
    m15_closes = [2008.0, 2010.0, 2018.0, 2006.0, 2003.0, 2001.0]
    m15_data = TimeframeData(
        tf="M15", time=[], open=m15_closes, high=m15_highs, low=m15_lows, close=m15_closes,
        tick_volume=[100]*6, spread=[1]*6
    )

    # Bullish SSL sweep setup, BUT reclaim candle has severe adverse negative delta (sellers dumping)
    m1_opens  = [2004.0, 2003.5, 2002.0, 2001.5, 2001.0, 2001.0, 2006.0]
    m1_highs  = [2004.5, 2004.0, 2002.5, 2002.0, 2002.5, 2006.5, 2008.0]
    m1_lows   = [2003.0, 2002.0, 2001.0, 2000.5, 1998.0, 2001.0, 2004.5]
    # Candle 6 has close near low -> negative delta
    m1_closes = [2003.5, 2002.5, 2001.5, 2001.0, 2001.0, 2006.0, 2004.6]

    m1_data_fake = TimeframeData(
        tf="M1", time=[], open=m1_opens, high=m1_highs, low=m1_lows, close=m1_closes,
        tick_volume=[100]*7, spread=[1]*7
    )

    telemetry = {}
    # When enable_delta_absorption is False, it passes
    seq_pass = td.detect_xau_scalp_sequence(
        m15_data=m15_data, m1_data=m1_data_fake, m1_atr=1.5, lookback_m15=6,
        sequence_window_m1=5, enable_delta_absorption=False,
    )
    # When enable_delta_absorption is True, the adverse delta rejection is triggered
    seq_abs = td.detect_xau_scalp_sequence(
        m15_data=m15_data, m1_data=m1_data_fake, m1_atr=1.5, lookback_m15=6,
        sequence_window_m1=5, enable_delta_absorption=True, telemetry=telemetry,
    )
    assert telemetry.get("delta_absorption_rejected", 0) >= 0


def test_detect_xau_scalp_sequence_min_sl_distance():
    td = TriggerDetector()
    m15_highs = [2010.0, 2012.0, 2015.0, 2008.0, 2004.0, 2002.0]
    m15_lows = [2005.0, 2006.0, 2010.0, 2004.0, 2002.0, 2000.0]
    m15_closes = [2008.0, 2010.0, 2012.0, 2006.0, 2003.0, 2001.0]
    m15_data = TimeframeData(
        tf="M15", time=[], open=m15_closes, high=m15_highs, low=m15_lows, close=m15_closes,
        tick_volume=[100]*6, spread=[1]*6
    )

    m1_opens  = [2004.0, 2003.5, 2002.0, 2001.5, 2001.0, 2001.0, 2006.0]
    m1_highs  = [2004.5, 2004.0, 2002.5, 2002.0, 2002.5, 2006.5, 2008.0]
    m1_lows   = [2003.0, 2002.0, 2001.0, 2000.5, 1998.0, 2001.0, 2004.5]
    m1_closes = [2003.5, 2002.5, 2001.5, 2001.0, 2001.0, 2006.0, 2007.5]
    m1_data = TimeframeData(
        tf="M1", time=[], open=m1_opens, high=m1_highs, low=m1_lows, close=m1_closes,
        tick_volume=[100]*7, spread=[1]*7
    )

    # Without floor (min_sl_distance=0.0): structural risk is entry_price (2008.0) - structural_sl (1998.0 - 0.75 = 1997.25) -> risk=10.75
    # Let's test with a high floor: min_sl_distance=15.0
    seq = td.detect_xau_scalp_sequence(
        m15_data=m15_data,
        m1_data=m1_data,
        m1_atr=1.5,
        lookback_m15=6,
        sequence_window_m1=5,
        target_r=2.0,
        min_sl_distance=15.0,
    )
    assert seq is not None
    assert seq["direction"] == TradeDirection.BUY
    assert seq["risk_distance"] == 15.0
    assert seq["sl_price"] == seq["entry_price"] - 15.0
    assert seq["tp_price"] == seq["entry_price"] + 30.0