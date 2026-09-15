import copy
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
            self.cfg.trading.initial_account_balance = initial_balance
        self.symbol = symbol or getattr(self.cfg.trading, "symbol", "XAUUSD")
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
        init_bal = initial_balance if initial_balance is not None else getattr(self.cfg.trading, "initial_account_balance", self.cfg.trading.backtest_initial_balance)
        self.sizer = PositionSizer(
            config.trading.pyramid_initial_risk_pct,
            config.trading.max_pyramid_entries,
            enable_profit_compounding=getattr(config.trading, "enable_profit_compounding", False),
            initial_balance=init_bal,
            compounding_cap_mult=getattr(config.trading, "compounding_cap_mult", 2.0),
        )
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
        self._pending_fvg_orders: List[dict] = []
        self._session_trades_count: int = 0
        self._london_trades_today: int = 0
        self._ny_trades_today: int = 0
        self._current_session_date: Optional[object] = None
        self._last_exit_time: Optional[datetime] = None
        self._ablation_counters: Dict[str, int] = defaultdict(
            int,
            sweeps_detected=0,
            reclaims_confirmed=0,
            displacement_confirmed=0,
            mss_confirmed=0,
            fvg_created=0,
            orders_placed=0,
            orders_filled=0,
            orders_expired=0,
        )


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
            account.server_time = current_time

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
            if not getattr(self.cfg.trading, "enable_profit_compounding", False):
                account.balance = max(account.balance, account.equity)
            self.daily_loss.update(account)
            self.max_dd.update(account.equity)

            if self.daily_loss.kill_switch_engaged() or self.max_dd.kill_switch_engaged():
                self._close_all(current_price)
                continue

            is_fvg_strat = getattr(self.cfg.trading, "strategy_trigger_type", "momentum") in (
                "xau_liquidity_sweep_fvg_m1", "liquidity_sweep_fvg"
            )

            # Check and track session window & daily session reset
            ecosystem_mode = getattr(self.cfg.trading, "xau_ecosystem_mode", True)
            s_start = getattr(self.cfg.trading, "xau_session_start", "10:00")
            s_end = getattr(self.cfg.trading, "xau_session_end", "11:00")
            s_tz = getattr(self.cfg.trading, "xau_session_timezone", "America/New_York")
            from ..utils.time_utils import is_in_ny_session, to_ny_time
            if ecosystem_mode and current_time is not None:
                h_utc = current_time.hour if hasattr(current_time, "hour") else 0
                m_utc = current_time.minute if hasattr(current_time, "minute") else 0
                is_gold = "XAU" in self.symbol
                is_eur = "EUR" in self.symbol
                if getattr(self.cfg.trading, "xau_strict_killzones", True) and is_gold:
                    # London Cash Open Killzone (07:45-10:30 UTC) & NY Core Killzone (13:30-16:30 UTC)
                    if hasattr(self.cfg.trading, "is_in_xau_london_killzone"):
                        in_london = self.cfg.trading.is_in_xau_london_killzone(current_time)
                    else:
                        in_london = (h_utc == 7 and m_utc >= 45) or (8 <= h_utc < 10) or (h_utc == 10 and m_utc <= 30)
                    in_ny_core = (13 < h_utc < 16) or (h_utc == 13 and m_utc >= 30) or (h_utc == 16 and m_utc <= 30)
                    in_session = in_london or in_ny_core
                elif is_eur:
                    # Dedicated Forex Institutional Session (London + NY overlap, skipping 10:00-12:00 UTC lunch dead zone)
                    if hasattr(self.cfg.trading, "is_in_eur_session"):
                        in_session = self.cfg.trading.is_in_eur_session(current_time)
                    else:
                        eur_start = getattr(self.cfg.trading, "eur_session_start_hour", 7)
                        eur_end = getattr(self.cfg.trading, "eur_session_end_hour", 16)
                        in_session = (eur_start <= h_utc < eur_end)
                        if getattr(self.cfg.trading, "eur_filter_lunch_chop", True):
                            l_start = getattr(self.cfg.trading, "eur_lunch_start_hour", 10)
                            l_end = getattr(self.cfg.trading, "eur_lunch_end_hour", 12)
                            if l_start <= h_utc < l_end:
                                in_session = False
                else:
                    in_ny_core = is_in_ny_session(current_time, s_start, s_end, s_tz)
                    in_london = (7 <= h_utc < 9) if getattr(self.cfg.trading, "xau_enable_london_asian_sweep", True) else False
                    in_session = in_ny_core or in_london
            else:
                in_session = is_in_ny_session(current_time, s_start, s_end, s_tz) if current_time else True

            if current_time:
                ny_date = to_ny_time(current_time, s_tz).date()
                if self._current_session_date != ny_date:
                    self._current_session_date = ny_date
                    self._session_trades_count = 0
                    self._london_trades_today = 0
                    self._ny_trades_today = 0

            # Expire pending orders if outside active session
            if is_fvg_strat and self._pending_fvg_orders and not in_session:
                for po in list(self._pending_fvg_orders):
                    self._pending_fvg_orders.remove(po)
                    self._ablation_counters["orders_expired"] += 1

            if i % 5 == 0 or hierarchy_result is None or data_all is None:
                m5 = self._slice_data(data, "M5", i, current_time)
                m15 = self._slice_data(data, "M15", i, current_time)
                h1 = self._slice_data(data, "H1", i, current_time)
                h4 = self._slice_data(data, "H4", i, current_time)
                if is_fvg_strat:
                    if not m15:
                        continue
                else:
                    if not all([m5, m15, h1, h4]):
                        continue
                m1_start = max(0, i + 1 - 100)
                m1_slice = TimeframeData(
                    tf="M1",
                    time=m1.time[m1_start:i+1],
                    open=m1.open[m1_start:i+1],
                    high=m1.high[m1_start:i+1],
                    low=m1.low[m1_start:i+1],
                    close=m1.close[m1_start:i+1],
                    tick_volume=m1.tick_volume[m1_start:i+1],
                    spread=m1.spread[m1_start:i+1],
                )
                data_all = {"M1": m1_slice, "M5": m5, "M15": m15, "H1": h1, "H4": h4}
                hierarchy_result = self.hierarchy.evaluate(data_all, Session.LONDON)

            if not data_all or not hierarchy_result:
                continue

            # Ensure M1 in data_all is always updated to the current bar i
            m1_start = max(0, i + 1 - 100)
            data_all["M1"] = TimeframeData(
                tf="M1",
                time=m1.time[m1_start:i+1],
                open=m1.open[m1_start:i+1],
                high=m1.high[m1_start:i+1],
                low=m1.low[m1_start:i+1],
                close=m1.close[m1_start:i+1],
                tick_volume=m1.tick_volume[m1_start:i+1],
                spread=m1.spread[m1_start:i+1],
            )

            # Check and advance pending FVG limit orders
            if is_fvg_strat and self._pending_fvg_orders:
                for po in list(self._pending_fvg_orders):
                    po["bars_active"] += 1
                    filled = False
                    sig = po["signal"]
                    fvg_high = getattr(sig, "fvg_high", 0.0)
                    fvg_low = getattr(sig, "fvg_low", 0.0)
                    o_bar = m1.open[i]
                    h_bar = m1.high[i]
                    l_bar = m1.low[i]
                    c_bar = m1.close[i]

                    fvg_tol_pct = getattr(self.cfg.trading, "fvg_adaptive_retest_tolerance_pct", 0.25)
                    fvg_span = abs(fvg_high - fvg_low) if (fvg_high > 0 and fvg_low > 0) else 0.0
                    tol = max(0.25, fvg_span * fvg_tol_pct) if "XAU" in self.symbol else max(0.00008, fvg_span * fvg_tol_pct)

                    if getattr(self.cfg.trading, "xau_require_retest", True):
                        touched = po.get("retest_touched", False)
                        if po["direction"] == TradeDirection.BUY:
                            if l_bar <= (po["limit_price"] + tol):
                                touched = True
                                po["retest_touched"] = True
                        else:
                            if h_bar >= (po["limit_price"] - tol):
                                touched = True
                                po["retest_touched"] = True

                        if touched:
                            inv_buf = 0.5 * tol
                            if po["direction"] == TradeDirection.BUY:
                                if fvg_low > 0 and c_bar < (fvg_low - inv_buf):
                                    self._pending_fvg_orders.remove(po)
                                    self._ablation_counters["orders_expired"] += 1
                                    continue
                                lower_wick = min(o_bar, c_bar) - l_bar
                                body = abs(c_bar - o_bar)
                                bar_range = h_bar - l_bar
                                if (c_bar > o_bar or lower_wick >= 0.4 * body or (bar_range > 0 and (c_bar - l_bar) / bar_range >= 0.5)) and c_bar >= (fvg_low - inv_buf):
                                    filled = True
                            else:
                                if fvg_high > 0 and c_bar > (fvg_high + inv_buf):
                                    self._pending_fvg_orders.remove(po)
                                    self._ablation_counters["orders_expired"] += 1
                                    continue
                                upper_wick = h_bar - max(o_bar, c_bar)
                                body = abs(c_bar - o_bar)
                                bar_range = h_bar - l_bar
                                if (c_bar < o_bar or upper_wick >= 0.4 * body or (bar_range > 0 and (h_bar - c_bar) / bar_range >= 0.5)) and c_bar <= (fvg_high + inv_buf):
                                    filled = True
                    elif getattr(self.cfg.trading, "xau_enable_pre_fill_guard", True) and (fvg_high > 0 or fvg_low > 0):
                        if po["direction"] == TradeDirection.SELL:
                            if m1.high[i] >= po["limit_price"]:
                                if fvg_high > 0 and m1.close[i] > fvg_high:
                                    # Runaway green bar blew straight through FVG resistance -> cancel!
                                    self._pending_fvg_orders.remove(po)
                                    self._ablation_counters["orders_expired"] += 1
                                    continue
                                else:
                                    filled = True
                        else:
                            if m1.low[i] <= po["limit_price"]:
                                if fvg_low > 0 and m1.close[i] < fvg_low:
                                    # Runaway red bar blew straight through FVG support -> cancel!
                                    self._pending_fvg_orders.remove(po)
                                    self._ablation_counters["orders_expired"] += 1
                                    continue
                                else:
                                    filled = True
                    else:
                        if po["direction"] == TradeDirection.BUY:
                            if m1.low[i] <= po["limit_price"]:
                                filled = True
                        else:
                            if m1.high[i] >= po["limit_price"]:
                                filled = True

                    if filled:
                        sig = po["signal"]
                        pyra_target = po.get("pyramid_cluster")
                        if pyra_target and pyra_target.status == TradeStatus.OPEN:
                            # Add as compound pyramid scale-in leg to existing runner cluster
                            leg = TradeLeg(
                                direction=sig.direction, entry_price=po["limit_price"], lot_size=po["lot_size"],
                                symbol=sig.symbol, sl_price=pyra_target.collective_sl, tp_price=po["tp_price"],
                                open_time=current_time, status=TradeStatus.OPEN,
                            )
                            pyra_target.legs.append(leg)
                            self._ablation_counters["pyramid_legs_added"] = self._ablation_counters.get("pyramid_legs_added", 0) + 1
                        else:
                            cluster = PyraCluster(
                                signal_id=sig.id, direction=sig.direction, entry_tf="M1",
                                symbol=sig.symbol, collective_sl=po["sl_price"],
                                open_time=current_time, status=TradeStatus.OPEN,
                            )
                            leg = TradeLeg(
                                direction=sig.direction, entry_price=po["limit_price"], lot_size=po["lot_size"],
                                symbol=sig.symbol, sl_price=po["sl_price"], tp_price=po["tp_price"],
                                open_time=current_time, status=TradeStatus.OPEN,
                            )
                            cluster.legs.append(leg)
                            self._clusters.append(cluster)
                        self._session_trades_count += 1
                        if ecosystem_mode and current_time is not None:
                            h_utc = current_time.hour if hasattr(current_time, "hour") else 0
                            lon_cutoff = 10 if "EUR" in self.symbol else 9
                            if 7 <= h_utc < lon_cutoff:
                                self._london_trades_today += 1
                            else:
                                self._ny_trades_today += 1
                        self._ablation_counters["orders_filled"] += 1
                        self.daily_loss.register_trade()
                        self._pending_fvg_orders.remove(po)
                    elif po["bars_active"] >= po["max_bars"]:
                        self._pending_fvg_orders.remove(po)
                        self._ablation_counters["orders_expired"] += 1

            signal = self._generate_signal(hierarchy_result, data_all, current_price, current_time=current_time)
            if signal and signal.is_tradeable():
                if is_fvg_strat:
                    enable_pyra = getattr(self.cfg.trading, "enable_fvg_pyramiding", True)
                    open_cluster = next((c for c in self._clusters if c.status == TradeStatus.OPEN and c.direction == signal.direction and getattr(c, "symbol", "") == signal.symbol), None)
                    can_pyramid = False
                    if enable_pyra and open_cluster and getattr(open_cluster, "breakeven_activated", False) and len(open_cluster.legs) < getattr(self.cfg.trading, "max_pyramid_entries", 3):
                        can_pyramid = True

                    has_open = any(c.status == TradeStatus.OPEN for c in self._clusters)
                    allow_entry = (not has_open) or can_pyramid
                    max_pending = getattr(self.cfg.trading, "max_concurrent_pending_orders", 2)
                    has_pending = len(self._pending_fvg_orders) >= max_pending
                    max_sess_trades = getattr(self.cfg.trading, "xau_max_trades_per_session", 2)
                    max_daily_trades = getattr(self.cfg.trading, "max_daily_trades", 4)
                    cooldown = getattr(self.cfg.trading, "xau_cooldown_minutes", 5)

                    in_cooldown = False
                    if self._last_exit_time:
                        t_diff = (current_time - self._last_exit_time).total_seconds() / 60.0
                        if t_diff < cooldown:
                            in_cooldown = True

                    same_dir_cooldown = False
                    if getattr(self, "_last_sl_time", None) and getattr(self, "_last_sl_direction", None) == signal.direction:
                        t_sl_diff = (current_time - self._last_sl_time).total_seconds() / 60.0
                        if t_sl_diff < cooldown:
                            same_dir_cooldown = True

                    session_limit_reached = False
                    if ecosystem_mode and current_time is not None:
                        h_utc = current_time.hour if hasattr(current_time, "hour") else 0
                        lon_cutoff = 10 if "EUR" in self.symbol else 11
                        if (7 <= h_utc < lon_cutoff) and self._london_trades_today >= max_sess_trades:
                            session_limit_reached = True
                        elif (not (7 <= h_utc < lon_cutoff)) and self._ny_trades_today >= max_sess_trades:
                            session_limit_reached = True

                    # ── El Professor Hidden Guards ─────────────────────────────────────────
                    professor_veto = False
                    is_eur_sig = "EUR" in self.symbol

                    if not is_eur_sig:
                        # Guard 1 (XAU only): London Close Wall — no new entries after 15:45 UTC
                        # Backtested: Win% 41.5%→46.3%, PF 1.54→1.74
                        if getattr(self.cfg.trading, "xau_london_close_guard", True) and current_time is not None:
                            g1_h = getattr(self.cfg.trading, "xau_london_close_cutoff_hour", 15)
                            g1_m = getattr(self.cfg.trading, "xau_london_close_cutoff_min", 45)
                            h_c = current_time.hour if hasattr(current_time, "hour") else 0
                            m_c = current_time.minute if hasattr(current_time, "minute") else 0
                            if h_c > g1_h or (h_c == g1_h and m_c >= g1_m):
                                professor_veto = True
                                log.debug("[%s] G1 London Close Wall: veto at %02d:%02d UTC", self.symbol, h_c, m_c)
                    else:
                        # Guard 2 (EUR only): ATR Flash-Crash Circuit Breaker
                        # Backtested: +$112 PnL, blocks 6 spike entries
                        if getattr(self.cfg.trading, "eur_atr_circuit_breaker", True) and i >= 22:
                            g2_lb = getattr(self.cfg.trading, "eur_atr_spike_lookback", 20)
                            g2_mult = getattr(self.cfg.trading, "eur_atr_spike_mult", 3.0)
                            cur_range = m1.high[i] - m1.low[i]
                            if cur_range > 0 and i >= g2_lb + 2:
                                avg_range = sum(m1.high[j] - m1.low[j] for j in range(i - g2_lb, i)) / g2_lb
                                if avg_range > 0 and (cur_range / avg_range) >= g2_mult:
                                    professor_veto = True
                                    log.debug("[%s] G2 ATR Circuit Breaker: spike ratio %.2f", self.symbol, cur_range / avg_range)

                        # Guard 3 (EUR only): H4 Macro Bias — block counter-H4-trend entries
                        # Backtested: PF 1.56→2.98 (with G4), Win% 55.4%→63.2%
                        if not professor_veto and getattr(self.cfg.trading, "eur_h4_bias_guard", True):
                            h4_slice = self._slice_data(data, "H4", i, current_time)
                            if h4_slice is not None and len(h4_slice.close) >= getattr(self.cfg.trading, "eur_h4_ema_slow", 50) + 5:
                                g3_fast = getattr(self.cfg.trading, "eur_h4_ema_fast", 9)
                                g3_slow = getattr(self.cfg.trading, "eur_h4_ema_slow", 50)
                                h4_cl = list(h4_slice.close[-60:])
                                k_f, k_s = 2.0 / (g3_fast + 1), 2.0 / (g3_slow + 1)
                                ef = es = h4_cl[0]
                                for p in h4_cl[1:]:
                                    ef = p * k_f + ef * (1 - k_f)
                                    es = p * k_s + es * (1 - k_s)
                                if ef > es and signal.direction == TradeDirection.SELL:
                                    professor_veto = True
                                    log.debug("[%s] G3 H4 Bias: BULLISH H4 vetos SELL signal", self.symbol)
                                elif ef < es and signal.direction == TradeDirection.BUY:
                                    professor_veto = True
                                    log.debug("[%s] G3 H4 Bias: BEARISH H4 vetos BUY signal", self.symbol)

                        # Guard 4 (EUR only): 3-Consecutive-Loss Cooldown
                        # Backtested: quality booster inside professor suite
                        if not professor_veto and getattr(self.cfg.trading, "eur_consec_loss_guard", True):
                            if not hasattr(self, "_g4_pause_until"):
                                self._g4_pause_until = None
                                self._g4_consec_losses = 0
                                self._g4_last_day = None
                            if current_time is not None:
                                cur_day = current_time.date() if hasattr(current_time, "date") else None
                                if cur_day != self._g4_last_day:
                                    self._g4_last_day = cur_day
                                    self._g4_consec_losses = 0
                                    self._g4_pause_until = None
                            if self._g4_pause_until and current_time is not None:
                                ct_naive = current_time.replace(tzinfo=None) if getattr(current_time, "tzinfo", None) else current_time
                                pu_naive = self._g4_pause_until.replace(tzinfo=None) if getattr(self._g4_pause_until, "tzinfo", None) else self._g4_pause_until
                                if ct_naive < pu_naive:
                                    professor_veto = True
                                    log.debug("[%s] G4 Cooldown: paused until %s", self.symbol, self._g4_pause_until)
                                else:
                                    self._g4_pause_until = None
                    # ── End Professor Guards ───────────────────────────────────────────────

                    if not professor_veto and allow_entry and not has_pending and self._session_trades_count < max_daily_trades and not session_limit_reached and not in_cooldown and not same_dir_cooldown:
                        remaining = self.daily_loss.remaining_budget_amount()
                        sym = signal.symbol
                        is_eur = "EUR" in sym
                        contract_sz = 100000 if is_eur else 100
                        tick_sz = 0.00001 if is_eur else 0.01
                        risk_scale = 1.0
                        if getattr(self.cfg.trading, "enable_net_beta_gate", True):
                            has_same_usd = any(
                                c.status == TradeStatus.OPEN and getattr(c, "symbol", "") != sym and c.direction == signal.direction
                                for c in self._clusters
                            )
                            if has_same_usd:
                                risk_scale = getattr(self.cfg.trading, "correlated_usd_risk_scale", 0.60)
                        lot = self.sizer.calculate_lot_size(
                            account=account, entry_price=signal.entry_price, sl_price=signal.sl_price,
                            direction=signal.direction, point_value=1.0, contract_size=contract_sz,
                            tick_size=tick_sz, remaining_budget=remaining, risk_scale=risk_scale,
                        )
                        if lot > 0:
                            signal.lot_size = lot
                            if getattr(signal, "setup_type", "") == "OVERLAP_PULLBACK":
                                cluster = PyraCluster(
                                    signal_id=signal.id, direction=signal.direction, entry_tf="M1",
                                    symbol=signal.symbol, collective_sl=signal.sl_price,
                                    open_time=current_time, status=TradeStatus.OPEN,
                                )
                                leg = TradeLeg(
                                    direction=signal.direction, entry_price=signal.entry_price, lot_size=lot,
                                    symbol=signal.symbol, sl_price=signal.sl_price, tp_price=signal.tp_price,
                                    open_time=current_time, status=TradeStatus.OPEN,
                                )
                                cluster.legs.append(leg)
                                cluster.r_distance()
                                self._clusters.append(cluster)
                                self._session_trades_count += 1
                                self._ablation_counters["orders_filled"] += 1
                                self.daily_loss.register_trade()
                            else:
                                max_expiry = self.cfg.trading.get_fvg_expiry_bars(self.symbol) if hasattr(self.cfg.trading, "get_fvg_expiry_bars") else getattr(self.cfg.trading, "xau_retest_max_bars", 8)
                                self._pending_fvg_orders.append({
                                    "signal": signal,
                                    "direction": signal.direction,
                                    "limit_price": signal.entry_price,
                                    "sl_price": signal.sl_price,
                                    "tp_price": signal.tp_price,
                                    "lot_size": lot,
                                    "bars_active": 0,
                                    "max_bars": max_expiry,
                                    "retest_touched": False,
                                    "pyramid_cluster": open_cluster if can_pyramid else None,
                                })
                                self._ablation_counters["orders_placed"] += 1
                else:
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
                m1_start = max(0, i + 1 - 30)
                m1_slice = TypeSliceData(m1, m1_start, i + 1)
                data_all_slice = {
                    "M1": m1_slice, "M5": m5, "M15": m15,
                }
                actions = self._manage_backtest_exits(cluster, data_all_slice, i, current_price)
                for action in actions:
                    self.trades.append(action)

                if cluster.status == TradeStatus.CLOSED:
                    if getattr(self.cfg.trading, "enable_profit_compounding", False):
                        for leg in cluster.legs:
                            if leg.status == TradeStatus.CLOSED and leg.exit_price and not getattr(leg, "_balance_credited", False):
                                leg._balance_credited = True
                                diff = (leg.exit_price - leg.entry_price) if leg.direction == TradeDirection.BUY else (leg.entry_price - leg.exit_price)
                                sym = getattr(leg, "symbol", self.symbol)
                                is_eur = "EUR" in sym
                                contract_sz = 100000 if is_eur else 100
                                tick_sz = 0.00001 if is_eur else 0.01
                                pnl = (diff / tick_sz) * 1.0 * leg.lot_size if tick_sz > 0 else diff * leg.lot_size * contract_sz
                                leg.pnl = round(pnl, 2)
                                account.balance += pnl

                    if ("EUR" in self.symbol) and getattr(self.cfg.trading, "eur_consec_loss_guard", True):
                        cluster_pnl = 0.0
                        for leg in cluster.legs:
                            if leg.status == TradeStatus.CLOSED and leg.exit_price:
                                p_pts = (leg.exit_price - leg.entry_price) if leg.direction == TradeDirection.BUY else (leg.entry_price - leg.exit_price)
                                cluster_pnl += p_pts
                        if cluster_pnl > 0:
                            self._g4_consec_losses = 0
                        else:
                            self._g4_consec_losses = getattr(self, "_g4_consec_losses", 0) + 1
                            if self._g4_consec_losses >= getattr(self.cfg.trading, "eur_consec_loss_max", 3) and current_time is not None:
                                from datetime import timedelta
                                pause_h = getattr(self.cfg.trading, "eur_consec_loss_pause_hours", 2)
                                self._g4_pause_until = current_time + timedelta(hours=pause_h)
                                log.debug("[%s] G4 Cooldown triggered: %d consecutive losses — paused until %s",
                                          self.symbol, self._g4_consec_losses, self._g4_pause_until)

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
            from datetime import timedelta
            tf_minutes = {"M1": 1, "M3": 3, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}.get(tf, 1)
            cutoff = current_time - timedelta(minutes=tf_minutes)
            src_sample = src.time[0]
            if getattr(current_time, "tzinfo", None) is not None and getattr(src_sample, "tzinfo", None) is None:
                cutoff = cutoff.replace(tzinfo=None)
            elif getattr(current_time, "tzinfo", None) is None and getattr(src_sample, "tzinfo", None) is not None:
                cutoff = cutoff.replace(tzinfo=src_sample.tzinfo)

            end = bisect.bisect_right(src.time, cutoff)
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

    def _generate_signal(self, hierarchy_result: dict, data_all: dict, price: float, current_time: Optional[datetime] = None) -> Optional[Signal]:
        trigger_type = getattr(self.cfg.trading, "strategy_trigger_type", "momentum")

        if trigger_type in ("xau_liquidity_sweep_fvg_m1", "liquidity_sweep_fvg"):
            m15_data = data_all.get("M15")
            m1_data = data_all.get("M1")
            if not m15_data or not m1_data or len(m15_data.close) < 15 or len(m1_data.close) < 15:
                return None

            m1_atr = atr(m1_data.high, m1_data.low, m1_data.close, getattr(self.cfg.trading, "xau_atr_period", 14)) or 1.0
            ecosystem_mode = getattr(self.cfg.trading, "xau_ecosystem_mode", True)
            seq = None

            sym = getattr(self.cfg.trading, "symbol", "XAUUSD")
            is_eur = ("EUR" in sym) or (price < 10.0)
            point_val = 0.00001 if is_eur else 0.01

            if ecosystem_mode and current_time is not None:
                from ..utils.time_utils import is_in_ny_session
                s_start = getattr(self.cfg.trading, "xau_session_start", "10:00")
                s_end = getattr(self.cfg.trading, "xau_session_end", "11:00")
                s_tz = getattr(self.cfg.trading, "xau_session_timezone", "America/New_York")
                h_utc = current_time.hour if hasattr(current_time, "hour") else 0
                m_utc = current_time.minute if hasattr(current_time, "minute") else 0

                if getattr(self.cfg.trading, "xau_strict_killzones", True) and ("XAU" in sym):
                    in_ny_core = (13 < h_utc < 16) or (h_utc == 13 and m_utc >= 30) or (h_utc == 16 and m_utc <= 30)
                elif is_eur:
                    in_ny_core = (12 <= h_utc < 16)
                else:
                    in_ny_core = is_in_ny_session(current_time, s_start, s_end, s_tz)

                h1_b = hierarchy_result.get("h1_bias", Bias.NEUTRAL)
                h1_dir = TradeDirection.BUY if h1_b == Bias.BULLISH else (TradeDirection.SELL if h1_b == Bias.BEARISH else None)

                # Tier 1: Flagship NY Core (10:00 - 11:00 AM NY)
                if in_ny_core:
                    seq = self.trigger.detect_xau_scalp_sequence(
                        m15_data=m15_data,
                        m1_data=m1_data,
                        m1_atr=m1_atr,
                        lookback_m15=getattr(self.cfg.trading, "xau_swing_lookback_m15", 20),
                        sequence_window_m1=getattr(self.cfg.trading, "xau_sequence_window_m1", 10),
                        min_atr_mult=getattr(self.cfg.trading, "xau_displacement_atr_mult", 0.60),
                        min_body_ratio=getattr(self.cfg.trading, "xau_displacement_body_ratio", 0.60),
                        mss_lookback=getattr(self.cfg.trading, "xau_mss_lookback_m1", 5),
                        target_r=self.cfg.trading.get_target_r(self.symbol) if hasattr(self.cfg.trading, "get_target_r") else getattr(self.cfg.trading, "xau_target_r", 2.0),
                        point_value=point_val,
                        stops_level_points=getattr(self.cfg.trading, "deviation_points", 10),
                        telemetry=self._ablation_counters,
                        liquidity_source="m15_swings",
                        current_time=current_time,
                        enable_delta_absorption=getattr(self.cfg.trading, "enable_delta_absorption", True),
                        delta_absorption_mode=getattr(self.cfg.trading, "delta_absorption_mode", "soft"),
                        enable_hvn_tp_calibration=getattr(self.cfg.trading, "enable_hvn_tp_calibration", True),
                        min_sl_distance=self.cfg.trading.get_min_sl_distance(self.symbol) if hasattr(self.cfg.trading, "get_min_sl_distance") else 0.0,
                    )
                # Tier 2: London Cash Open Killzone (07:45 - 10:30 UTC) with m15 swing sweeps
                elif (getattr(self.cfg.trading, "xau_enable_london_asian_sweep", True) and (self.cfg.trading.is_in_xau_london_killzone(current_time) if hasattr(self.cfg.trading, "is_in_xau_london_killzone") else ((h_utc == 7 and m_utc >= 45) or (8 <= h_utc < 10) or (h_utc == 10 and m_utc <= 30)))) or (is_eur and (7 <= h_utc < 12)):
                    early_london = (h_utc == 7 and m_utc >= 45) or (h_utc == 8 and m_utc <= 30)
                    lon_trend_bias = h1_dir if early_london else None
                    seq = self.trigger.detect_xau_scalp_sequence(
                        m15_data=m15_data,
                        m1_data=m1_data,
                        m1_atr=m1_atr,
                        lookback_m15=getattr(self.cfg.trading, "xau_swing_lookback_m15", 20),
                        sequence_window_m1=getattr(self.cfg.trading, "xau_sequence_window_m1", 10),
                        min_atr_mult=getattr(self.cfg.trading, "xau_london_displacement_atr_mult", 0.75),
                        min_body_ratio=getattr(self.cfg.trading, "xau_london_displacement_body_ratio", 0.65),
                        mss_lookback=getattr(self.cfg.trading, "xau_mss_lookback_m1", 5),
                        target_r=self.cfg.trading.get_target_r(self.symbol) if hasattr(self.cfg.trading, "get_target_r") else getattr(self.cfg.trading, "xau_target_r", 2.0),
                        point_value=point_val,
                        stops_level_points=getattr(self.cfg.trading, "deviation_points", 10),
                        telemetry=self._ablation_counters,
                        liquidity_source="m15_swings",
                        current_time=current_time,
                        trend_bias=lon_trend_bias,
                        enable_delta_absorption=getattr(self.cfg.trading, "enable_delta_absorption", True),
                        delta_absorption_mode=getattr(self.cfg.trading, "delta_absorption_mode", "soft"),
                        enable_hvn_tp_calibration=getattr(self.cfg.trading, "enable_hvn_tp_calibration", True),
                        min_sl_distance=self.cfg.trading.get_min_sl_distance(self.symbol) if hasattr(self.cfg.trading, "get_min_sl_distance") else 0.0,
                    )
                # Tier 3: London/NY Overlap Pullback (disabled by default in high-quality killzone ecosystem)
                elif getattr(self.cfg.trading, "xau_enable_overlap_pullback", False) and (13 <= h_utc < 16) and not hierarchy_result.get("is_sideways"):
                    if h1_dir is not None:
                        seq = self.trigger.detect_overlap_pullback_setup(
                            m15_data=m15_data,
                            m1_data=m1_data,
                            m1_atr=m1_atr,
                            trend_direction=h1_dir,
                            target_r=self.cfg.trading.get_target_r(self.symbol) if hasattr(self.cfg.trading, "get_target_r") else getattr(self.cfg.trading, "xau_target_r", 2.0),
                            point_value=point_val,
                            stops_level_points=getattr(self.cfg.trading, "deviation_points", 10),
                            telemetry=self._ablation_counters,
                            min_sl_distance=self.cfg.trading.get_min_sl_distance(self.symbol) if hasattr(self.cfg.trading, "get_min_sl_distance") else 0.0,
                        )
            else:
                # Standard single-window mode
                if getattr(self.cfg.trading, "enable_session_filter", False) or getattr(self.cfg.trading, "xau_session_start", None):
                    from ..utils.time_utils import is_in_ny_session
                    s_start = getattr(self.cfg.trading, "xau_session_start", "10:00")
                    s_end = getattr(self.cfg.trading, "xau_session_end", "11:00")
                    s_tz = getattr(self.cfg.trading, "xau_session_timezone", "America/New_York")
                    if current_time is not None and not is_in_ny_session(current_time, s_start, s_end, s_tz):
                        return None

                seq = self.trigger.detect_xau_scalp_sequence(
                    m15_data=m15_data,
                    m1_data=m1_data,
                    m1_atr=m1_atr,
                    lookback_m15=getattr(self.cfg.trading, "xau_swing_lookback_m15", 20),
                    sequence_window_m1=getattr(self.cfg.trading, "xau_sequence_window_m1", 10),
                    min_atr_mult=getattr(self.cfg.trading, "xau_displacement_atr_mult", 0.60),
                    min_body_ratio=getattr(self.cfg.trading, "xau_displacement_body_ratio", 0.60),
                    mss_lookback=getattr(self.cfg.trading, "xau_mss_lookback_m1", 5),
                    target_r=self.cfg.trading.get_target_r(self.symbol) if hasattr(self.cfg.trading, "get_target_r") else getattr(self.cfg.trading, "xau_target_r", 2.0),
                    point_value=point_val,
                    stops_level_points=getattr(self.cfg.trading, "deviation_points", 10),
                    telemetry=self._ablation_counters,
                    liquidity_source=getattr(self.cfg.trading, "xau_liquidity_source", "m15_swings"),
                    current_time=current_time,
                    enable_delta_absorption=getattr(self.cfg.trading, "enable_delta_absorption", True),
                    delta_absorption_mode=getattr(self.cfg.trading, "delta_absorption_mode", "soft"),
                    enable_hvn_tp_calibration=getattr(self.cfg.trading, "enable_hvn_tp_calibration", True),
                    min_sl_distance=self.cfg.trading.get_min_sl_distance(self.symbol) if hasattr(self.cfg.trading, "get_min_sl_distance") else 0.0,
                )

            if not seq:
                return None

            direction = seq["direction"]
            entry_price = seq["entry_price"]
            sl = seq["sl_price"]
            tp = seq["tp_price"]
            entry_tf = "M1"
            atr_val = m1_atr
            setup_type = seq.get("setup_type", "SWEEP_FVG")


        else:
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
            atr_val = atr(entry_data.high, entry_data.low, entry_data.close, 14) or 0

            if trigger_type == "top_bottom_hunter":
                # Optional trend filter
                if getattr(self.cfg.trading, "tbh_use_trend_filter", False):
                    from ..indicators.moving_averages import sma
                    sma_len = getattr(self.cfg.trading, "tbh_trend_sma_len", 200)
                    trend_ma = sma(entry_data.close, sma_len)
                    if trend_ma is not None:
                        if direction == TradeDirection.BUY and price < trend_ma:
                            return None
                        if direction == TradeDirection.SELL and price > trend_ma:
                            return None

                tbh_ok, _ = self.trigger.check_top_bottom_hunter(
                    entry_data,
                    direction,
                    lookback=getattr(self.cfg.trading, "tbh_lookback", 2),
                    fib_0=getattr(self.cfg.trading, "tbh_fib_0", 0.382),
                    fib_1=getattr(self.cfg.trading, "tbh_fib_1", 0.618),
                    rsi_oversold=getattr(self.cfg.trading, "tbh_rsi_oversold", 30.0),
                    rsi_overbought=getattr(self.cfg.trading, "tbh_rsi_overbought", 70.0),
                )
                if not tbh_ok:
                    return None
                sl_dist = (atr_val or 1.0) * getattr(self.cfg.trading, "tbh_atr_sl_mult", 2.0)
                sl = price - sl_dist if direction == TradeDirection.BUY else price + sl_dist
                tp_dist = sl_dist * getattr(self.cfg.trading, "tbh_rr_ratio", 1.5)
                tp = price + tp_dist if direction == TradeDirection.BUY else price - tp_dist
            elif trigger_type == "hybrid":
                momentum_ok, _ = self.trigger.check_momentum_continuation(entry_data, direction)
                tbh_ok, _ = self.trigger.check_top_bottom_hunter(
                    entry_data,
                    direction,
                    lookback=getattr(self.cfg.trading, "tbh_lookback", 2),
                    fib_0=getattr(self.cfg.trading, "tbh_fib_0", 0.382),
                    fib_1=getattr(self.cfg.trading, "tbh_fib_1", 0.618),
                    rsi_oversold=getattr(self.cfg.trading, "tbh_rsi_oversold", 30.0),
                    rsi_overbought=getattr(self.cfg.trading, "tbh_rsi_overbought", 70.0),
                )
                if not (momentum_ok and tbh_ok):
                    return None
                sl = self.exit_mgr.calc_atr_sl(entry_data, direction, entry_tf)
                tp = self.exit_mgr.calc_structure_tp(entry_data, direction, price, atr_val)
            else:
                momentum_ok, _ = self.trigger.check_momentum_continuation(entry_data, direction)
                if not momentum_ok:
                    return None
                sl = self.exit_mgr.calc_atr_sl(entry_data, direction, entry_tf)
                tp = self.exit_mgr.calc_structure_tp(entry_data, direction, price, atr_val)

        zone = hierarchy_result.get("m15_zone")
        in_zone = self.zone.price_in_zone(price, zone) if zone else False

        is_fvg = trigger_type in ("xau_liquidity_sweep_fvg_m1", "liquidity_sweep_fvg")
        signal = Signal(
            symbol=getattr(self.cfg.trading, "symbol", "XAUUSD"),
            direction=direction,
            grade=SignalGrade.A if is_fvg else SignalGrade.B,
            entry_tf=entry_tf,
            h4_bias=hierarchy_result.get("h4_bias", Bias.NEUTRAL),
            h1_bias=hierarchy_result.get("h1_bias", Bias.NEUTRAL),
            m15_bias=hierarchy_result.get("m15_bias", Bias.NEUTRAL),
            m5_bias=hierarchy_result.get("m5_bias", Bias.NEUTRAL),
            regime=hierarchy_result.get("regime", Regime.RANGING),
            entry_price=entry_price if is_fvg else price,
            sl_price=sl,
            tp_price=tp,
            atr_value=atr_val,
            zone_high=zone[1] if zone else 0,
            zone_low=zone[0] if zone else 0,
            score=hierarchy_result.get("alignment_score", 0),
            setup_type=setup_type if is_fvg else "LEGACY",
        )
        if is_fvg and seq:
            signal.fvg_low = seq.get("fvg_low", 0.0)
            signal.fvg_high = seq.get("fvg_high", 0.0)
        if not is_fvg:
            signal.grade = self.scorer.grade(signal)
        return signal

    def _execute_backtest_order(self, signal: Signal, account: AccountInfo,
                                data_all: dict, idx: int, price: float) -> Optional[PyraCluster]:
        remaining = self.daily_loss.remaining_budget_amount()
        sym = signal.symbol
        is_eur = "EUR" in sym
        contract_sz = 100000 if is_eur else 100
        tick_sz = 0.00001 if is_eur else 0.01

        risk_scale = 1.0
        if getattr(self.cfg.trading, "enable_net_beta_gate", True):
            has_same_usd = any(
                c.status == TradeStatus.OPEN and getattr(c, "symbol", "") != sym and c.direction == signal.direction
                for c in self._clusters
            )
            if has_same_usd:
                risk_scale = getattr(self.cfg.trading, "correlated_usd_risk_scale", 0.60)

        lot = self.sizer.calculate_lot_size(
            account=account, entry_price=price, sl_price=signal.sl_price,
            direction=signal.direction, point_value=1.0, contract_size=contract_sz,
            tick_size=tick_sz,
            remaining_budget=remaining,
            risk_scale=risk_scale,
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
        m1_d = data_all.get("M1")
        bar_time = m1_d.time[-1] if (m1_d and m1_d.time) else None
        if cluster.direction == TradeDirection.BUY:
            cluster.highest_price = max(cluster.highest_price, price)
        else:
            if cluster.lowest_price <= 0.0:
                cluster.lowest_price = price
            else:
                cluster.lowest_price = min(cluster.lowest_price, price)

        # 1. Dynamic Breakeven Ratchet (+1.0R trigger)
        if getattr(self.cfg.trading, "xau_breakeven_ratchet_enabled", True) and not cluster.breakeven_activated:
            sym = getattr(cluster, "symbol", self.symbol)
            if hasattr(self.cfg.trading, "get_breakeven_trigger_r"):
                be_trig = self.cfg.trading.get_breakeven_trigger_r(sym)
            else:
                be_trig = getattr(self.cfg.trading, "xau_breakeven_trigger_r", 1.75)
            be_buf = getattr(self.cfg.trading, "xau_breakeven_buffer_r", 0.05)
            new_be = self.exit_mgr.check_breakeven_ratchet(cluster, price, trigger_r=be_trig, buffer_r=be_buf)
            if new_be is not None:
                cluster.collective_sl = new_be
                cluster.breakeven_activated = True
                for leg in cluster.legs:
                    if leg.status == TradeStatus.OPEN:
                        leg.sl_price = new_be
                actions.append({"action": "breakeven_ratchet", "sl": new_be, "cluster": cluster.cluster_id})

        # 2. Structural Damage / Opposite MSS Early Exit
        if getattr(self.cfg.trading, "xau_early_invalidation_exit", False) and cluster.status == TradeStatus.OPEN:
            m1_slice = data_all.get("M1")
            if m1_slice and len(m1_slice.close) >= 7:
                m1_atr = atr(m1_slice.high, m1_slice.low, m1_slice.close, 14) or 1.0
                inv_ok, inv_reason = self.exit_mgr.check_structural_invalidation(cluster, m1_slice, m1_atr)
                if inv_ok:
                    for leg in cluster.legs:
                        if leg.status == TradeStatus.OPEN:
                            leg.status = TradeStatus.CLOSED
                            leg.exit_price = price
                            leg.exit_reason = ExitReason.STRUCTURAL_INVALIDATION
                            actions.append({"action": "structural_invalidation", "price": price, "reason": inv_reason})
                    cluster.status = TradeStatus.CLOSED
                    m1_d = data_all.get("M1")
                    self._last_exit_time = m1_d.time[-1] if (m1_d and m1_d.time) else (cluster.legs[0].open_time if cluster.legs else None)
                    return actions

        # 3. Partial TP check (guarded by xau_partial_close_enabled)
        if getattr(self.cfg.trading, "xau_partial_close_enabled", False) and self.partial_close.check_partial_tp(cluster, price):
            for leg in cluster.legs:
                if leg.status == TradeStatus.OPEN:
                    leg.lot_size *= 0.5
            cluster.breakeven_activated = True
            cluster.collective_sl = cluster.avg_entry_price()
            actions.append({"action": "partial_tp", "price": price, "cluster": cluster.cluster_id})

        # 4. Stagnation Exit
        cluster_bars = getattr(cluster, "_bars_open", 0) + 1
        cluster._bars_open = cluster_bars
        if getattr(self.cfg.trading, "xau_stagnation_exit_enabled", False) and cluster.status == TradeStatus.OPEN:
            max_stag_bars = getattr(self.cfg.trading, "xau_stagnation_bars", 15)
            min_stag_r = getattr(self.cfg.trading, "xau_stagnation_min_r", 0.40)
            if self.exit_mgr.check_stagnation_exit(cluster, price, cluster_bars, max_bars=max_stag_bars, min_r=min_stag_r):
                for leg in cluster.legs:
                    if leg.status == TradeStatus.OPEN:
                        leg.status = TradeStatus.CLOSED
                        leg.exit_price = price
                        leg.exit_reason = ExitReason.STAGNATION
                        actions.append({"action": "stagnation_exit", "price": price})
                cluster.status = TradeStatus.CLOSED
                m1_d = data_all.get("M1")
                self._last_exit_time = m1_d.time[-1] if (m1_d and m1_d.time) else (cluster.legs[0].open_time if cluster.legs else None)
                return actions

        # 5. Standard SL / TP check & Split-Tranche Runner
        m1_slice = data_all.get("M1")
        m1_atr = atr(m1_slice.high, m1_slice.low, m1_slice.close, 14) or 1.5 if (m1_slice and len(m1_slice.close) >= 14) else 1.5
        bar_low = m1_slice.low[-1] if m1_slice and len(m1_slice.low) > 0 else price
        bar_high = m1_slice.high[-1] if m1_slice and len(m1_slice.high) > 0 else price
        enable_runner = getattr(self.cfg.trading, "enable_split_tranche_runner", True)
        trail_mult = getattr(self.cfg.trading, "runner_trail_atr_mult", 2.0)

        for leg in list(cluster.legs):
            if leg.status != TradeStatus.OPEN:
                continue

            # Trailing stop check for moonbag runner
            if getattr(leg, "_is_runner", False):
                if leg.direction == TradeDirection.BUY:
                    trail = cluster.highest_price - (trail_mult * m1_atr)
                    if trail > leg.sl_price:
                        leg.sl_price = trail
                    if bar_low <= leg.sl_price:
                        leg.status = TradeStatus.CLOSED
                        leg.exit_price = leg.sl_price
                        leg.exit_reason = ExitReason.CHANDELIER_TRAIL
                        actions.append({"action": "runner_trail_exit", "price": leg.exit_price})
                        continue
                else:
                    trail = cluster.lowest_price + (trail_mult * m1_atr)
                    if trail < leg.sl_price:
                        leg.sl_price = trail
                    if bar_high >= leg.sl_price:
                        leg.status = TradeStatus.CLOSED
                        leg.exit_price = leg.sl_price
                        leg.exit_reason = ExitReason.CHANDELIER_TRAIL
                        actions.append({"action": "runner_trail_exit", "price": leg.exit_price})
                        continue

            if leg.direction == TradeDirection.BUY:
                sl_hit = bar_low <= leg.sl_price
                tp_hit = bar_high >= leg.tp_price
                if sl_hit and tp_hit:
                    # Intrabar collision: conservative assumption (SL hit first)
                    leg.status = TradeStatus.CLOSED
                    leg.exit_price = leg.sl_price
                    leg.exit_reason = ExitReason.STOP_LOSS
                    self._last_sl_direction = leg.direction
                    self._last_sl_time = bar_time
                    actions.append({"action": "sl_hit", "price": leg.sl_price})
                elif sl_hit:
                    leg.status = TradeStatus.CLOSED
                    leg.exit_price = leg.sl_price
                    leg.exit_reason = ExitReason.STOP_LOSS
                    self._last_sl_direction = leg.direction
                    self._last_sl_time = bar_time
                    actions.append({"action": "sl_hit", "price": leg.sl_price})
                elif tp_hit:
                    if enable_runner and not getattr(leg, "_partial_banked", False) and leg.lot_size >= 0.02:
                        leg._partial_banked = True
                        bank_lot = round(leg.lot_size * 0.5, 2)
                        runner_lot = round(leg.lot_size - bank_lot, 2)
                        leg.lot_size = bank_lot
                        leg.status = TradeStatus.CLOSED
                        leg.exit_price = leg.tp_price
                        leg.exit_reason = ExitReason.TAKE_PROFIT
                        actions.append({"action": "banker_tp_hit", "price": leg.tp_price})

                        # Spawn Moonbag Runner Leg with SL trailed to Breakeven
                        runner_sl = leg.entry_price + (0.20 if "XAU" in self.symbol else 0.00010)
                        runner_leg = TradeLeg(
                            direction=leg.direction, entry_price=leg.entry_price, lot_size=runner_lot,
                            symbol=leg.symbol, sl_price=runner_sl, tp_price=999999.0,
                            open_time=leg.open_time, status=TradeStatus.OPEN,
                        )
                        runner_leg._is_runner = True
                        cluster.legs.append(runner_leg)
                        cluster.breakeven_activated = True
                    else:
                        leg.status = TradeStatus.CLOSED
                        leg.exit_price = leg.tp_price
                        leg.exit_reason = ExitReason.TAKE_PROFIT
                        actions.append({"action": "tp_hit", "price": leg.tp_price})
            else:
                sl_hit = bar_high >= leg.sl_price
                tp_hit = bar_low <= leg.tp_price
                if sl_hit and tp_hit:
                    # Intrabar collision: conservative assumption (SL hit first)
                    leg.status = TradeStatus.CLOSED
                    leg.exit_price = leg.sl_price
                    leg.exit_reason = ExitReason.STOP_LOSS
                    self._last_sl_direction = leg.direction
                    self._last_sl_time = bar_time
                    actions.append({"action": "sl_hit", "price": leg.sl_price})
                elif sl_hit:
                    leg.status = TradeStatus.CLOSED
                    leg.exit_price = leg.sl_price
                    leg.exit_reason = ExitReason.STOP_LOSS
                    self._last_sl_direction = leg.direction
                    self._last_sl_time = bar_time
                    actions.append({"action": "sl_hit", "price": leg.sl_price})
                elif tp_hit:
                    if enable_runner and not getattr(leg, "_partial_banked", False) and leg.lot_size >= 0.02:
                        leg._partial_banked = True
                        bank_lot = round(leg.lot_size * 0.5, 2)
                        runner_lot = round(leg.lot_size - bank_lot, 2)
                        leg.lot_size = bank_lot
                        leg.status = TradeStatus.CLOSED
                        leg.exit_price = leg.tp_price
                        leg.exit_reason = ExitReason.TAKE_PROFIT
                        actions.append({"action": "banker_tp_hit", "price": leg.tp_price})

                        # Spawn Moonbag Runner Leg with SL trailed to Breakeven
                        runner_sl = leg.entry_price - (0.20 if "XAU" in self.symbol else 0.00010)
                        runner_leg = TradeLeg(
                            direction=leg.direction, entry_price=leg.entry_price, lot_size=runner_lot,
                            symbol=leg.symbol, sl_price=runner_sl, tp_price=0.0,
                            open_time=leg.open_time, status=TradeStatus.OPEN,
                        )
                        runner_leg._is_runner = True
                        cluster.legs.append(runner_leg)
                        cluster.breakeven_activated = True
                    else:
                        leg.status = TradeStatus.CLOSED
                        leg.exit_price = leg.tp_price
                        leg.exit_reason = ExitReason.TAKE_PROFIT
                        actions.append({"action": "tp_hit", "price": leg.tp_price})

        open_legs = [l for l in cluster.legs if l.status == TradeStatus.OPEN]
        if not open_legs:
            cluster.status = TradeStatus.CLOSED
            m1_d = data_all.get("M1")
            self._last_exit_time = m1_d.time[-1] if (m1_d and m1_d.time) else (cluster.legs[0].open_time if cluster.legs else None)

        # 6. Check time stop for FVG scalping strategy (runners exempt)
        is_fvg_strat = getattr(self.cfg.trading, "strategy_trigger_type", "momentum") in (
            "xau_liquidity_sweep_fvg_m1", "liquidity_sweep_fvg"
        )
        if is_fvg_strat and cluster.status == TradeStatus.OPEN:
            sym = getattr(cluster, "symbol", self.symbol)
            if hasattr(self.cfg.trading, "get_max_holding_bars"):
                max_holding = self.cfg.trading.get_max_holding_bars(sym)
            else:
                max_holding = getattr(self.cfg.trading, "xau_max_holding_bars", 60)
            if max_holding > 0 and cluster_bars >= max_holding:
                for leg in cluster.legs:
                    if leg.status == TradeStatus.OPEN and not getattr(leg, "_is_runner", False):
                        leg.status = TradeStatus.CLOSED
                        leg.exit_price = price
                        leg.exit_reason = ExitReason.TIME_BASED
                        actions.append({"action": "time_stop_hit", "price": price})
                open_legs = [l for l in cluster.legs if l.status == TradeStatus.OPEN]
                if not open_legs:
                    cluster.status = TradeStatus.CLOSED
                    m1_d = data_all.get("M1")
                    self._last_exit_time = m1_d.time[-1] if (m1_d and m1_d.time) else (cluster.legs[0].open_time if cluster.legs else None)
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
        pnl_list: List[float] = []

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
                    pnl_list.append(pnl)
                    if pnl > 0:
                        wins += 1
                        gross_profit += pnl
                    elif pnl < 0:
                        losses += 1
                        gross_loss += abs(pnl)

        max_dd = float(self.max_dd.current_dd_pct)
        init_bal = self.cfg.trading.backtest_initial_balance
        final_bal = init_bal + total_pnl
        return_pct = round((total_pnl / init_bal) * 100, 2) if init_bal else 0.0
        win_rate = round(wins / total_trades * 100, 2) if total_trades else 0.0
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

        # Institutional Quant Metrics (from awesome-quant / quantstats / empyrical)
        avg_win = round(gross_profit / wins, 2) if wins > 0 else 0.0
        avg_loss = round(gross_loss / losses, 2) if losses > 0 else 0.0
        payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0
        expectancy = round((wins / total_trades * avg_win) - (losses / total_trades * avg_loss), 2) if total_trades > 0 else 0.0

        # Sharpe & Sortino ratios based on trade returns
        trade_returns = [p / init_bal for p in pnl_list] if init_bal else []
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        if len(trade_returns) > 1:
            mean_r = sum(trade_returns) / len(trade_returns)
            var_r = sum((r - mean_r) ** 2 for r in trade_returns) / (len(trade_returns) - 1)
            std_r = math.sqrt(var_r)
            if std_r > 0:
                sharpe_ratio = round((mean_r / std_r) * math.sqrt(252), 2)

            downside_sq = [r ** 2 for r in trade_returns if r < 0]
            if downside_sq:
                downside_std = math.sqrt(sum(downside_sq) / len(downside_sq))
                if downside_std > 0:
                    sortino_ratio = round((mean_r / downside_std) * math.sqrt(252), 2)

        calmar_ratio = round(return_pct / max_dd, 2) if max_dd > 0 else 0.0
        max_dd_dollars = round(init_bal * (max_dd / 100.0), 2)
        recovery_factor = round(total_pnl / max_dd_dollars, 2) if max_dd_dollars > 0 else 0.0
        kelly_fraction = round(PositionSizer.calculate_kelly_fraction(
            win_rate=wins / total_trades if total_trades else 0.0,
            win_loss_ratio=payoff_ratio,
            half_kelly=True,
        ) * 100, 2)

        return {
            "initial_balance": init_bal,
            "final_balance": round(final_bal, 2),
            "total_pnl": round(total_pnl, 2),
            "return_pct": return_pct,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_dd, 2),
            "clusters": len(self._clusters),
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "calmar_ratio": calmar_ratio,
            "expectancy": expectancy,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff_ratio": payoff_ratio,
            "recovery_factor": recovery_factor,
            "kelly_fraction_pct": kelly_fraction,
            "ablation": dict(self._ablation_counters),
            "trade_pnls": pnl_list,
        }

    @classmethod
    def run_sweep(
        cls,
        data: Dict,
        base_config: Config,
        param_grid: List[Dict[str, Any]],
        symbol: Optional[str] = None,
        strategy_version: str = "TopBottomHunter_v1.0",
    ) -> List[Dict[str, Any]]:
        """Run parameter sweeps deterministically adhering to Appendix A result schema.

        For each configuration:
        - Deepcopies base_config and applies parameter overrides.
        - Runs the backtest engine.
        - Formats results into an immutable record.
        - Preserves failed/losing tests with status='FAILED' without hallucinating missing metrics.
        """
        results = []
        for idx, param_set in enumerate(param_grid):
            config_id = param_set.get("config_id", f"EXP-{idx + 1:05d}")
            cfg = copy.deepcopy(base_config)

            # Apply parameters to trading config
            for k, v in param_set.items():
                if hasattr(cfg.trading, k):
                    setattr(cfg.trading, k, v)
                elif hasattr(cfg.mt5, k):
                    setattr(cfg.mt5, k, v)

            if symbol:
                cfg.trading.symbol = symbol

            try:
                engine = cls(cfg)
                res = engine.run(data)
                total_trades = res.get("total_trades", 0)
                status = "COMPLETED" if total_trades > 0 else "NO_TRADES"

                record = {
                    "config_id": config_id,
                    "strategy_version": strategy_version,
                    "symbol": cfg.trading.symbol,
                    "timeframe": cfg.trading.tradingview_timeframe or "M15",
                    "parameters": dict(param_set),
                    "metrics": {
                        "total_trades": total_trades,
                        "net_profit": res.get("total_pnl", 0.0),
                        "net_profit_pct": res.get("return_pct", 0.0),
                        "max_drawdown_pct": res.get("max_drawdown_pct", 0.0),
                        "win_rate": res.get("win_rate", 0.0),
                        "profit_factor": res.get("profit_factor", 0.0),
                        "avg_trade": round(res.get("total_pnl", 0.0) / total_trades, 2) if total_trades > 0 else 0.0,
                        "sharpe_ratio": res.get("sharpe_ratio", 0.0),
                        "sortino_ratio": res.get("sortino_ratio", 0.0),
                        "calmar_ratio": res.get("calmar_ratio", 0.0),
                        "payoff_ratio": res.get("payoff_ratio", 0.0),
                        "expectancy": res.get("expectancy", 0.0),
                    },
                    "status": status,
                    "error_message": None,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as e:
                log.warning("Sweep failed for config %s: %s", config_id, e)
                record = {
                    "config_id": config_id,
                    "strategy_version": strategy_version,
                    "symbol": cfg.trading.symbol,
                    "timeframe": cfg.trading.tradingview_timeframe or "M15",
                    "parameters": dict(param_set),
                    "metrics": {
                        "total_trades": 0,
                        "net_profit": 0.0,
                        "net_profit_pct": 0.0,
                        "max_drawdown_pct": 0.0,
                        "win_rate": 0.0,
                        "profit_factor": 0.0,
                        "avg_trade": 0.0,
                        "sharpe_ratio": 0.0,
                        "sortino_ratio": 0.0,
                        "calmar_ratio": 0.0,
                        "payoff_ratio": 0.0,
                        "expectancy": 0.0,
                    },
                    "status": "FAILED",
                    "error_message": str(e),
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }
            results.append(record)
        return results

    @staticmethod
    def audit_neighborhood(
        results: List[Dict[str, Any]],
        param_keys: Optional[List[str]] = None,
        min_trades: int = 5,
    ) -> List[Dict[str, Any]]:
        """Audit parameter neighborhoods and assign robustness classifications.

        Classifies each candidate region as:
        - ROBUST: Neighbor median PF >= 1.2, worst neighbor PF >= 1.0, candidate PF >= 1.3
        - PROMISING: Candidate PF >= 1.15, median neighbor PF >= 1.05
        - FRAGILE: Candidate has high return/PF but neighbors collapse (isolated cliff/overfit)
        - REJECT: Negative return, insufficient trades, or failing metrics
        """
        import statistics

        if not results:
            return []

        # Determine keys to compare
        all_keys = set()
        for r in results:
            all_keys.update(r.get("parameters", {}).keys())
        keys_to_compare = param_keys if param_keys else sorted(list(all_keys))

        # Build sorted index map for each numeric or hashable parameter
        val_indices: Dict[str, Dict[Any, int]] = {}
        for k in keys_to_compare:
            try:
                uniq = sorted({r.get("parameters", {}).get(k) for r in results if k in r.get("parameters", {})})
            except TypeError:
                uniq = list({r.get("parameters", {}).get(k) for r in results if k in r.get("parameters", {})})
            val_indices[k] = {v: idx for idx, v in enumerate(uniq)}

        audited = []
        for i, cand in enumerate(results):
            c_metrics = cand.get("metrics", {})
            c_trades = c_metrics.get("total_trades", 0)
            c_pf = c_metrics.get("profit_factor", 0.0)
            c_pnl = c_metrics.get("net_profit", 0.0)
            c_params = cand.get("parameters", {})

            # Find immediate adjacent neighbors in the grid
            neighbors = []
            for j, other in enumerate(results):
                if i == j:
                    continue
                o_params = other.get("parameters", {})
                is_neighbor = True
                diff_steps = 0
                for k in keys_to_compare:
                    if k in c_params and k in o_params:
                        idx_c = val_indices[k].get(c_params[k], 0)
                        idx_o = val_indices[k].get(o_params[k], 0)
                        step = abs(idx_c - idx_o)
                        if step > 1:
                            is_neighbor = False
                            break
                        diff_steps += step
                    else:
                        is_neighbor = False
                        break
                # Immediate grid neighbor has exactly 1 step difference across the grid
                if is_neighbor and diff_steps == 1:
                    neighbors.append(other)

            if neighbors:
                n_pfs = [n.get("metrics", {}).get("profit_factor", 0.0) for n in neighbors]
                median_n_pf = statistics.median(n_pfs)
                worst_n_pf = min(n_pfs)
            else:
                median_n_pf = c_pf
                worst_n_pf = c_pf

            # Robustness classification
            if c_trades < min_trades or c_pnl <= 0:
                label = "REJECT"
            elif c_pf >= 1.3 and median_n_pf >= 1.2 and worst_n_pf >= 1.0:
                label = "ROBUST"
            elif c_pf >= 1.15 and median_n_pf >= 1.05:
                label = "PROMISING"
            elif c_pf >= 1.25 and (median_n_pf < 1.0 or worst_n_pf < 0.8):
                label = "FRAGILE"
            else:
                label = "REJECT"

            cand_copy = copy.deepcopy(cand)
            cand_copy["neighborhood_audit"] = {
                "median_neighbor_pf": round(median_n_pf, 2),
                "worst_neighbor_pf": round(worst_n_pf, 2),
                "neighbor_count": len(neighbors),
                "robustness_label": label,
            }
            audited.append(cand_copy)

        return audited


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
