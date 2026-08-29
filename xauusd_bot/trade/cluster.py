import logging
from typing import List, Optional

from ..models import PyraCluster, TradeDirection, TradeLeg, TradeStatus

log = logging.getLogger("xauusd_bot.trade.cluster")


class ClusterManager:
    def __init__(self):
        self._clusters: List[PyraCluster] = []

    @property
    def active(self) -> List[PyraCluster]:
        return [c for c in self._clusters if c.status == TradeStatus.OPEN]

    @property
    def all_clusters(self) -> List[PyraCluster]:
        return list(self._clusters)

    def add(self, cluster: PyraCluster):
        self._clusters.append(cluster)

    def get(self, cluster_id: str) -> Optional[PyraCluster]:
        for c in self._clusters:
            if c.cluster_id == cluster_id:
                return c
        return None

    def has_active_for_direction(self, direction: TradeDirection, symbol: str = "") -> bool:
        if symbol:
            return any(c.direction == direction and c.status == TradeStatus.OPEN and c.symbol == symbol for c in self._clusters)
        return any(c.direction == direction and c.status == TradeStatus.OPEN for c in self._clusters)

    def active_clusters_for_direction(self, direction: TradeDirection, symbol: str = "") -> List[PyraCluster]:
        if symbol:
            return [c for c in self._clusters if c.direction == direction and c.status == TradeStatus.OPEN and c.symbol == symbol]
        return [c for c in self._clusters if c.direction == direction and c.status == TradeStatus.OPEN]

    def active_clusters_for_symbol(self, symbol: str) -> List[PyraCluster]:
        return [c for c in self._clusters if c.symbol == symbol and c.status == TradeStatus.OPEN]

    def total_open_lots(self, symbol: str = "") -> float:
        if symbol:
            return sum(c.total_lot_size() for c in self.active_clusters_for_symbol(symbol))
        return sum(c.total_lot_size() for c in self.active)

    def clear(self):
        self._clusters.clear()
