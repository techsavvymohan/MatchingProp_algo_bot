#!/usr/bin/env python3
import argparse
import logging
import signal
import sys
import time
from datetime import datetime
from typing import Dict, Optional

from .config import Config
from .logger import setup_logging
from .models import ExitReason, Signal, SignalGrade, TradeDirection, Bias, Regime, Session
from .utils.time_utils import current_session
from .broker.mt5_connector import MT5Connector
from .broker.account import AccountManager
from .data.ohlcv import MultiTFData
from .data.spread import SpreadTracker
from .data.economic_calendar import EconomicCalendar
from .filters.session_filter import SessionFilter
from .filters.spread_filter import SpreadFilter
from .filters.news_filter import NewsFilter
from .data.tradingview_feed import TradingViewFeed
from .indicators.atr import atr
from .indicators.moving_averages import ema
from .strategy.bias_detector import BiasDetector
from .strategy.sideways_detector import SidewaysDetector
from .strategy.signal_scorer import SignalScorer
from .strategy.timeframe_hierarchy import TimeframeHierarchy
from .strategy.trigger import TriggerDetector
from .strategy.zone_detector import ZoneDetector
from .risk.daily_loss import DailyLossTracker
from .risk.max_dd import MaxDDTracker
from .risk.position_sizer import PositionSizer
from .risk.pyramid_manager import PyramidManager
from .order.entry import OrderEntry
from .order.exit import ExitManager
from .order.partial_close import PartialCloseManager
from .trade.trade_manager import TradeManager
from .trade.cluster import ClusterManager
from .state.persistence import StatePersistence

log = logging.getLogger("xauusd_bot.main")


class XAUUSDBot:
    def __init__(self, config: Config):
        self.cfg = config
        self.running = False
        self._setup_components()
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _setup_components(self):
        tc = self.cfg.trading
        self.connector = MT5Connector(self.cfg.mt5)
        self.account = AccountManager(self.connector)

        # Multi-symbol configuration
        self.symbols = getattr(tc, "symbols", None) or [tc.symbol]
        self.data_feeds: Dict[str, MultiTFData] = {s: MultiTFData(self.connector, s) for s in self.symbols}
        self.data = self.data_feeds.get(tc.symbol) or next(iter(self.data_feeds.values()))

        self.spread_trackers: Dict[str, SpreadTracker] = {s: SpreadTracker(tc.spread_lookback_bars) for s in self.symbols}
        self.spread = self.spread_trackers.get(tc.symbol) or next(iter(self.spread_trackers.values()))

        self.spread_filters: Dict[str, SpreadFilter] = {
            s: SpreadFilter(self.spread_trackers[s], tc.max_spread_multiplier) for s in self.symbols
        }
        self.spread_filter = self.spread_filters.get(tc.symbol) or next(iter(self.spread_filters.values()))

        self.calendar = EconomicCalendar()
        self.session_filter = SessionFilter(
            tc.session_london_open, tc.session_london_close,
            tc.session_ny_open, tc.session_ny_close,
            enabled=tc.enable_session_filter,
        )
        self.news_filter = NewsFilter(self.calendar, tc.news_block_before_minutes, tc.news_block_after_minutes)
        self.tv_feed = TradingViewFeed(
            cache_ttl_seconds=tc.tradingview_cache_ttl,
            enabled=tc.enable_tradingview,
        )

        self.sideways_detector = SidewaysDetector(
            chop_threshold=tc.sideways_chop_threshold,
            adx_threshold=tc.sideways_adx_threshold,
            bandwidth_squeeze_pct=tc.sideways_bandwidth_squeeze_pct,
        ) if tc.enable_sideways_filter else None

        self.bias_detector = BiasDetector(
            tc.ema_fast, tc.ema_medium, tc.ema_slow,
            tc.rsi_period, tc.rsi_mid_upper, tc.rsi_mid_lower,
        )
        self.zone_detector = ZoneDetector(
            tc.vwap_period, tc.min_structure_swing_bars, tc.max_structure_swing_bars,
        )
        self.hierarchy = TimeframeHierarchy(
            self.bias_detector,
            self.zone_detector,
            sideways_detector=self.sideways_detector,
            session_agnostic=not tc.enable_session_filter,
        )
        self.scorer = SignalScorer(tc.signal_score_a_min, tc.signal_score_b_min)
        self.trigger = TriggerDetector(
            tc.ema_fast, tc.rsi_period, tc.rsi_mid_upper, tc.rsi_mid_lower,
        )
        self.exit_mgr = ExitManager(tc)
        self.sizer = PositionSizer(tc.pyramid_initial_risk_pct, tc.max_pyramid_entries)
        self.partial_close = PartialCloseManager(tc.partial_take_profit_r, tc.partial_close_pct)
        self.daily_loss = DailyLossTracker(
            tc.daily_loss_limit_pct, tc.daily_loss_buffer_pct,
            tc.broker_daily_reset_hour, tc.broker_daily_reset_tz,
        )
        self.max_dd = MaxDDTracker(tc.max_dd_limit_pct, tc.max_dd_buffer_pct)
        self.pyramid_mgr = PyramidManager(tc.max_pyramid_entries, tc.pyramid_add_trigger_r)
        self.order_entry = OrderEntry(self.connector, tc)
        self.trade_mgr = TradeManager(
            self.order_entry, self.exit_mgr, self.partial_close,
            self.pyramid_mgr, self.sizer, self.daily_loss, self.max_dd,
        )
        self.cluster_mgr = ClusterManager()
        self.persistence = StatePersistence(tc.state_db_path, tc.trade_log_path)

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d — shutting down", signum)
        self.running = False

    def start(self):
        log.info("Starting Multi-Symbol Quant Profit Digger Bot v%s (Symbols: %s)",
                 __import__("xauusd_bot").__version__, ", ".join(self.symbols))
        if not self.connector.connect():
            log.critical("Failed to connect to MT5")
            return
        log.info("MT5 connected successfully")

        self.persistence.connect()
        saved_state = self.persistence.load_daily_state()
        if saved_state:
            self.daily_loss.state = saved_state
            log.info("Restored daily state: date=%s start_equity=%.2f",
                     saved_state.date, saved_state.start_equity)

        self.news_filter.update_fetch()
        self.running = True
        poll_s = self.cfg.trading.poll_interval_ms / 1000.0
        last_data_update: Dict[str, float] = {s: 0.0 for s in self.symbols}
        last_calendar_update = 0.0

        while self.running:
            try:
                now = time.time()
                account_info = self.account.refresh()
                if not account_info:
                    time.sleep(poll_s)
                    continue

                self.daily_loss.update(account_info)
                self.max_dd.update(account_info.equity)

                if self.daily_loss.kill_switch_engaged() or self.max_dd.kill_switch_engaged():
                    self._close_all_positions("risk_limit")
                    log.info("Risk limit reached — waiting")
                    time.sleep(poll_s * 10)
                    continue

                if now - last_calendar_update > 3600:
                    self.news_filter.update_fetch()
                    last_calendar_update = now

                sess_ok, sess_name = self.session_filter.check()
                if not sess_ok:
                    time.sleep(poll_s * 5)
                    continue

                # Iterate through all configured symbols (XAUUSD & EURUSD)
                for symbol in self.symbols:
                    data_feed = self.data_feeds.get(symbol)
                    if not data_feed:
                        continue

                    if now - last_data_update.get(symbol, 0.0) > 5.0:
                        data_feed.update_all()
                        last_data_update[symbol] = now

                    spread_tracker = self.spread_trackers.get(symbol)
                    if spread_tracker:
                        spread_tracker.update(self.account.current_spread(symbol))

                    data_all = data_feed.all_tfs()
                    if not data_all:
                        continue

                    hierarchy_result = self.hierarchy.evaluate(data_all, current_session())
                    signal = self._build_signal(hierarchy_result, data_all, symbol)
                    if signal:
                        self._process_signal(signal, account_info, data_all)

                    self._manage_active_trades(data_all, symbol)

                self.persistence.save_daily_state(self.daily_loss.state)
                time.sleep(poll_s)

            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                log.exception("Unhandled error in main loop: %s", e)
                time.sleep(poll_s * 5)

        self._shutdown()

    def _build_signal(self, hierarchy_result: dict, data_all: dict, symbol: str = "XAUUSD") -> Optional[Signal]:
        # Sideways rejection
        if hierarchy_result.get("is_sideways"):
            log.debug("[%s] Sideways condition rejected: %s", symbol, hierarchy_result.get("sideways_reason"))
            return None

        allowed = hierarchy_result.get("allowed_direction")
        if allowed is None:
            return None

        direction = TradeDirection.BUY if allowed == "bullish" else TradeDirection.SELL
        entry_tf = hierarchy_result.get("entry_tier", "M15")
        entry_data = data_all.get(entry_tf)
        if not entry_data or not entry_data.close:
            return None
        current_price = entry_data.close[-1]
        wick_break_ok, _ = self.trigger.check_micro_structure_break(entry_data, direction)
        momentum_ok, _ = self.trigger.check_momentum_continuation(entry_data, direction)
        zone = hierarchy_result.get("m15_zone")
        in_zone = self.zone_detector.price_in_zone(current_price, zone) if zone else False

        if entry_tf == "M1":
            if not (wick_break_ok and momentum_ok and in_zone):
                return None
        elif entry_tf == "M5":
            if not momentum_ok:
                return None
        elif entry_tf == "M15":
            if not in_zone:
                return None

        spread_filter = self.spread_filters.get(symbol, self.spread_filter)
        spread_ok, spread_msg = spread_filter.check()
        if not spread_ok:
            log.info("[%s] Spread filter blocked: %s", symbol, spread_msg)
            return None

        news_ok, news_msg = self.news_filter.check(symbol)
        if not news_ok:
            log.info("[%s] News filter blocked: %s", symbol, news_msg)
            return None

        tv_rec = ""
        if self.tv_feed and self.tv_feed.enabled:
            tv_rec = self.tv_feed.get_recommendation(symbol, self.cfg.trading.tradingview_timeframe)
            tv_sideways, tv_reason = self.tv_feed.is_sideways(symbol, self.cfg.trading.tradingview_timeframe)
            if tv_sideways:
                log.info("[%s] TradingView sideways filter blocked: %s", symbol, tv_reason)
                return None

        atr_val = atr(entry_data.high, entry_data.low, entry_data.close, self.cfg.trading.atr_period) or 0
        sl = self.exit_mgr.calc_atr_sl(entry_data, direction, entry_tf)
        tp = self.exit_mgr.calc_structure_tp(entry_data, direction, current_price, atr_val)

        signal = Signal(
            symbol=symbol,
            direction=direction,
            entry_tf=entry_tf,
            h4_bias=hierarchy_result.get("h4_bias", Bias.NEUTRAL),
            h1_bias=hierarchy_result.get("h1_bias", Bias.NEUTRAL),
            m15_bias=hierarchy_result.get("m15_bias", Bias.NEUTRAL),
            m5_bias=hierarchy_result.get("m5_bias", Bias.NEUTRAL),
            regime=hierarchy_result.get("regime", Regime.RANGING),
            session=current_session(),
            entry_price=current_price,
            sl_price=sl,
            tp_price=tp,
            atr_value=atr_val,
            zone_high=zone[1] if zone else 0,
            zone_low=zone[0] if zone else 0,
            score=hierarchy_result.get("alignment_score", 0),
            m15_zone=zone,
            ao_saucer=hierarchy_result.get("ao_saucer", False),
            ha_trend=hierarchy_result.get("ha_trend", ""),
            tradingview_recommendation=tv_rec,
        )
        signal.grade = self.scorer.grade(signal)
        if signal.grade == SignalGrade.C:
            return None
        return signal

    def _process_signal(self, signal: Signal, account_info, data_all: dict):
        symbol = signal.symbol
        max_lot = self.account.max_lot(symbol)
        min_lot = self.account.min_lot(symbol)
        lot_step = self.account.lot_step(symbol)
        point_val = self.account.point_value(symbol)
        contract_sz = self.account.contract_size(symbol)

        if self.cluster_mgr.has_active_for_direction(signal.direction, symbol=symbol):
            for cluster in self.cluster_mgr.active_clusters_for_direction(signal.direction, symbol=symbol):
                if self.pyramid_mgr.can_add_leg(cluster, signal.entry_price, cluster.avg_entry_price(), cluster.collective_sl):
                    self.trade_mgr.manage_pyramid_add(signal, cluster, account_info, data_all,
                                                       point_val, contract_sz,
                                                       min_lot, lot_step, max_lot)
            return

        cluster = self.trade_mgr.execute_signal(signal, account_info, data_all,
                                                 point_val, contract_sz,
                                                 min_lot, lot_step, max_lot)
        if cluster:
            self.cluster_mgr.add(cluster)
            log.info("New cluster opened [%s]: %s %s %.4f (grade=%s)",
                     symbol, signal.direction.value, signal.entry_tf, signal.entry_price, signal.grade.value)

    def _manage_active_trades(self, data_all: dict, symbol: str = ""):
        clusters = self.cluster_mgr.active_clusters_for_symbol(symbol) if symbol else list(self.cluster_mgr.active)
        for cluster in clusters:
            actions = self.trade_mgr.manage_exits(cluster, data_all)
            for action in actions:
                log.info("Exit action [%s]: %s cluster=%s", getattr(cluster, "symbol", ""), action.get("action"), cluster.cluster_id[:8])

    def _close_all_positions(self, reason: str):
        for cluster in list(self.cluster_mgr.active):
            sym = getattr(cluster, "symbol", "XAUUSD")
            tick = self.connector.symbol_info_tick(sym)
            price = (tick.bid + tick.ask) / 2 if tick else 0.0
            self.trade_mgr._close_cluster_positions(cluster, price, ExitReason.EQUITY_KILL)
            log.warning("[%s] All positions closed: %s at %.4f", sym, reason, price)

    def _shutdown(self):
        log.info("Shutting down...")
        self.connector.disconnect()
        self.persistence.close()


def main():
    parser = argparse.ArgumentParser(description="XAUUSD Digger Bot")
    parser.add_argument("--env", type=str, default=None, help="Path to .env file")
    parser.add_argument("--backtest", type=str, default=None, help="Path to backtest data JSON")
    parser.add_argument("--config", type=str, default=None, help="Path to config JSON")
    parser.add_argument("--balance", type=float, default=None, help="Initial backtest balance in USD (e.g. 10000)")
    parser.add_argument("--symbol", type=str, default=None, help="Symbol to backtest (e.g. XAUUSD, EURUSD)")
    parser.add_argument("--start", type=str, default=None, help="Start date filter YYYY-MM-DD (e.g. 2026-07-01)")
    parser.add_argument("--end", type=str, default=None, help="End date filter YYYY-MM-DD (e.g. 2026-07-31)")
    args = parser.parse_args()

    config = Config.load(args.env, args.config)
    setup_logging(config.trading.logging_level, config.trading.log_file,
                  config.trading.telegram_token, config.trading.telegram_chat_id)

    if args.backtest:
        from .backtesting.engine import BacktestEngine
        from .backtesting.report import print_report
        import json
        from datetime import datetime, timezone
        with open(args.backtest) as f:
            raw = json.load(f)

        sym = args.symbol
        if not sym:
            if "eur" in args.backtest.lower():
                sym = "EURUSD"
            elif "xau" in args.backtest.lower():
                sym = "XAUUSD"

        dt_start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc) if args.start else None
        dt_end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc) if args.end else None

        engine = BacktestEngine(config, initial_balance=args.balance, symbol=sym, start_date=dt_start, end_date=dt_end)
        results = engine.run(raw)
        print_report(results)
        return

    bot = XAUUSDBot(config)
    bot.start()


if __name__ == "__main__":
    main()
