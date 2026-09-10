import logging
from typing import Optional

from ..config import TradingConfig
from ..indicators.atr import atr
from ..models import PyraCluster, TimeframeData, TradeDirection, ExitReason, TradeStatus

log = logging.getLogger("xauusd_bot.order.exit")


class ExitManager:
    def __init__(self, config: TradingConfig):
        self.config = config

    def calc_atr_sl(self, data: TimeframeData, direction: TradeDirection, tf: str) -> float:
        a = atr(data.high, data.low, data.close, self.config.atr_period)
        if a is None:
            a = (max(data.high[-14:]) - min(data.low[-14:])) / 14
        multiplier = self.config.atr_multiplier_for_tf(tf)
        current = data.close[-1]
        if direction == TradeDirection.BUY:
            return current - a * multiplier
        return current + a * multiplier

    def calc_structure_tp(self, data: TimeframeData, direction: TradeDirection,
                          entry_price: float, atr_value: float) -> float:
        lookback = 20
        if direction == TradeDirection.BUY:
            swing_highs = []
            for i in range(1, len(data.high) - 1):
                if data.high[i] > data.high[i - 1] and data.high[i] > data.high[i + 1]:
                    swing_highs.append(data.high[i])
            target = max(swing_highs[-3:]) if len(swing_highs) >= 3 else max(data.high[-lookback:])
        else:
            swing_lows = []
            for i in range(1, len(data.low) - 1):
                if data.low[i] < data.low[i - 1] and data.low[i] < data.low[i + 1]:
                    swing_lows.append(data.low[i])
            target = min(swing_lows[-3:]) if len(swing_lows) >= 3 else min(data.low[-lookback:])
        max_r_move = self.config.max_r_multiple * atr_value
        if direction == TradeDirection.BUY:
            capped_target = min(target, entry_price + max_r_move)
        else:
            capped_target = max(target, entry_price - max_r_move)
        return capped_target

    def check_time_exit(self, cluster: PyraCluster) -> bool:
        if cluster.open_time is None:
            return False
        from ..utils.time_utils import minutes_since
        elapsed = minutes_since(cluster.open_time)
        if elapsed > self.config.time_based_exit_minutes:
            avg_entry = cluster.avg_entry_price()
            log.info("Time exit triggered: %.1f min elapsed for cluster %s (entry=%.2f)",
                     elapsed, cluster.cluster_id[:8], avg_entry)
            return True
        return False

    def check_chandelier_exit(self, data: TimeframeData, cluster: PyraCluster) -> Optional[float]:
        a = atr(data.high, data.low, data.close, 22)
        if a is None:
            return None
        if cluster.direction == TradeDirection.BUY:
            if cluster.highest_price <= 0:
                return None
            return cluster.highest_price - a * 3.0
        else:
            if cluster.lowest_price <= 0:
                return None
            return cluster.lowest_price + a * 3.0

    def check_psar_exit(self, data: TimeframeData, cluster: PyraCluster) -> Optional[float]:
        from ..indicators.quant_indicators import parabolic_sar
        if len(data.close) < 5:
            return None
        psar_res = parabolic_sar(data.high, data.low, data.close)
        sar_series = psar_res.get("sar", [])
        trend_series = psar_res.get("trend", [])
        if not sar_series or not trend_series:
            return None
        latest_sar = sar_series[-1]
        latest_trend = trend_series[-1]
        if cluster.direction == TradeDirection.BUY and latest_trend < 0:
            return latest_sar
        elif cluster.direction == TradeDirection.SELL and latest_trend > 0:
            return latest_sar
        return None

    def check_volatility_step_trail(
        self,
        cluster: PyraCluster,
        current_price: float,
    ) -> Optional[float]:
        """Multi-stage ratchet trailing stop adapted from quantitative exit policies (exitkit).
        
        Ratchets the collective stop as favorable price excursions occur:
        - At +1.5R move: locks in +0.5R profit
        - At +2.0R move: locks in +1.0R profit
        - At +3.0R move: locks in +2.0R profit
        
        Monotonically enforces that SL can only move in favor of the trade.
        """
        avg_entry = cluster.avg_entry_price()
        if avg_entry <= 0:
            return None
        r_dist = cluster.r_distance() if hasattr(cluster, "r_distance") else 0.0
        if r_dist <= 0:
            return None

        if cluster.direction == TradeDirection.BUY:
            move_r = (current_price - avg_entry) / r_dist
            target_sl = None
            if move_r >= 3.0:
                target_sl = avg_entry + 2.0 * r_dist
            elif move_r >= 2.0:
                target_sl = avg_entry + 1.0 * r_dist
            elif move_r >= 1.5:
                target_sl = avg_entry + 0.5 * r_dist

            if target_sl is not None and target_sl > cluster.collective_sl:
                return target_sl
        else:
            move_r = (avg_entry - current_price) / r_dist
            target_sl = None
            if move_r >= 3.0:
                target_sl = avg_entry - 2.0 * r_dist
            elif move_r >= 2.0:
                target_sl = avg_entry - 1.0 * r_dist
            elif move_r >= 1.5:
                target_sl = avg_entry - 0.5 * r_dist

            if target_sl is not None and (cluster.collective_sl <= 0 or target_sl < cluster.collective_sl):
                return target_sl

        return None

    def check_breakeven_ratchet(
        self,
        cluster: PyraCluster,
        current_price: float,
        trigger_r: float = 1.0,
        buffer_r: float = 0.05,
    ) -> Optional[float]:
        """Move Stop Loss to entry + buffer when trade reaches trigger_r (default 1.0R)."""
        avg_entry = cluster.avg_entry_price()
        if avg_entry <= 0:
            return None
        r_dist = cluster.r_distance() if hasattr(cluster, "r_distance") else 0.0
        if r_dist <= 0:
            return None

        if cluster.direction == TradeDirection.BUY:
            move_r = (current_price - avg_entry) / r_dist
            if move_r >= trigger_r:
                target_sl = avg_entry + buffer_r * r_dist
                if target_sl > cluster.collective_sl:
                    return target_sl
        else:
            move_r = (avg_entry - current_price) / r_dist
            if move_r >= trigger_r:
                target_sl = avg_entry - buffer_r * r_dist
                if cluster.collective_sl <= 0 or target_sl < cluster.collective_sl:
                    return target_sl

        return None

    def check_stagnation_exit(
        self,
        cluster: PyraCluster,
        current_price: float,
        bars_held: int,
        max_bars: int = 8,
        min_r: float = 0.40,
    ) -> bool:
        """Exit flat if a scalp trade has been open for max_bars and hasn't reached min_r."""
        if bars_held < max_bars:
            return False
        avg_entry = cluster.avg_entry_price()
        if avg_entry <= 0:
            return False
        r_dist = cluster.r_distance() if hasattr(cluster, "r_distance") else 0.0
        if r_dist <= 0:
            return False

        if cluster.direction == TradeDirection.BUY:
            move_r = (current_price - avg_entry) / r_dist
        else:
            move_r = (avg_entry - current_price) / r_dist

        return move_r < min_r

    def check_structural_invalidation(
        self,
        cluster: PyraCluster,
        m1_data: TimeframeData,
        m1_atr: float = 1.0,
    ) -> tuple[bool, str]:
        """Detect opposite displacement, MSS, or order flow absorption breakdown to exit an open trade before full SL."""
        from ..strategy.trigger import TriggerDetector
        td = TriggerDetector()
        
        # 1. Check Opposite Displacement & MSS
        mss_inv, mss_msg = td.check_opposite_mss_invalidation(m1_data, cluster.direction, m1_atr)
        if mss_inv:
            return True, mss_msg

        # 2. Check Order Flow Absorption Breakdown (Aggressive Institutional Volume Spike in Adverse Direction)
        c = m1_data.close
        o = m1_data.open
        h = m1_data.high
        l = m1_data.low
        v = m1_data.tick_volume
        if c and o and h and l and v and len(c) >= 5:
            from ..indicators.quant_indicators import calc_bar_delta
            d_curr = calc_bar_delta(o[-1], h[-1], l[-1], c[-1], v[-1])
            avg_v = sum(v[-5:]) / 5.0
            if avg_v > 0:
                if cluster.direction == TradeDirection.BUY:
                    # Heavy institutional dumping breaking prior low
                    if d_curr < -1.5 * avg_v and c[-1] < min(l[-3:-1]):
                        return True, f"Adverse Delta Breakdown: Sell delta {d_curr:.1f} broke local support"
                else:
                    # Heavy institutional pump breaking prior high
                    if d_curr > 1.5 * avg_v and c[-1] > max(h[-3:-1]):
                        return True, f"Adverse Delta Breakdown: Buy delta {d_curr:.1f} broke local resistance"

        return False, "structure and delta intact"
