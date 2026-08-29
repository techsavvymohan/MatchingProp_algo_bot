import logging
from typing import Optional

from ..models import PyraCluster, TradeDirection, TradeStatus, ExitReason

log = logging.getLogger("xauusd_bot.order.partial")


class PartialCloseManager:
    def __init__(self, take_profit_r: float = 1.0, close_pct: float = 50.0):
        self.take_profit_r = take_profit_r
        self.close_pct = close_pct
        self._tp_hit: set = set()

    def check_partial_tp(self, cluster: PyraCluster, current_price: float) -> bool:
        cluster_id = cluster.cluster_id
        if cluster_id in self._tp_hit:
            return False
        avg_entry = cluster.avg_entry_price()
        if avg_entry <= 0:
            return False
        atr_based = abs(avg_entry - cluster.collective_sl) if cluster.collective_sl else 0
        if atr_based <= 0:
            return False
        if cluster.direction == TradeDirection.BUY:
            move_r = (current_price - avg_entry) / atr_based
        else:
            move_r = (avg_entry - current_price) / atr_based
        if move_r >= self.take_profit_r:
            self._tp_hit.add(cluster_id)
            log.info("Partial TP triggered: cluster=%s move=%.2fR pct=%.0f%%",
                     cluster_id[:8], move_r, self.close_pct)
            return True
        return False

    def reset(self):
        self._tp_hit.clear()
