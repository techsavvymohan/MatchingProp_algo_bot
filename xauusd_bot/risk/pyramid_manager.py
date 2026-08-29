import logging
from datetime import datetime, timezone
from typing import List, Optional

from ..models import PyraCluster, TradeDirection, TradeLeg, TradeStatus

log = logging.getLogger("xauusd_bot.risk.pyramid")


class PyramidManager:
    def __init__(self, max_entries: int = 4, add_trigger_r: float = 0.5):
        self.max_entries = max_entries
        self.add_trigger_r = add_trigger_r
        self._clusters: List[PyraCluster] = []

    @property
    def active_clusters(self) -> List[PyraCluster]:
        return [c for c in self._clusters if c.status == TradeStatus.OPEN]

    def create_cluster(self, signal_id: str, direction: TradeDirection, entry_tf: str) -> PyraCluster:
        cluster = PyraCluster(
            signal_id=signal_id,
            direction=direction,
            entry_tf=entry_tf,
        )
        self._clusters.append(cluster)
        return cluster

    def can_add_leg(self, cluster: PyraCluster, current_price: float, entry_price: float,
                    collective_sl: float) -> bool:
        if cluster.leg_count() >= self.max_entries:
            return False
        if cluster.leg_count() == 0:
            return True
        avg_entry = cluster.avg_entry_price()
        if avg_entry <= 0:
            return False
        if cluster.direction == TradeDirection.BUY:
            move_r = (current_price - avg_entry) / abs(avg_entry - collective_sl) if avg_entry != collective_sl else 0
        else:
            move_r = (avg_entry - current_price) / abs(avg_entry - collective_sl) if avg_entry != collective_sl else 0
        if move_r < self.add_trigger_r:
            return False
        log.info("Pyramid add eligible: move=%.2fR threshold=%.1fR leg=%d/%d",
                 move_r, self.add_trigger_r, cluster.leg_count() + 1, self.max_entries)
        return True

    def update_collective_sl(self, cluster: PyraCluster, new_sl: float):
        cluster.collective_sl = new_sl
        for leg in cluster.legs:
            if leg.status == TradeStatus.OPEN:
                leg.sl_price = new_sl

    def activate_breakeven(self, cluster: PyraCluster):
        if cluster.breakeven_activated:
            return
        be_price = cluster.avg_entry_price()
        self.update_collective_sl(cluster, be_price)
        cluster.breakeven_activated = True
        log.info("Breakeven activated for cluster %s at %.2f", cluster.cluster_id[:8], be_price)

    def update_trailing_prices(self, cluster: PyraCluster, current_high: float, current_low: float):
        if cluster.direction == TradeDirection.BUY and current_high > cluster.highest_price:
            cluster.highest_price = current_high
        if cluster.direction == TradeDirection.SELL and current_low < cluster.lowest_price:
            cluster.lowest_price = current_low

    def close_cluster(self, cluster: PyraCluster, exit_price: float, reason: str, exit_reason):
        for leg in cluster.legs:
            if leg.status == TradeStatus.OPEN:
                leg.status = TradeStatus.CLOSED
                leg.close_time = datetime.now(timezone.utc).replace(tzinfo=None)
                leg.exit_price = exit_price
                leg.exit_reason = exit_reason
        cluster.status = TradeStatus.CLOSED
        log.info("Cluster %s closed: %s at %.2f", cluster.cluster_id[:8], reason, exit_price)

    def total_open_risk(self, point_value: float, contract_size: int = 100) -> float:
        total = 0.0
        for cluster in self.active_clusters:
            total += cluster.total_risk_amount(point_value, contract_size)
        return total
