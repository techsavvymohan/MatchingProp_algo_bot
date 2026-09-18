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
        persistence: Optional[object] = None,
    ):
        self.order_entry = order_entry
        self.exit_mgr = exit_mgr
        self.partial_close = partial_close
        self.pyramid_mgr = pyramid_mgr
        self.sizer = sizer
        self.daily_loss = daily_loss
        self.max_dd = max_dd
        self.persistence = persistence


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
        risk_scale: float = 1.0,
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
            risk_scale=risk_scale,
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
        cluster.fvg_low = getattr(signal, "fvg_low", 0.0)
        cluster.fvg_high = getattr(signal, "fvg_high", 0.0)
        cluster.highest_price = leg.entry_price
        cluster.lowest_price = leg.entry_price
        cluster.legs.append(leg)
        cluster.collective_sl = signal.sl_price
        cluster.open_time = leg.open_time
        cluster.status = TradeStatus.OPEN
        self.daily_loss.register_trade()
        log.info("Trade executed: %s %s %.2f lots at %.2f cluster=%s",
                 cluster.symbol, signal.direction.value, lot, leg.entry_price, cluster.cluster_id[:8])
        return cluster

    def execute_limit_signal(
        self,
        signal: Signal,
        limit_price: float,
        account: AccountInfo,
        point_value: float,
        contract_size: int,
        min_lot: float,
        lot_step: float,
        max_lot: float = 100.0,
        risk_scale: float = 1.0,
    ) -> Optional[PyraCluster]:
        """Submit an FVG Pending Limit Order and initialize pending cluster tracking."""
        if not signal.is_tradeable():
            log.info("Signal %s not tradeable", signal.id[:8])
            return None

        if self.daily_loss.kill_switch_engaged() or self.max_dd.kill_switch_engaged():
            signal.equity_blocked = True
            log.warning("Kill switch engaged — rejecting limit signal %s", signal.id[:8])
            return None

        remaining_budget = self.daily_loss.remaining_budget_amount()
        if remaining_budget <= 0:
            log.warning("No remaining risk budget — skipping limit signal %s", signal.id[:8])
            return None

        lot = self.sizer.calculate_lot_size(
            account=account,
            entry_price=limit_price,
            sl_price=signal.sl_price,
            direction=signal.direction,
            point_value=point_value,
            contract_size=contract_size,
            min_lot=min_lot,
            max_lot=max_lot,
            lot_step=lot_step,
            remaining_budget=remaining_budget,
            max_risk_amount=remaining_budget,
            risk_scale=risk_scale,
        )
        if lot <= 0:
            log.warning("Lot size 0 — skipping limit signal %s", signal.id[:8])
            return None
        signal.lot_size = lot

        leg = self.order_entry.place_limit_order(
            signal=signal,
            limit_price=limit_price,
            account=account,
            point_value=point_value,
            contract_size=contract_size,
            min_lot=min_lot,
            lot_step=lot_step,
            comment="FVG_Limit",
        )
        if leg is None:
            return None

        cluster = self.pyramid_mgr.create_cluster(signal.id, signal.direction, signal.entry_tf or "M1")
        cluster.symbol = getattr(signal, "symbol", "XAUUSD")
        cluster.fvg_low = getattr(signal, "fvg_low", 0.0)
        cluster.fvg_high = getattr(signal, "fvg_high", 0.0)
        cluster.highest_price = limit_price
        cluster.lowest_price = limit_price
        cluster.legs.append(leg)
        cluster.collective_sl = signal.sl_price
        cluster.open_time = leg.open_time
        cluster.status = TradeStatus.PENDING
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

        signal.lot_size = lot
        signal.entry_price = price
        signal.sl_price = cluster.collective_sl
        leg = self.order_entry.place_market_order(signal, account, point_value, contract_size, min_lot, lot_step)
        if leg is None:
            return None

        cluster.legs.append(leg)
        cluster.collective_sl = cluster.avg_entry_price()
        if cluster.direction == TradeDirection.BUY:
            cluster.highest_price = max(cluster.highest_price, price)
        else:
            if cluster.lowest_price <= 0.0:
                cluster.lowest_price = price
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
        if cluster.status != TradeStatus.OPEN:
            return actions
        m1_data = data_all.get("M1")
        m5_data = data_all.get("M5")
        if not m1_data or not m1_data.close:
            return actions
        current_price = m1_data.close[-1]

        # 1. Update high-water / low-water extremes safely
        if cluster.direction == TradeDirection.BUY:
            cluster.highest_price = max(cluster.highest_price, current_price)
        else:
            if cluster.lowest_price <= 0.0:
                cluster.lowest_price = current_price
            else:
                cluster.lowest_price = min(cluster.lowest_price, current_price)

        cfg_obj = getattr(self.exit_mgr, "config", getattr(self.exit_mgr, "cfg", None))

        # Check if this cluster is an FVG / Liquidity Sweep scalp setup
        is_fvg_trade = (
            getattr(cluster, "fvg_low", 0.0) > 0
            or getattr(cluster, "fvg_high", 0.0) > 0
            or getattr(cfg_obj, "strategy_trigger_type", "") in ("xau_liquidity_sweep_fvg_m1", "liquidity_sweep_fvg")
        )

        # 2. Dynamic Breakeven Ratchet (+1.0R move -> locks in +0.05R to guarantee no winner becomes a loser)
        be_enabled = getattr(cfg_obj, "xau_breakeven_ratchet_enabled", True)
        if not isinstance(be_enabled, bool):
            be_enabled = True
        if be_enabled and not cluster.breakeven_activated:
            sym = getattr(cluster, "symbol", "XAUUSD")
            if hasattr(cfg_obj, "get_breakeven_trigger_r"):
                be_trig = cfg_obj.get_breakeven_trigger_r(sym)
            else:
                be_trig = getattr(cfg_obj, "xau_breakeven_trigger_r", 1.75)
            if not isinstance(be_trig, (int, float)):
                be_trig = 1.75
            be_buf = getattr(cfg_obj, "xau_breakeven_buffer_r", 0.05)
            if not isinstance(be_buf, (int, float)):
                be_buf = 0.05
            new_be = self.exit_mgr.check_breakeven_ratchet(cluster, current_price, trigger_r=float(be_trig), buffer_r=float(be_buf))
            if new_be is not None:
                cluster.collective_sl = new_be
                cluster.breakeven_activated = True
                for leg in cluster.legs:
                    if leg.status == TradeStatus.OPEN:
                        leg.sl_price = new_be
                        if leg.position_ticket > 0:
                            self.order_entry.modify_sl_tp(leg.position_ticket, new_be, leg.tp_price)
                actions.append({"action": "breakeven_ratchet", "sl": new_be, "cluster": cluster.cluster_id})

        # 3. Partial TP check
        pc_enabled = getattr(cfg_obj, "xau_partial_close_enabled", True)
        if not isinstance(pc_enabled, bool):
            pc_enabled = True
        if self.partial_close and pc_enabled and self.partial_close.check_partial_tp(cluster, current_price):
            for leg in cluster.legs:
                if leg.status == TradeStatus.OPEN:
                    sym = getattr(leg, "symbol", "") or getattr(cluster, "symbol", "")
                    self.order_entry.close_position(leg.position_ticket, leg.lot_size * 0.5, leg.direction, symbol=sym)
                    leg.lot_size *= 0.5
                    if leg.lot_size <= 0:
                        leg.status = TradeStatus.CLOSED
            self.pyramid_mgr.activate_breakeven(cluster)
            actions.append({"action": "partial_tp", "cluster": cluster.cluster_id})

        # 4. Holding time exit (only for non-FVG trades, or trades meeting full threshold)
        if not is_fvg_trade and self.exit_mgr.check_time_exit(cluster):
            self._close_cluster_positions(cluster, current_price, ExitReason.TIME_BASED)
            pnl_tot = sum(getattr(l, "pnl", 0.0) for l in cluster.legs)
            actions.append({"action": "time_exit", "cluster": cluster.cluster_id, "price": current_price, "pnl": pnl_tot})
            return actions

        # 5. Parabolic SAR Signal Reversal Exit
        # CRITICAL PROTECTION: FVG scalp trades enter counter-trend into sweeps; M5 PSAR naturally opposes them at entry.
        # Decouple PSAR reversal exit from FVG scalps so they are never closed prematurely on tick #1.
        # For non-FVG (momentum) trades, require at least 180s holding time and config enablement.
        enable_psar = getattr(cfg_obj, "enable_psar_trailing", True)
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        trade_age_s = (now_utc - cluster.open_time).total_seconds() if cluster.open_time else 999.0

        if not is_fvg_trade and enable_psar and trade_age_s >= 180.0:
            psar_exit = self.exit_mgr.check_psar_exit(m5_data or m1_data, cluster)
            if psar_exit is not None:
                self._close_cluster_positions(cluster, current_price, ExitReason.SIGNAL_REVERSAL)
                pnl_tot = sum(getattr(l, "pnl", 0.0) for l in cluster.legs)
                actions.append({"action": "psar_exit", "cluster": cluster.cluster_id, "price": current_price, "pnl": pnl_tot})
                return actions

        # 6. Chandelier Exit / Trailing Ratchet
        # For FVG scalp trades, Chandelier trailing ONLY ratchets runner legs that have already banked partial TP,
        # or when position has moved significantly in favor (never kills a freshly opened base position).
        # For momentum trades, checks chandelier trailing after minimum holding duration.
        if is_fvg_trade:
            runner_legs = [l for l in cluster.legs if l.status == TradeStatus.OPEN and getattr(l, "_is_runner", False)]
            if runner_legs:
                trail_stop = self.exit_mgr.check_chandelier_exit(m1_data, cluster)
                if trail_stop is not None:
                    for r_leg in runner_legs:
                        if cluster.direction == TradeDirection.BUY and current_price <= trail_stop:
                            sym = getattr(r_leg, "symbol", "") or getattr(cluster, "symbol", "")
                            self.order_entry.close_position(r_leg.position_ticket, r_leg.lot_size, r_leg.direction, symbol=sym)
                            r_leg.status = TradeStatus.CLOSED
                            r_leg.exit_price = current_price
                            r_leg.exit_reason = ExitReason.CHANDELIER_TRAIL
                            actions.append({"action": "runner_chandelier_exit", "cluster": cluster.cluster_id, "price": current_price})
                        elif cluster.direction == TradeDirection.SELL and current_price >= trail_stop:
                            sym = getattr(r_leg, "symbol", "") or getattr(cluster, "symbol", "")
                            self.order_entry.close_position(r_leg.position_ticket, r_leg.lot_size, r_leg.direction, symbol=sym)
                            r_leg.status = TradeStatus.CLOSED
                            r_leg.exit_price = current_price
                            r_leg.exit_reason = ExitReason.CHANDELIER_TRAIL
                            actions.append({"action": "runner_chandelier_exit", "cluster": cluster.cluster_id, "price": current_price})
        else:
            chandelier_stop = self.exit_mgr.check_chandelier_exit(m5_data or m1_data, cluster)
            if chandelier_stop is not None:
                if cluster.direction == TradeDirection.BUY and current_price <= chandelier_stop:
                    self._close_cluster_positions(cluster, current_price, ExitReason.CHANDELIER_TRAIL)
                    pnl_tot = sum(getattr(l, "pnl", 0.0) for l in cluster.legs)
                    actions.append({"action": "chandelier_exit", "cluster": cluster.cluster_id, "price": current_price, "pnl": pnl_tot})
                elif cluster.direction == TradeDirection.SELL and current_price >= chandelier_stop:
                    self._close_cluster_positions(cluster, current_price, ExitReason.CHANDELIER_TRAIL)
                    pnl_tot = sum(getattr(l, "pnl", 0.0) for l in cluster.legs)
                    actions.append({"action": "chandelier_exit", "cluster": cluster.cluster_id, "price": current_price, "pnl": pnl_tot})
        return actions

    def _close_cluster_positions(self, cluster: PyraCluster, price: float, reason: ExitReason):
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        for leg in cluster.legs:
            if leg.status in (TradeStatus.OPEN, TradeStatus.PENDING):
                sym = getattr(leg, "symbol", "") or getattr(cluster, "symbol", "")
                was_open = (leg.status == TradeStatus.OPEN)
                if was_open:
                    self.order_entry.close_position(leg.position_ticket, leg.lot_size, leg.direction, symbol=sym)
                elif leg.status == TradeStatus.PENDING:
                    self.order_entry.cancel_order(leg.position_ticket, signal_id=getattr(cluster, "signal_id", None), symbol=sym)

                leg.status = TradeStatus.CLOSED
                leg.exit_price = price if was_open else 0.0
                leg.exit_reason = reason
                leg.close_time = now_utc

                # Realized PnL calculation (only for positions that were filled/OPEN)
                if was_open and leg.entry_price and price:
                    if leg.direction == TradeDirection.BUY:
                        pnl_points = price - leg.entry_price
                    else:
                        pnl_points = leg.entry_price - price
                    c_sz = 100.0 if "XAU" in sym else 100000.0
                    leg.pnl = round(pnl_points * leg.lot_size * c_sz, 2)
                else:
                    leg.pnl = 0.0

                if self.persistence:
                    try:
                        self.persistence.save_trade_leg(leg, cluster.cluster_id, getattr(cluster, "signal_id", ""), cluster.entry_tf)
                        self.persistence.append_trade_csv({
                            "cluster_id": cluster.cluster_id,
                            "leg_id": leg.leg_id,
                            "symbol": sym,
                            "direction": leg.direction.value,
                            "entry_price": leg.entry_price,
                            "exit_price": leg.exit_price,
                            "lot_size": leg.lot_size,
                            "pnl": leg.pnl,
                            "exit_reason": reason.value,
                            "open_time": leg.open_time.isoformat() if leg.open_time else "",
                            "close_time": leg.close_time.isoformat() if leg.close_time else "",
                        })
                    except Exception as e:
                        log.warning("Failed saving trade leg to persistence: %s", e)

        cluster.status = TradeStatus.CLOSED
        log.info("Cluster %s closed: %s at %.2f", cluster.cluster_id[:8], reason.value, price)

