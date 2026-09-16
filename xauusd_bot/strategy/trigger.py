import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..indicators.moving_averages import ema, ema_series
from ..indicators.rsi import rsi
from ..models import Bias, TimeframeData, TradeDirection

log = logging.getLogger("xauusd_bot.strategy.trigger")


class TriggerDetector:
    def __init__(self, ema_fast: int = 9, rsi_period: int = 14,
                 rsi_mid_upper: float = 60.0, rsi_mid_lower: float = 40.0):
        self.ema_fast = ema_fast
        self.rsi_period = rsi_period
        self.rsi_mid_upper = rsi_mid_upper
        self.rsi_mid_lower = rsi_mid_lower

    def check_momentum_continuation(self, data: TimeframeData, direction: TradeDirection) -> Tuple[bool, str]:
        c = data.close
        if len(c) < self.ema_fast + 5:
            return False, "insufficient data"
        e = ema(c, self.ema_fast)
        r = rsi(c, self.rsi_period)
        if e is None or r is None:
            return False, "indicator calc failed"
        if direction == TradeDirection.BUY:
            if c[-1] > e and self.rsi_mid_lower <= r <= self.rsi_mid_upper:
                return True, "bullish momentum + mid-range RSI"
            return False, f"buy fail: price={c[-1]:.2f} ema={e:.2f} rsi={r:.1f}"
        else:
            if c[-1] < e and self.rsi_mid_lower <= r <= self.rsi_mid_upper:
                return True, "bearish momentum + mid-range RSI"
            return False, f"sell fail: price={c[-1]:.2f} ema={e:.2f} rsi={r:.1f}"

    def check_micro_structure_break(self, data: TimeframeData, direction: TradeDirection,
                                    lookback: int = 3) -> Tuple[bool, float]:
        c = data.high if direction == TradeDirection.BUY else data.low
        if len(c) < lookback + 2:
            return False, 0.0
        recent = c[-(lookback + 1):-1]
        current = c[-1]
        if direction == TradeDirection.BUY and current > max(recent):
            return True, current
        if direction == TradeDirection.SELL and current < min(recent):
            return True, current
        return False, current

    def check_zone_entry(self, price: float, zone: Optional[Tuple[float, float]],
                         direction: TradeDirection) -> Tuple[bool, str]:
        if zone is None:
            return False, "no zone defined"
        zone_low, zone_high = zone
        if direction == TradeDirection.BUY:
            if zone_low <= price <= zone_high:
                return True, f"price {price:.2f} in buy zone [{zone_low:.2f}, {zone_high:.2f}]"
            return False, f"price {price:.2f} outside buy zone"
        else:
            if zone_low <= price <= zone_high:
                return True, f"price {price:.2f} in sell zone [{zone_low:.2f}, {zone_high:.2f}]"
            return False, f"price {price:.2f} outside sell zone"

    def check_ema_stack_alignment(self, data: TimeframeData,
                                  fast: int = 9, medium: int = 21, slow: int = 50) -> Tuple[bool, str]:
        c = data.close
        if len(c) < slow + 5:
            return False, "insufficient data"
        e_fast = ema(c, fast)
        e_med = ema(c, medium)
        e_slow = ema(c, slow)
        if any(x is None for x in [e_fast, e_med, e_slow]):
            return False, "ema calc failed"
        if e_fast > e_med > e_slow and c[-1] > e_fast:
            return True, "bullish stack"
        if e_fast < e_med < e_slow and c[-1] < e_fast:
            return True, "bearish stack"
        return False, "no stack alignment"

    def check_top_bottom_hunter(
        self,
        data: TimeframeData,
        direction: TradeDirection,
        lookback: int = 2,
        fib_0: float = 0.382,
        fib_1: float = 0.618,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
    ) -> Tuple[bool, str]:
        """Top and Bottom Hunter trigger adapted from Pine Script.

        Calculates Fibonacci retracement levels from the highest high and lowest low
        over `lookback` bars and checks for RSI(14) boundary crossovers:
        - BUY (Bottom Hunter): ta.crossover(rsi, 30) AND close > Fib_Level_1 (0.618)
        - SELL (Top Hunter): ta.crossunder(rsi, 70) AND close < Fib_Level_0 (0.382)
        """
        c = data.close
        h = data.high
        l = data.low
        if len(c) < self.rsi_period + 2 or len(h) < lookback or len(l) < lookback:
            return False, "insufficient data"

        prev_rsi = rsi(c[:-1], self.rsi_period)
        curr_rsi = rsi(c, self.rsi_period)
        if prev_rsi is None or curr_rsi is None:
            return False, "rsi calc failed"

        recent_high = max(h[-lookback:])
        recent_low = min(l[-lookback:])
        fib_range = recent_high - recent_low
        fib_level_0 = recent_high - (fib_range * fib_0)
        fib_level_1 = recent_high - (fib_range * fib_1)
        curr_close = c[-1]

        if direction == TradeDirection.BUY:
            rsi_cross = prev_rsi <= rsi_oversold and curr_rsi > rsi_oversold
            if rsi_cross and curr_close > fib_level_1:
                return True, f"Bottom Hunter Buy: RSI crossover {prev_rsi:.1f}->{curr_rsi:.1f} & close {curr_close:.2f} > Fib61.8 {fib_level_1:.2f}"
            return False, f"Bottom Hunter Fail: rsi_cross={rsi_cross}, close={curr_close:.2f}, fib61.8={fib_level_1:.2f}"
        else:
            rsi_cross = prev_rsi >= rsi_overbought and curr_rsi < rsi_overbought
            if rsi_cross and curr_close < fib_level_0:
                return True, f"Top Hunter Sell: RSI crossunder {prev_rsi:.1f}->{curr_rsi:.1f} & close {curr_close:.2f} < Fib38.2 {fib_level_0:.2f}"
            return False, f"Top Hunter Fail: rsi_cross={rsi_cross}, close={curr_close:.2f}, fib38.2={fib_level_0:.2f}"

    def detect_m15_liquidity_levels(
        self,
        data: TimeframeData,
        lookback_bars: int = 20,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Detect confirmed Buy-Side (BSL) and Sell-Side (SSL) liquidity levels on M15.

        Uses a causal 5-bar fractal swing definition:
        - Swing High (BSL): high[j] > high[j-1], high[j-2], high[j+1], high[j+2]
        - Swing Low (SSL):  low[j]  < low[j-1],  low[j-2],  low[j+1],  low[j+2]
        Confirmed strictly at bar j+2 (zero look-ahead bias).
        """
        h = data.high
        l = data.low
        n = len(h)
        if n < 5:
            return None, None

        bsl = None
        ssl = None
        max_search = min(lookback_bars, n - 3)

        # Scan backwards starting from the latest confirmed fractal center (n - 3)
        for offset in range(max_search):
            j = n - 3 - offset
            if j < 2:
                break
            if bsl is None:
                if h[j] > h[j - 1] and h[j] > h[j - 2] and h[j] > h[j + 1] and h[j] > h[j + 2]:
                    bsl = h[j]
            if ssl is None:
                if l[j] < l[j - 1] and l[j] < l[j - 2] and l[j] < l[j + 1] and l[j] < l[j + 2]:
                    ssl = l[j]
            if bsl is not None and ssl is not None:
                break

        # Fallback to recent swing extremes if strict fractal not found in narrow window
        if bsl is None and n >= 5:
            bsl = max(h[-lookback_bars:])
        if ssl is None and n >= 5:
            ssl = min(l[-lookback_bars:])

        return bsl, ssl

    def detect_asian_range_levels(
        self,
        data: TimeframeData,
        current_time: Optional[datetime] = None,
        asian_start_hour: int = 0,
        asian_end_hour: int = 7,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Detect Asian Session High (BSL) and Low (SSL) for London expansion.

        The Asian session runs from asian_start_hour to asian_end_hour UTC.
        Only valid once the Asian session has completed for the current day.
        """
        if not data.time or not data.high or not data.low:
            return None, None

        ref_time = current_time if current_time is not None else data.time[-1]
        ref_date = ref_time.date() if hasattr(ref_time, "date") else None
        if ref_date is None:
            return None, None

        # Require that Asian session has completed (hour >= asian_end_hour)
        if hasattr(ref_time, "hour") and ref_time.hour < asian_end_hour:
            return None, None

        asian_highs = []
        asian_lows = []
        for t, h, l in zip(data.time, data.high, data.low):
            if hasattr(t, "date") and t.date() == ref_date:
                if hasattr(t, "hour") and asian_start_hour <= t.hour < asian_end_hour:
                    asian_highs.append(h)
                    asian_lows.append(l)

        if not asian_highs or not asian_lows:
            return None, None

        return max(asian_highs), min(asian_lows)

    def check_m1_liquidity_sweep(
        self,
        data: TimeframeData,
        bsl: Optional[float],
        ssl: Optional[float],
    ) -> Tuple[bool, Optional[TradeDirection], float, str]:
        """Check if M1 candle swept and reclaimed an M15 liquidity level.

        - Long: low < SSL and close > SSL (Sell-Side Liquidity swept & reclaimed)
        - Short: high > BSL and close < BSL (Buy-Side Liquidity swept & reclaimed)
        """
        c = data.close
        h = data.high
        l = data.low
        if not c or not h or not l:
            return False, None, 0.0, "insufficient data"

        curr_c = c[-1]
        curr_h = h[-1]
        curr_l = l[-1]

        if ssl is not None and curr_l < ssl and curr_c > ssl:
            return True, TradeDirection.BUY, ssl, f"Bullish SSL sweep: low {curr_l:.2f} < {ssl:.2f}, close {curr_c:.2f} > {ssl:.2f}"

        if bsl is not None and curr_h > bsl and curr_c < bsl:
            return True, TradeDirection.SELL, bsl, f"Bearish BSL sweep: high {curr_h:.2f} > {bsl:.2f}, close {curr_c:.2f} < {bsl:.2f}"

        return False, None, 0.0, "no sweep detected"

    def check_m1_displacement(
        self,
        data: TimeframeData,
        atr_val: float,
        min_atr_mult: float = 0.60,
        min_body_ratio: float = 0.60,
    ) -> Tuple[bool, str]:
        """Check if M1 candle exhibits strong directional displacement.

        - body_size >= min_atr_mult * ATR_M1(14)
        - body_size / total_candle_range >= min_body_ratio
        """
        o = data.open
        c = data.close
        h = data.high
        l = data.low
        if not o or not c or not h or not l or atr_val <= 0:
            return False, "insufficient data or zero ATR"

        body = abs(c[-1] - o[-1])
        candle_range = h[-1] - l[-1]
        if candle_range <= 0:
            return False, "zero candle range"

        body_ratio = body / candle_range
        atr_ratio = body / atr_val

        if atr_ratio >= min_atr_mult and body_ratio >= min_body_ratio:
            return True, f"Displacement valid: body={body:.2f} ({atr_ratio:.2f}x ATR, body_ratio={body_ratio:.2f})"

        return False, f"Displacement weak: atr_ratio={atr_ratio:.2f} (min {min_atr_mult}), body_ratio={body_ratio:.2f} (min {min_body_ratio})"

    def check_m1_mss(
        self,
        data: TimeframeData,
        direction: TradeDirection,
        lookback: int = 5,
    ) -> Tuple[bool, float, str]:
        """Check causal M1 Market Structure Shift (MSS).

        - Long: close breaks above most recent confirmed lower-high prior to the sweep/reversal.
        - Short: close breaks below most recent confirmed higher-low prior to the sweep/reversal.
        """
        c = data.close
        h = data.high
        l = data.low
        if len(c) < lookback + 1:
            return False, 0.0, "insufficient data"

        curr = c[-1]

        if direction == TradeDirection.BUY:
            ref_highs = h[-(lookback + 1):-1]
            pivot = None
            for offset in range(len(ref_highs) - 2, 0, -1):
                idx = len(h) - 1 - (len(ref_highs) - 1 - offset)
                if 1 <= idx < len(h) - 1:
                    if h[idx] >= h[idx - 1] and h[idx] >= h[idx + 1]:
                        pivot = h[idx]
                        break
            mss_level = pivot if pivot is not None else max(ref_highs)
            if curr > mss_level:
                return True, mss_level, f"Bullish MSS: close {curr:.2f} broke structure high {mss_level:.2f}"
            return False, mss_level, f"Bullish MSS pending: close {curr:.2f} <= {mss_level:.2f}"
        else:
            ref_lows = l[-(lookback + 1):-1]
            pivot = None
            for offset in range(len(ref_lows) - 2, 0, -1):
                idx = len(l) - 1 - (len(ref_lows) - 1 - offset)
                if 1 <= idx < len(l) - 1:
                    if l[idx] <= l[idx - 1] and l[idx] <= l[idx + 1]:
                        pivot = l[idx]
                        break
            mss_level = pivot if pivot is not None else min(ref_lows)
            if curr < mss_level:
                return True, mss_level, f"Bearish MSS: close {curr:.2f} broke structure low {mss_level:.2f}"
            return False, mss_level, f"Bearish MSS pending: close {curr:.2f} >= {mss_level:.2f}"


    def detect_m1_fvg(
        self,
        data: TimeframeData,
        direction: TradeDirection,
    ) -> Optional[Tuple[float, float]]:
        """Detect deterministic 3-candle Fair Value Gap (FVG) on M1.

        - Bullish FVG: candle_1.high < candle_3.low -> gap: [candle_1.high, candle_3.low]
        - Bearish FVG: candle_1.low > candle_3.high -> gap: [candle_3.high, candle_1.low]
        Returns (fvg_low, fvg_high) or None.
        """
        h = data.high
        l = data.low
        if len(h) < 3 or len(l) < 3:
            return None

        # 3-candle window: c1 = -3, c2 = -2, c3 = -1
        c1_high, c1_low = h[-3], l[-3]
        c3_high, c3_low = h[-1], l[-1]

        if direction == TradeDirection.BUY:
            if c1_high < c3_low:
                return (c1_high, c3_low)
        else:
            if c1_low > c3_high:
                return (c3_high, c1_low)

        return None

    def check_amt_rejection(
        self,
        data: TimeframeData,
        direction: TradeDirection,
        val: float,
        vah: float,
        poc: float,
    ) -> Tuple[bool, str]:
        """Auction Market Theory (AMT) Value Area Rejection Check.

        - BUY: Low swept at or below VAL and close reclaimed inside/above VAL.
               Target is POC.
        - SELL: High swept at or above VAH and close reclaimed inside/below VAH.
               Target is POC.
        """
        c = data.close
        h = data.high
        l = data.low
        if not c or not h or not l:
            return False, "insufficient data"

        curr_c = c[-1]
        curr_h = h[-1]
        curr_l = l[-1]

        if direction == TradeDirection.BUY:
            if curr_l <= val and curr_c > val:
                return True, f"AMT Bullish Rejection: low {curr_l:.2f} <= VAL {val:.2f} and close {curr_c:.2f} > VAL (target POC {poc:.2f})"
            return False, f"AMT Bullish Rejection not met: low {curr_l:.2f}, close {curr_c:.2f}, VAL {val:.2f}"
        else:
            if curr_h >= vah and curr_c < vah:
                return True, f"AMT Bearish Rejection: high {curr_h:.2f} >= VAH {vah:.2f} and close {curr_c:.2f} < VAH (target POC {poc:.2f})"
            return False, f"AMT Bearish Rejection not met: high {curr_h:.2f}, close {curr_c:.2f}, VAH {vah:.2f}"

    def check_vwap_band_exhaustion(
        self,
        data: TimeframeData,
        direction: TradeDirection,
        vwap_data: Tuple[float, float, float, float, float],
    ) -> Tuple[bool, str]:
        """VWAP Band Statistical Exhaustion & Mean-Reversion Check.

        - BUY: Low pierced Lower 2-sigma band and closed above it.
        - SELL: High pierced Upper 2-sigma band and closed below it.
        """
        c = data.close
        h = data.high
        l = data.low
        if not c or not h or not l:
            return False, "insufficient data"

        vwap_val, upper_1, lower_1, upper_2, lower_2 = vwap_data
        curr_c = c[-1]
        curr_h = h[-1]
        curr_l = l[-1]

        if direction == TradeDirection.BUY:
            if curr_l <= lower_2 and curr_c > lower_2:
                return True, f"VWAP Exhaustion Buy: low {curr_l:.2f} <= -2σ {lower_2:.2f} and close {curr_c:.2f} > -2σ (target VWAP {vwap_val:.2f})"
            return False, f"VWAP Exhaustion Buy not met: low={curr_l:.2f}, -2σ={lower_2:.2f}"
        else:
            if curr_h >= upper_2 and curr_c < upper_2:
                return True, f"VWAP Exhaustion Sell: high {curr_h:.2f} >= +2σ {upper_2:.2f} and close {curr_c:.2f} < +2σ (target VWAP {vwap_val:.2f})"
            return False, f"VWAP Exhaustion Sell not met: high={curr_h:.2f}, +2σ={upper_2:.2f}"

    def detect_xau_scalp_sequence(
        self,
        m15_data: TimeframeData,
        m1_data: TimeframeData,
        m1_atr: float,
        lookback_m15: int = 20,
        sequence_window_m1: int = 10,
        min_atr_mult: float = 0.60,
        min_body_ratio: float = 0.60,
        mss_lookback: int = 5,
        target_r: float = 2.0,
        point_value: float = 0.01,
        stops_level_points: float = 10.0,
        telemetry: Optional[Dict[str, int]] = None,
        liquidity_source: str = "m15_swings",
        current_time: Optional[datetime] = None,
        trend_bias: Optional[TradeDirection] = None,
        enable_delta_absorption: bool = True,
        delta_absorption_mode: str = "soft",
        enable_hvn_tp_calibration: bool = True,
        min_sl_distance: float = 0.0,
    ) -> Optional[dict]:
        """Detect the complete, causal XAUUSD Scalping Sequence:
        Liquidity Context -> M1 Sweep & Reclaim -> Delta Absorption -> Displacement -> True MSS -> FVG.

        Returns structured dictionary with setup telemetry or None.
        """
        # 1. Liquidity Context (closed bars only) - Professional Winner: Confirmed M15 Swings
        if liquidity_source == "asian_range":
            bsl, ssl = self.detect_asian_range_levels(m15_data, current_time=current_time)
        else:
            bsl, ssl = self.detect_m15_liquidity_levels(m15_data, lookback_bars=lookback_m15)

        if bsl is None or ssl is None:
            return None

        # 2. Check Trend Bias Alignment (if provided)
        if trend_bias is not None:
            if trend_bias == TradeDirection.BUY:
                bsl = None  # Block shorts
            elif trend_bias == TradeDirection.SELL:
                ssl = None  # Block longs

        # 3. M1 Sweep & Reclaim within sequence window
        h1 = m1_data.high
        l1 = m1_data.low
        c1 = m1_data.close
        o1 = m1_data.open
        v1 = m1_data.tick_volume if hasattr(m1_data, "tick_volume") else None
        n1 = len(c1)
        if n1 < sequence_window_m1 + 2:
            return None

        curr_c = c1[-1]
        is_forex = (curr_c < 10.0) or (point_value < 0.001)
        if is_forex and point_value >= 0.01:
            point_value = 0.00001
        digits = 5 if is_forex else 2
        sl_buffer = stops_level_points * point_value

        # -------------------------------------------------------------
        # Long Sequence: Sweep of SSL -> Reclaim -> Bullish Displacement -> Bullish MSS -> Bullish FVG
        # -------------------------------------------------------------
        if ssl is not None:
            sweep_indices_long = [
                i for i in range(n1 - sequence_window_m1, n1 - 1)
                if l1[i] < ssl
            ]
            if sweep_indices_long:
                if telemetry is not None:
                    telemetry["sweeps_detected"] = telemetry.get("sweeps_detected", 0) + 1
                if curr_c > ssl:
                    if telemetry is not None:
                        telemetry["reclaims_confirmed"] = telemetry.get("reclaims_confirmed", 0) + 1
                    sweep_idx = min(sweep_indices_long, key=lambda idx: l1[idx])
                    sweep_low = l1[sweep_idx]

                    # Institutional Delta Absorption: verify sellers absorbed and buyers stepped in
                    if enable_delta_absorption and v1 and len(v1) >= n1:
                        from ..indicators.quant_indicators import check_delta_absorption
                        is_abs, score, _ = check_delta_absorption(h1, l1, c1, o1, v1, sweep_idx, n1 - 1, "bullish")
                        if not is_abs:
                            if telemetry is not None:
                                telemetry["delta_absorption_rejected"] = telemetry.get("delta_absorption_rejected", 0) + 1
                            if delta_absorption_mode == "hard":
                                sweep_indices_long = []

                    if sweep_indices_long:
                        # Verify displacement occurred between sweep and current bar
                        has_displacement = False
                        for d in range(sweep_idx, n1):
                            body = c1[d] - o1[d]
                            rng = h1[d] - l1[d]
                            if body > 0 and rng > 0:
                                if (body / m1_atr) >= min_atr_mult and (body / rng) >= min_body_ratio:
                                    has_displacement = True
                                    break

                        if has_displacement:
                            if telemetry is not None:
                                telemetry["displacement_confirmed"] = telemetry.get("displacement_confirmed", 0) + 1
                            mss_ok, mss_lvl, _ = self.check_m1_mss(m1_data, TradeDirection.BUY, lookback=mss_lookback)
                            if mss_ok:
                                if telemetry is not None:
                                    telemetry["mss_confirmed"] = telemetry.get("mss_confirmed", 0) + 1
                                fvg = self.detect_m1_fvg(m1_data, TradeDirection.BUY)
                                if fvg is not None:
                                    if telemetry is not None:
                                        telemetry["fvg_created"] = telemetry.get("fvg_created", 0) + 1
                                    fvg_low, fvg_high = fvg
                                    entry_price = fvg_high
                                    structural_sl = min(sweep_low, min(l1[sweep_idx:])) - sl_buffer
                                    risk = entry_price - structural_sl
                                    effective_min_sl = min_sl_distance
                                    if entry_price > 1000:
                                        effective_min_sl = max(effective_min_sl, entry_price * 0.0016, m1_atr * 1.5)
                                    elif entry_price < 10.0 and effective_min_sl > 0.1:
                                        effective_min_sl = 0.0
                                    if effective_min_sl > 0 and risk < effective_min_sl:
                                        risk = effective_min_sl
                                        structural_sl = entry_price - risk
                                    if risk > 0:
                                        tp = entry_price + (risk * target_r)
                                        # HVN/POC Take-Profit Harmonization
                                        if enable_hvn_tp_calibration and m15_data.tick_volume and len(m15_data.close) >= 20:
                                            from ..indicators.quant_indicators import calc_hvn_lvn_nodes
                                            vp = calc_hvn_lvn_nodes(m15_data.high, m15_data.low, m15_data.close, m15_data.tick_volume, bins=25)
                                            poc = vp.get("poc", 0.0)
                                            if poc > 0 and entry_price < poc <= tp:
                                                r_poc = (poc - entry_price) / risk
                                                if r_poc >= 1.5:
                                                    tp = poc
                                        return {
                                            "direction": TradeDirection.BUY,
                                            "bsl": bsl,
                                            "ssl": ssl,
                                            "swept_level": ssl,
                                            "sweep_direction": "SSL_SWEEP",
                                            "sweep_low": sweep_low,
                                            "mss_level": mss_lvl,
                                            "fvg_low": fvg_low,
                                            "fvg_high": fvg_high,
                                            "entry_price": round(entry_price, digits),
                                            "sl_price": round(structural_sl, digits),
                                            "tp_price": round(tp, digits),
                                            "risk_distance": round(risk, digits),
                                            "target_r": target_r,
                                            "atr": round(m1_atr, 4 if digits == 2 else 6),
                                        }

        # -------------------------------------------------------------
        # Short Sequence: Sweep of BSL -> Reclaim -> Bearish Displacement -> Bearish MSS -> Bearish FVG
        # -------------------------------------------------------------
        if trend_bias is None or trend_bias == TradeDirection.SELL:
            sweep_indices_short = [idx for idx in range(max(0, n1 - sequence_window_m1), n1) if h1[idx] > bsl]
            if sweep_indices_short:
                if telemetry is not None:
                    telemetry["sweeps_detected"] = telemetry.get("sweeps_detected", 0) + 1
                if curr_c < bsl:
                    if telemetry is not None:
                        telemetry["reclaims_confirmed"] = telemetry.get("reclaims_confirmed", 0) + 1
                    sweep_idx = max(sweep_indices_short, key=lambda idx: h1[idx])
                    sweep_high = h1[sweep_idx]

                    # Institutional Delta Absorption: verify buyers absorbed and sellers stepped in
                    if enable_delta_absorption and v1 and len(v1) >= n1:
                        from ..indicators.quant_indicators import check_delta_absorption
                        is_abs, score, _ = check_delta_absorption(h1, l1, c1, o1, v1, sweep_idx, n1 - 1, "bearish")
                        if not is_abs:
                            if telemetry is not None:
                                telemetry["delta_absorption_rejected"] = telemetry.get("delta_absorption_rejected", 0) + 1
                            if delta_absorption_mode == "hard":
                                sweep_indices_short = []

                    if sweep_indices_short:
                        has_displacement = False
                        for d in range(sweep_idx, n1):
                            body = o1[d] - c1[d]
                            rng = h1[d] - l1[d]
                            if body > 0 and rng > 0:
                                if (body / m1_atr) >= min_atr_mult and (body / rng) >= min_body_ratio:
                                    has_displacement = True
                                    break

                        if has_displacement:
                            if telemetry is not None:
                                telemetry["displacement_confirmed"] = telemetry.get("displacement_confirmed", 0) + 1
                            mss_ok, mss_lvl, _ = self.check_m1_mss(m1_data, TradeDirection.SELL, lookback=mss_lookback)
                            if mss_ok:
                                if telemetry is not None:
                                    telemetry["mss_confirmed"] = telemetry.get("mss_confirmed", 0) + 1
                                fvg = self.detect_m1_fvg(m1_data, TradeDirection.SELL)
                                if fvg is not None:
                                    if telemetry is not None:
                                        telemetry["fvg_created"] = telemetry.get("fvg_created", 0) + 1
                                    fvg_low, fvg_high = fvg
                                    entry_price = fvg_low
                                    structural_sl = max(sweep_high, max(h1[sweep_idx:])) + sl_buffer
                                    risk = structural_sl - entry_price
                                    effective_min_sl = min_sl_distance
                                    if entry_price > 1000:
                                        effective_min_sl = max(effective_min_sl, entry_price * 0.0016, m1_atr * 1.5)
                                    elif entry_price < 10.0 and effective_min_sl > 0.1:
                                        effective_min_sl = 0.0
                                    if effective_min_sl > 0 and risk < effective_min_sl:
                                        risk = effective_min_sl
                                        structural_sl = entry_price + risk
                                    if risk > 0:
                                        tp = entry_price - (risk * target_r)
                                        # HVN/POC Take-Profit Harmonization
                                        if enable_hvn_tp_calibration and m15_data.tick_volume and len(m15_data.close) >= 20:
                                            from ..indicators.quant_indicators import calc_hvn_lvn_nodes
                                            vp = calc_hvn_lvn_nodes(m15_data.high, m15_data.low, m15_data.close, m15_data.tick_volume, bins=25)
                                            poc = vp.get("poc", 0.0)
                                            if poc > 0 and tp <= poc < entry_price:
                                                r_poc = (entry_price - poc) / risk
                                                if r_poc >= 1.5:
                                                    tp = poc
                                        return {
                                            "direction": TradeDirection.SELL,
                                            "bsl": bsl,
                                            "ssl": ssl,
                                            "swept_level": bsl,
                                            "sweep_direction": "BSL_SWEEP",
                                            "sweep_high": sweep_high,
                                            "mss_level": mss_lvl,
                                            "fvg_low": fvg_low,
                                            "fvg_high": fvg_high,
                                            "entry_price": round(entry_price, digits),
                                            "sl_price": round(structural_sl, digits),
                                            "tp_price": round(tp, digits),
                                            "risk_distance": round(risk, digits),
                                            "target_r": target_r,
                                            "atr": round(m1_atr, 4 if digits == 2 else 6),
                                        }

        return None

    def check_opposite_mss_invalidation(
        self,
        m1_data: TimeframeData,
        position_direction: TradeDirection,
        m1_atr: float,
        lookback: int = 5,
        min_atr_mult: float = 0.50,
        min_body_ratio: float = 0.55,
    ) -> Tuple[bool, str]:
        """Detect opposite displacement and market structure shift to invalidate open trade early.

        - In a BUY position: If M1 prints Bearish Displacement + Bearish MSS break, the trade is structurally failed.
        - In a SELL position: If M1 prints Bullish Displacement + Bullish MSS break, the trade is structurally failed.
        """
        c = m1_data.close
        o = m1_data.open
        h = m1_data.high
        l = m1_data.low
        if len(c) < lookback + 2 or m1_atr <= 0:
            return False, "insufficient data"

        recent_bars = min(3, len(c) - 1)
        has_opp_displacement = False
        for offset in range(recent_bars):
            idx = len(c) - 1 - offset
            body = abs(c[idx] - o[idx])
            rng = h[idx] - l[idx]
            if rng > 0 and (body / m1_atr) >= min_atr_mult and (body / rng) >= min_body_ratio:
                if position_direction == TradeDirection.BUY and c[idx] < o[idx]:
                    has_opp_displacement = True
                    break
                elif position_direction == TradeDirection.SELL and c[idx] > o[idx]:
                    has_opp_displacement = True
                    break

        if not has_opp_displacement:
            return False, "no opposite displacement"

        opp_direction = TradeDirection.SELL if position_direction == TradeDirection.BUY else TradeDirection.BUY
        mss_ok, mss_lvl, msg = self.check_m1_mss(m1_data, opp_direction, lookback=lookback)
        if mss_ok:
            return True, f"Opposite MSS invalidation: {msg}"

        return False, "no opposite mss"

    def detect_overlap_pullback_setup(
        self,
        m15_data: TimeframeData,
        m1_data: TimeframeData,
        m1_atr: float,
        trend_direction: TradeDirection,
        target_r: float = 2.0,
        point_value: float = 0.01,
        stops_level_points: float = 10.0,
        telemetry: Optional[Dict[str, int]] = None,
        min_sl_distance: float = 0.0,
    ) -> Optional[dict]:
        """Detect high-probability London/NY Overlap trend-continuation pullback on M15/M1."""
        if not m15_data.close or len(m15_data.close) < 50 or not m1_data.close or len(m1_data.close) < 15:
            return None

        e50 = ema(m15_data.close, 50)
        e21 = ema(m15_data.close, 21)
        if e50 is None or e21 is None:
            return None

        m15_close = m15_data.close[-1]
        m1_close = m1_data.close[-1]
        sl_buffer = max(0.6 * m1_atr, stops_level_points * point_value)

        digits = 5 if (m1_close < 10.0 or point_value < 0.001) else 2
        # Bullish Pullback: M15 close > 50-EMA and within range of 21-EMA
        if trend_direction == TradeDirection.BUY and m15_close > e50:
            recent_lows = m15_data.low[-3:]
            if min(recent_lows) <= e21 * 1.002:
                tbh_ok, _ = self.check_top_bottom_hunter(m1_data, TradeDirection.BUY, lookback=3, rsi_oversold=35.0)
                mss_ok, mss_lvl, _ = self.check_m1_mss(m1_data, TradeDirection.BUY, lookback=5)
                if tbh_ok or mss_ok:
                    entry_price = m1_close
                    recent_m1_low = min(m1_data.low[-8:])
                    sl_price = recent_m1_low - sl_buffer
                    risk = entry_price - sl_price
                    if min_sl_distance > 0 and risk < min_sl_distance:
                        risk = min_sl_distance
                        sl_price = entry_price - risk
                    if risk > 0 and (risk / m1_atr) <= 4.0:
                        tp_price = entry_price + (risk * target_r)
                        if telemetry is not None:
                            telemetry["overlap_pullbacks_triggered"] = telemetry.get("overlap_pullbacks_triggered", 0) + 1
                        return {
                            "direction": TradeDirection.BUY,
                            "setup_type": "OVERLAP_PULLBACK",
                            "entry_price": round(entry_price, digits),
                            "sl_price": round(sl_price, digits),
                            "tp_price": round(tp_price, digits),
                            "risk_distance": round(risk, digits),
                            "target_r": target_r,
                            "atr": round(m1_atr, 4 if digits == 2 else 6),
                        }

        # Bearish Pullback: M15 close < 50-EMA and within range of 21-EMA
        elif trend_direction == TradeDirection.SELL and m15_close < e50:
            recent_highs = m15_data.high[-3:]
            if max(recent_highs) >= e21 * 0.998:
                tbh_ok, _ = self.check_top_bottom_hunter(m1_data, TradeDirection.SELL, lookback=3, rsi_overbought=65.0)
                mss_ok, mss_lvl, _ = self.check_m1_mss(m1_data, TradeDirection.SELL, lookback=5)
                if tbh_ok or mss_ok:
                    entry_price = m1_close
                    recent_m1_high = max(m1_data.high[-8:])
                    sl_price = recent_m1_high + sl_buffer
                    risk = sl_price - entry_price
                    if min_sl_distance > 0 and risk < min_sl_distance:
                        risk = min_sl_distance
                        sl_price = entry_price + risk
                    if risk > 0 and (risk / m1_atr) <= 4.0:
                        tp_price = entry_price - (risk * target_r)
                        if telemetry is not None:
                            telemetry["overlap_pullbacks_triggered"] = telemetry.get("overlap_pullbacks_triggered", 0) + 1
                        return {
                            "direction": TradeDirection.SELL,
                            "setup_type": "OVERLAP_PULLBACK",
                            "entry_price": round(entry_price, digits),
                            "sl_price": round(sl_price, digits),
                            "tp_price": round(tp_price, digits),
                            "risk_distance": round(risk, digits),
                            "target_r": target_r,
                            "atr": round(m1_atr, 4 if digits == 2 else 6),
                        }

        return None




