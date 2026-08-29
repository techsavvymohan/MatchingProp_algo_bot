import logging
from typing import Dict, List, Optional

from ..models import (
    AccountInfo, ExitReason, PyraCluster, Signal, SignalGrade,
    TimeframeData, TradeDirection, TradeLeg, TradeStatus,
)
from ..risk.pyramid_manager import PyramidManager
from ..order.entry import OrderEntry
from ..order.exit import ExitManager
from ..order.partial_close import PartialCloseManager
from ..risk.daily_loss import DailyLossTracker
from ..risk.max_dd import MaxDDTracker
from ..risk.position_sizer import PositionSizer

log = logging.getLogger("xauusd_bot.trade.manager")


class TradeManager:
    def __init__(
        self,
        order_entry: OrderEntry,
        exit_mgr: ExitManager,
        partial_close: PartialCloseManager,
        pyramid_mgr: PyramidManager,
        sizer: PositionSizer,
        daily_loss: DailyLossTracker,
        max_dd: MaxDDTracker,
    ):
        self.order_entry = order_entry
        self.exit_mgr = exit_mgr
        self.partial_close = partial_close
        self.pyramid_mgr = pyramid_mgr
        self.sizer = sizer
        self.daily_loss = daily_loss
        self.max_dd = max_dd

    def execute_signal(
        self,
        signal: Signal,
        account: AccountInfo,
        data_all: Dict[str, TimeframeData],
        point_value: float,
        contract_size: int,
        min_lot: float,
        lot_step: float,
        max_lot: float = 100.0,
    ) -> Optional[PyraCluster]:
        if not signal.is_tradeable():
            log.info("Signal %s not tradeable (grade=%s)", signal.id[:8], signal.grade.value)
            return None

        if self.daily_loss.kill_switch_engaged():
            signal.equity_blocked = True
            log.warning("Daily loss kill switch active — not executing %s", signal.id[:8])
            return None
        if self.max_dd.kill_switch_engaged():
            signal.equity_blocked = True
            log.warning("Max DD kill switch active — not executing %s", signal.id[:8])
            return None

        remaining_budget = self.daily_loss.remaining_budget_amount()
        if remaining_budget <= 0:
            log.warning("No remaining budget — not executing %s", signal.id[:8])
            return None
        max_risk_amount = remaining_budget / max(self.sizer.max_pyramid_entries, 1)

        lot = self.sizer.calculate_lot_size(
            account=account,
            entry_price=signal.entry_price,
            sl_price=signal.sl_price,
            direction=signal.direction,
            point_value=point_value,
            contract_size=contract_size,
            min_lot=min_lot,
            max_lot=max_lot,
            lot_step=lot_step,
            remaining_budget=remaining_budget,
            max_risk_amount=max_risk_amount,
        )
        if lot <= 0:
            log.warning("Lot size zero — skipping signal %s", signal.id[:8])
            return None
        signal.lot_size = lot

        leg = self.order_entry.place_market_order(signal, account, point_value, contract_size, min_lot, lot_step)
        if leg is None:
            log.error("Order placement failed for signal %s", signal.id[:8])
            return None

        cluster = self.pyramid_mgr.create_cluster(signal.id, signal.direction, signal.entry_tf)
        cluster.symbol = getattr(signal, "symbol", "XAUUSD")
        cluster.legs.append(leg)
        cluster.collective_sl = signal.sl_price
        cluster.open_time = leg.open_time
        self.daily_loss.register_trade()
        log.info("Trade executed: %s %s %.2f lots at %.2f cluster=%s",
                 cluster.symbol, signal.direction.value, lot, leg.entry_price, cluster.cluster_id[:8])
        return cluster

    def manage_pyramid_add(
        self,
        signal: Signal,
        cluster: PyraCluster,
        account: AccountInfo,
        data_all: Dict[str, TimeframeData],
        point_value: float,
        contract_size: int,
        min_lot: float,
        lot_step: float,
        max_lot: float = 100.0,
    ) -> Optional[TradeLeg]:
        m15_data = data_all.get("M15")
        current_price = data_all.get("M1", m15_data)
        if current_price is None:
            return None
        price = current_price.close[-1]

        if not self.pyramid_mgr.can_add_leg(cluster, price, cluster.avg_entry_price(), cluster.collective_sl):
            return None

        remaining_budget = self.daily_loss.remaining_budget_amount()
        total_risk = cluster.total_risk_amount(point_value, contract_size)
        risk_budget = remaining_budget - total_risk
        if risk_budget <= 0:
            log.info("No risk budget for pyramid add")
            return None

        lot = self.sizer.calculate_lot_size(
            account=account,
            entry_price=price,
            sl_price=cluster.collective_sl,
            direction=cluster.direction,
            point_value=point_value,
            contract_size=contract_size,
            min_lot=min_lot,
            max_lot=max_lot,
            lot_step=lot_step,
            max_risk_amount=risk_budget,
        )
        if lot <= 0:
            return None

        entry_data = data_all.get(signal.entry_tf or "M1")
        atr_val = 0
        if entry_data:
            from ..indicators.atr import atr as calc_atr
            atr_val = calc_atr(entry_data.high, entry_data.low, entry_data.close, 14) or 0

        sl = self.exit_mgr.calc_atr_sl(entry_data or data_all.get("M15"), cluster.direction, signal.entry_tf or "M15")

        signal.sl_price = sl
        signal.lot_size = lot
        signal.entry_price = price
        signal.is_pyramid_add = True

        leg = self.order_entry.place_market_order(signal, account, point_value, contract_size, min_lot, lot_step)
        if leg is None:
            return None

        cluster.legs.append(leg)
        if cluster.direction == TradeDirection.BUY:
            cluster.highest_price = max(cluster.highest_price, price)
        else:
            cluster.lowest_price = min(cluster.lowest_price, price)
        self.daily_loss.register_trade()
        log.info("Pyramid add: leg=%d %.2f lots at %.2f cluster=%s",
                 cluster.leg_count(), lot, price, cluster.cluster_id[:8])
        return leg

    def manage_exits(
        self,
        cluster: PyraCluster,
        data_all: Dict[str, TimeframeData],
    ) -> List[dict]:
        actions = []
        m1_data = data_all.get("M1")
        m5_data = data_all.get("M5")
        if not m1_data or not m1_data.close:
            return actions
        current_price = m1_data.close[-1]

        if cluster.direction == TradeDirection.BUY:
            cluster.highest_price = max(cluster.highest_price, current_price)
        else:
            cluster.lowest_price = min(cluster.lowest_price, current_price)

        if self.partial_close.check_partial_tp(cluster, current_price):
            for leg in cluster.legs:
                if leg.status == TradeStatus.OPEN:
                    sym = getattr(leg, "symbol", "") or getattr(cluster, "symbol", "")
                    self.order_entry.close_position(leg.position_ticket, leg.lot_size * 0.5, leg.direction, symbol=sym)
                    leg.lot_size *= 0.5
                    if leg.lot_size <= 0:
                        leg.status = TradeStatus.CLOSED
            self.pyramid_mgr.activate_breakeven(cluster)
            actions.append({"action": "partial_tp", "cluster": cluster.cluster_id})

        if self.exit_mgr.check_time_exit(cluster):
            self._close_cluster_positions(cluster, current_price, ExitReason.TIME_BASED)
            actions.append({"action": "time_exit", "cluster": cluster.cluster_id, "price": current_price})
            return actions

        psar_exit = self.exit_mgr.check_psar_exit(m5_data or m1_data, cluster)
        if psar_exit is not None:
            self._close_cluster_positions(cluster, current_price, ExitReason.SIGNAL_REVERSAL)
            actions.append({"action": "psar_exit", "cluster": cluster.cluster_id, "price": current_price})
            return actions

        chandelier_stop = self.exit_mgr.check_chandelier_exit(m5_data or m1_data, cluster)
        if chandelier_stop is not None:
            if cluster.direction == TradeDirection.BUY and current_price <= chandelier_stop:
                self._close_cluster_positions(cluster, current_price, ExitReason.CHANDELIER_TRAIL)
                actions.append({"action": "chandelier_exit", "cluster": cluster.cluster_id, "price": current_price})
            elif cluster.direction == TradeDirection.SELL and current_price >= chandelier_stop:
                self._close_cluster_positions(cluster, current_price, ExitReason.CHANDELIER_TRAIL)
                actions.append({"action": "chandelier_exit", "cluster": cluster.cluster_id, "price": current_price})
        return actions

    def _close_cluster_positions(self, cluster: PyraCluster, price: float, reason: ExitReason):
        for leg in cluster.legs:
            if leg.status == TradeStatus.OPEN:
                sym = getattr(leg, "symbol", "") or getattr(cluster, "symbol", "")
                self.order_entry.close_position(leg.position_ticket, leg.lot_size, leg.direction, symbol=sym)
                leg.status = TradeStatus.CLOSED
                leg.exit_price = price
                leg.exit_reason = reason
        cluster.status = TradeStatus.CLOSED
        log.info("Cluster %s closed: %s at %.2f", cluster.cluster_id[:8], reason.value, price)
