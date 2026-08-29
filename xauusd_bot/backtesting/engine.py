import copy
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..indicators.atr import atr
from ..indicators.moving_averages import ema
from ..indicators.rsi import rsi
from ..models import (
    AccountInfo, Bias, DailyState, ExitReason, PyraCluster, Regime, Session,
    Signal, SignalGrade, TimeframeData, TradeDirection, TradeLeg, TradeStatus,
)
from ..risk.daily_loss import DailyLossTracker
from ..risk.max_dd import MaxDDTracker
from ..risk.position_sizer import PositionSizer
from ..risk.pyramid_manager import PyramidManager
from ..order.exit import ExitManager
from ..order.partial_close import PartialCloseManager
from ..strategy.bias_detector import BiasDetector
from ..strategy.signal_scorer import SignalScorer
from ..strategy.timeframe_hierarchy import TimeframeHierarchy
from ..strategy.trigger import TriggerDetector
from ..strategy.zone_detector import ZoneDetector
from ..filters.session_filter import SessionFilter
from ..utils.time_utils import current_session, minutes_since

log = logging.getLogger("xauusd_bot.backtest")


class BacktestEngine:
    def __init__(self, config: Config, initial_balance: Optional[float] = None, symbol: Optional[str] = None,
                 start_date: Optional[datetime] = None, end_date: Optional[datetime] = None):
        self.cfg = config
        self.start_date = start_date
        self.end_date = end_date
        if initial_balance is not None:
            self.cfg.trading.backtest_initial_balance = initial_balance
        if symbol is not None:
            self.cfg.trading.symbol = symbol
        self.bias = BiasDetector(
            config.trading.ema_fast, config.trading.ema_medium, config.trading.ema_slow,
            config.trading.rsi_period, config.trading.rsi_mid_upper, config.trading.rsi_mid_lower,
        )
        self.zone = ZoneDetector(config.trading.vwap_period, config.trading.min_structure_swing_bars,
                                 config.trading.max_structure_swing_bars)
        from ..strategy.sideways_detector import SidewaysDetector
        self.sideways = SidewaysDetector(
            chop_threshold=config.trading.sideways_chop_threshold,
            adx_threshold=config.trading.sideways_adx_threshold,
            bandwidth_squeeze_pct=config.trading.sideways_bandwidth_squeeze_pct,
        ) if getattr(config.trading, "enable_sideways_filter", True) else None
        self.hierarchy = TimeframeHierarchy(
            self.bias,
            self.zone,
            sideways_detector=self.sideways,
            session_agnostic=not getattr(config.trading, "enable_session_filter", False),
        )
        self.scorer = SignalScorer(config.trading.signal_score_a_min, config.trading.signal_score_b_min)
        self.trigger = TriggerDetector(config.trading.ema_fast, config.trading.rsi_period,
                                       config.trading.rsi_mid_upper, config.trading.rsi_mid_lower)
        self.exit_mgr = ExitManager(config.trading)
        self.sizer = PositionSizer(config.trading.pyramid_initial_risk_pct, config.trading.max_pyramid_entries)
        self.partial_close = PartialCloseManager(config.trading.partial_take_profit_r, config.trading.partial_close_pct)
        self.daily_loss = DailyLossTracker(
            config.trading.daily_loss_limit_pct, config.trading.daily_loss_buffer_pct,
            config.trading.broker_daily_reset_hour, config.trading.broker_daily_reset_tz,
        )
        self.max_dd = MaxDDTracker(config.trading.max_dd_limit_pct, config.trading.max_dd_buffer_pct)
        self.pyramid_mgr = PyramidManager(config.trading.max_pyramid_entries, config.trading.pyramid_add_trigger_r)
        self.session_filter = SessionFilter(
            config.trading.session_london_open, config.trading.session_london_close,
            config.trading.session_ny_open, config.trading.session_ny_close,
        )
        self.trades: List[dict] = []
        self._clusters: List[PyraCluster] = []

    def run(self, data: Dict) -> dict:
        if data and isinstance(next(iter(data.values())), dict):
            data = self._convert_dicts(data)
        if "M1" not in data:
            log.error("M1 data required for backtest")
            return {}
        m1 = data["M1"]
        account = AccountInfo(
            balance=self.cfg.trading.backtest_initial_balance,
            equity=self.cfg.trading.backtest_initial_balance,
        )
        self.daily_loss.update(account)
        self.max_dd.reset(account.equity)
        hierarchy_result = None
        data_all = None

        for i in range(100, len(m1.close)):
            current_time = m1.time[i]
            current_price = m1.close[i]

            if self.start_date is not None:
                t_chk = current_time.replace(tzinfo=None) if getattr(current_time, "tzinfo", None) else current_time
                s_chk = self.start_date.replace(tzinfo=None) if getattr(self.start_date, "tzinfo", None) else self.start_date
                if t_chk < s_chk:
                    continue

            if self.end_date is not None:
                t_chk = current_time.replace(tzinfo=None) if getattr(current_time, "tzinfo", None) else current_time
                e_chk = self.end_date.replace(tzinfo=None) if getattr(self.end_date, "tzinfo", None) else self.end_date
                if t_chk > e_chk:
                    break

            account.equity = self._compute_equity(account.balance, current_price, i)
            account.balance = max(account.balance, account.equity)
            self.daily_loss.update(account)
            self.max_dd.update(account.equity)

            if self.daily_loss.kill_switch_engaged() or self.max_dd.kill_switch_engaged():
                self._close_all(current_price)
                continue

            if i % 5 == 0 or hierarchy_result is None or data_all is None:
                m5 = self._slice_data(data, "M5", i, current_time)
                m15 = self._slice_data(data, "M15", i, current_time)
                h1 = self._slice_data(data, "H1", i, current_time)
                h4 = self._slice_data(data, "H4", i, current_time)
                if not all([m5, m15, h1, h4]):
                    continue
                data_all = {"M1": m1, "M5": m5, "M15": m15, "H1": h1, "H4": h4}
                hierarchy_result = self.hierarchy.evaluate(data_all, Session.LONDON)

            if not data_all or not hierarchy_result:
                continue

            signal = self._generate_signal(hierarchy_result, data_all, current_price)
            if signal and signal.is_tradeable():
                has_active = any(c.status == TradeStatus.OPEN and c.direction == signal.direction for c in self._clusters)
                if has_active:
                    for cluster in self._clusters:
                        if cluster.status == TradeStatus.OPEN and cluster.direction == signal.direction:
                            if self.pyramid_mgr.can_add_leg(cluster, signal.entry_price, cluster.avg_entry_price(), cluster.collective_sl):
                                remaining = self.daily_loss.remaining_budget_amount()
                                sym = signal.symbol
                                is_eur = "EUR" in sym
                                contract_sz = 100000 if is_eur else 100
                                tick_sz = 0.00001 if is_eur else 0.01
                                lot = self.sizer.calculate_lot_size(
                                    account=account, entry_price=current_price, sl_price=signal.sl_price,
                                    direction=signal.direction, point_value=1.0, contract_size=contract_sz,
                                    tick_size=tick_sz, remaining_budget=remaining,
                                )
                                if lot > 0:
                                    leg = TradeLeg(
                                        direction=signal.direction, entry_price=current_price, lot_size=lot,
                                        symbol=signal.symbol, sl_price=signal.sl_price, tp_price=signal.tp_price,
                                        open_time=signal.timestamp, status=TradeStatus.OPEN,
                                    )
                                    cluster.legs.append(leg)
                                    cluster.collective_sl = cluster.avg_entry_price()
                else:
                    cluster = self._execute_backtest_order(signal, account, data_all, i, current_price)
                    if cluster:
                        self._clusters.append(cluster)

            for cluster in list(self._clusters):
                if cluster.status != TradeStatus.OPEN:
                    continue
                m1_slice = TypeSliceData(m1, i, i + 1)
                data_all_slice = {
                    "M1": m1_slice, "M5": m5, "M15": m15,
                }
                actions = self._manage_backtest_exits(cluster, data_all_slice, i, current_price)
                for action in actions:
                    self.trades.append(action)

        return self._report()

    @staticmethod
    def _convert_dicts(data: dict) -> Dict[str, TimeframeData]:
        result = {}
        for tf, d in data.items():
            times = [datetime.fromisoformat(t) if isinstance(t, str) else t for t in d.get("time", [])]
            result[tf] = TimeframeData(
                tf=tf,
                time=times,
                open=d.get("open", []),
                high=d.get("high", []),
                low=d.get("low", []),
                close=d.get("close", []),
                tick_volume=d.get("tick_volume", []) or d.get("volume", []),
                spread=d.get("spread", []),
            )
        return result

    def _slice_data(self, data: Dict[str, TimeframeData], tf: str, idx: int, current_time: Optional[datetime] = None) -> Optional[TimeframeData]:
        if tf not in data:
            return None
        src = data[tf]
        required = 100
        if current_time is not None and src.time:
            import bisect
            end = bisect.bisect_right(src.time, current_time)
            if end < 15:
                return None
            start = max(0, end - required)
            return TimeframeData(
                tf=tf,
                time=src.time[start:end],
                open=src.open[start:end],
                high=src.high[start:end],
                low=src.low[start:end],
                close=src.close[start:end],
                tick_volume=src.tick_volume[start:end],
                spread=src.spread[start:end],
            )
        ratio = {"M5": 1, "M15": 3, "M30": 6, "H1": 12, "H4": 48}.get(tf, 1)
        required = 200
        start = max(0, idx // ratio - required)
        end = idx // ratio
        if end >= len(src.close) or end <= start:
            return None
        return TimeframeData(
            tf=tf,
            time=src.time[start:end],
            open=src.open[start:end],
            high=src.high[start:end],
            low=src.low[start:end],
            close=src.close[start:end],
            tick_volume=src.tick_volume[start:end],
            spread=src.spread[start:end],
        )

    def _generate_signal(self, hierarchy_result: dict, data_all: dict, price: float) -> Optional[Signal]:
        if hierarchy_result.get("is_sideways"):
            return None
        allowed = hierarchy_result.get("allowed_direction")
        if allowed is None:
            return None
        direction = TradeDirection.BUY if allowed == "bullish" else TradeDirection.SELL
        entry_tf = hierarchy_result.get("entry_tier", "M15")
        entry_data = data_all.get(entry_tf) or data_all.get("M15")
        if not entry_data:
            return None
        momentum_ok, _ = self.trigger.check_momentum_continuation(entry_data, direction)
        if not momentum_ok:
            return None

        atr_val = atr(entry_data.high, entry_data.low, entry_data.close, 14) or 0
        sl = self.exit_mgr.calc_atr_sl(entry_data, direction, entry_tf)
        tp = self.exit_mgr.calc_structure_tp(entry_data, direction, price, atr_val)

        zone = hierarchy_result.get("m15_zone")
        in_zone = self.zone.price_in_zone(price, zone) if zone else False

        signal = Signal(
            symbol=getattr(self.cfg.trading, "symbol", "XAUUSD"),
            direction=direction,
            grade=SignalGrade.B,
            entry_tf=entry_tf,
            h4_bias=hierarchy_result.get("h4_bias", Bias.NEUTRAL),
            h1_bias=hierarchy_result.get("h1_bias", Bias.NEUTRAL),
            m15_bias=hierarchy_result.get("m15_bias", Bias.NEUTRAL),
            m5_bias=hierarchy_result.get("m5_bias", Bias.NEUTRAL),
            regime=hierarchy_result.get("regime", Regime.RANGING),
            entry_price=price,
            sl_price=sl,
            tp_price=tp,
            atr_value=atr_val,
            zone_high=zone[1] if zone else 0,
            zone_low=zone[0] if zone else 0,
            score=hierarchy_result.get("alignment_score", 0),
        )
        signal.grade = self.scorer.grade(signal)
        return signal

    def _execute_backtest_order(self, signal: Signal, account: AccountInfo,
                                data_all: dict, idx: int, price: float) -> Optional[PyraCluster]:
        remaining = self.daily_loss.remaining_budget_amount()
        sym = signal.symbol
        is_eur = "EUR" in sym
        contract_sz = 100000 if is_eur else 100
        tick_sz = 0.00001 if is_eur else 0.01

        lot = self.sizer.calculate_lot_size(
            account=account, entry_price=price, sl_price=signal.sl_price,
            direction=signal.direction, point_value=1.0, contract_size=contract_sz,
            tick_size=tick_sz,
            remaining_budget=remaining,
        )
        if lot <= 0:
            return None
        signal.lot_size = lot
        cluster = PyraCluster(
            signal_id=signal.id, direction=signal.direction, entry_tf=signal.entry_tf,
            symbol=signal.symbol,
            collective_sl=signal.sl_price, open_time=signal.timestamp, status=TradeStatus.OPEN,
        )
        leg = TradeLeg(
            direction=signal.direction, entry_price=price, lot_size=lot,
            symbol=signal.symbol,
            sl_price=signal.sl_price, tp_price=signal.tp_price,
            open_time=signal.timestamp, status=TradeStatus.OPEN,
        )
        cluster.legs.append(leg)
        self.daily_loss.register_trade()
        return cluster

    def _manage_backtest_exits(self, cluster: PyraCluster, data_all: dict,
                               idx: int, price: float) -> List[dict]:
        actions = []
        if cluster.direction == TradeDirection.BUY:
            cluster.highest_price = max(cluster.highest_price, price)
        else:
            cluster.lowest_price = min(cluster.lowest_price, price)

        if self.partial_close.check_partial_tp(cluster, price):
            for leg in cluster.legs:
                if leg.status == TradeStatus.OPEN:
                    leg.lot_size *= 0.5
            cluster.breakeven_activated = True
            cluster.collective_sl = cluster.avg_entry_price()
            actions.append({"action": "partial_tp", "price": price, "cluster": cluster.cluster_id})

        for leg in cluster.legs:
            if leg.status != TradeStatus.OPEN:
                continue
            if leg.direction == TradeDirection.BUY:
                if price <= leg.sl_price:
                    leg.status = TradeStatus.CLOSED
                    leg.exit_price = price
                    leg.exit_reason = ExitReason.STOP_LOSS
                    actions.append({"action": "sl_hit", "price": price})
                elif price >= leg.tp_price:
                    leg.status = TradeStatus.CLOSED
                    leg.exit_price = price
                    leg.exit_reason = ExitReason.TAKE_PROFIT
                    actions.append({"action": "tp_hit", "price": price})
            else:
                if price >= leg.sl_price:
                    leg.status = TradeStatus.CLOSED
                    leg.exit_price = price
                    leg.exit_reason = ExitReason.STOP_LOSS
                    actions.append({"action": "sl_hit", "price": price})
                elif price <= leg.tp_price:
                    leg.status = TradeStatus.CLOSED
                    leg.exit_price = price
                    leg.exit_reason = ExitReason.TAKE_PROFIT
                    actions.append({"action": "tp_hit", "price": price})
        open_legs = [l for l in cluster.legs if l.status == TradeStatus.OPEN]
        if not open_legs:
            cluster.status = TradeStatus.CLOSED
        return actions

    def _close_all(self, price: float):
        for cluster in self._clusters:
            if cluster.status == TradeStatus.OPEN:
                for leg in cluster.legs:
                    if leg.status == TradeStatus.OPEN:
                        leg.status = TradeStatus.CLOSED
                        leg.exit_price = price
                        leg.exit_reason = ExitReason.EQUITY_KILL
                cluster.status = TradeStatus.CLOSED

    def _compute_equity(self, balance: float, current_price: float, idx: int) -> float:
        unrealized = 0.0
        for cluster in self._clusters:
            if cluster.status == TradeStatus.OPEN:
                sym = getattr(cluster, "symbol", "XAUUSD")
                is_eur = "EUR" in sym
                contract_sz = 100000 if is_eur else 100
                tick_sz = 0.00001 if is_eur else 0.01
                unrealized += cluster.unrealized_pnl(current_price, 1.0, contract_sz, tick_size=tick_sz)
        return balance + unrealized

    def _report(self) -> dict:
        total_trades = 0
        wins = 0
        losses = 0
        total_pnl = 0.0
        gross_profit = 0.0
        gross_loss = 0.0
        for cluster in self._clusters:
            for leg in cluster.legs:
                if leg.status == TradeStatus.CLOSED and leg.exit_price:
                    total_trades += 1
                    sym = getattr(leg, "symbol", "XAUUSD")
                    is_eur = "EUR" in sym
                    contract_sz = 100000 if is_eur else 100
                    tick_sz = 0.00001 if is_eur else 0.01
                    if leg.direction == TradeDirection.BUY:
                        diff = leg.exit_price - leg.entry_price
                    else:
                        diff = leg.entry_price - leg.exit_price

                    pnl = (diff / tick_sz) * 1.0 * leg.lot_size if tick_sz > 0 else diff * leg.lot_size * contract_sz
                    leg.pnl = round(pnl, 2)
                    total_pnl += pnl
                    if pnl > 0:
                        wins += 1
                        gross_profit += pnl
                    elif pnl < 0:
                        losses += 1
                        gross_loss += abs(pnl)

        max_dd = self.max_dd.current_dd_pct
        init_bal = self.cfg.trading.backtest_initial_balance
        final_bal = init_bal + total_pnl
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

        return {
            "initial_balance": init_bal,
            "final_balance": round(final_bal, 2),
            "total_pnl": round(total_pnl, 2),
            "return_pct": round((total_pnl / init_bal) * 100, 2) if init_bal else 0.0,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total_trades * 100, 2) if total_trades else 0.0,
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_dd, 2),
            "clusters": len(self._clusters),
        }


class TypeSliceData(TimeframeData):
    def __init__(self, src: TimeframeData, start: int, end: int):
        super().__init__(
            tf=src.tf,
            time=src.time[start:end],
            open=src.open[start:end],
            high=src.high[start:end],
            low=src.low[start:end],
            close=src.close[start:end],
            tick_volume=src.tick_volume[start:end],
            spread=src.spread[start:end],
        )
