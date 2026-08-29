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
