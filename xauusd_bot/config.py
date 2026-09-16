import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(): pass


def _env_bool(k: str, default: bool = False) -> bool:
    v = os.getenv(k, str(default)).strip().lower()
    return v in ("1", "true", "yes", "on")


def _env_float(k: str, default: float) -> float:
    return float(os.getenv(k, str(default)))


def _env_int(k: str, default: int) -> int:
    return int(os.getenv(k, str(default)))


log = logging.getLogger("xauusd_bot.config")


@dataclass
class MT5Config:
    login: int = 0
    password: str = ""
    server: str = ""
    path: str = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    timeout_ms: int = 5000

    @classmethod
    def from_env(cls) -> "MT5Config":
        pwd = os.getenv("MT5_PASSWORD", "")
        if pwd:
            log.warning("MT5 password found in plaintext environment variable — consider using encrypted storage")
        return cls(
            login=_env_int("MT5_LOGIN", 0),
            password=pwd,
            server=os.getenv("MT5_SERVER", ""),
            path=os.getenv("MT5_PATH", cls.path),
            timeout_ms=_env_int("MT5_TIMEOUT_MS", 5000),
        )


@dataclass
class TradingConfig:
    symbol: str = "XAUUSD"
    symbols: List[str] = field(default_factory=lambda: ["XAUUSD", "EURUSD"])
    magic_number: int = 20260601
    comment: str = "XAUUSD_Digger"

    enable_session_filter: bool = False
    enable_sideways_filter: bool = True
    sideways_chop_threshold: float = 61.8
    sideways_adx_threshold: float = 22.0
    sideways_bandwidth_squeeze_pct: float = 25.0

    enable_ao_saucer: bool = True
    enable_ha_filter: bool = True
    enable_psar_trailing: bool = True

    enable_tradingview: bool = False
    tradingview_timeframe: str = "M15"
    tradingview_cache_ttl: float = 45.0

    max_spread_multiplier: float = 1.5
    spread_lookback_bars: int = 50

    daily_loss_limit_pct: float = 3.0
    max_dd_limit_pct: float = 10.0
    daily_loss_buffer_pct: float = 0.3
    max_dd_buffer_pct: float = 2.0

    max_pyramid_entries: int = 3
    pyramid_add_trigger_r: float = 0.5
    pyramid_initial_risk_pct: float = 0.85

    partial_take_profit_r: float = 1.5
    partial_close_pct: float = 50.0

    time_based_exit_minutes: int = 240
    max_r_multiple: float = 3.0

    news_block_before_minutes: int = 30
    news_block_after_minutes: int = 30
    high_impact_news_only: bool = True

    atr_period: int = 14
    atr_multiplier_m1: float = 1.2
    atr_multiplier_m5: float = 1.5
    atr_multiplier_m15: float = 2.0
    atr_multiplier_m30: float = 2.5
    atr_multiplier_h1: float = 3.0

    ema_fast: int = 9
    ema_medium: int = 21
    ema_slow: int = 50
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    rsi_mid_upper: float = 60.0
    rsi_mid_lower: float = 40.0

    # Strategy Trigger Configuration (xau_liquidity_sweep_fvg_m1 is the authoritative production engine)
    strategy_trigger_type: str = "xau_liquidity_sweep_fvg_m1"
    tbh_lookback: int = 2
    tbh_fib_0: float = 0.382
    tbh_fib_1: float = 0.618
    tbh_rsi_length: int = 14
    tbh_rsi_oversold: float = 30.0
    tbh_rsi_overbought: float = 70.0
    tbh_atr_sl_mult: float = 2.0
    tbh_rr_ratio: float = 1.5
    tbh_use_trend_filter: bool = False
    tbh_trend_sma_len: int = 200

    # XAU_LIQUIDITY_SWEEP_FVG_M1 Scalping Parameters (Professional Setup)
    xau_fvg_strategy_enabled: bool = True
    xau_context_timeframe: str = "M15"
    xau_execution_timeframe: str = "M1"
    xau_session_timezone: str = "America/New_York"
    xau_session_start: str = "02:00"   # NY time — covers Asian + London + NY
    xau_session_end: str = "20:00"    # NY time — full trading day open
    xau_atr_period: int = 14
    xau_displacement_atr_mult: float = 0.60
    xau_displacement_body_ratio: float = 0.60
    xau_fvg_expiry_bars: int = 5
    xau_max_holding_bars: int = 0  # 0 = disabled for Gold runners (full 2.0R TP target)
    xau_require_retest: bool = True  # Confirmed rejection bounce required before filling FVG order
    xau_retest_max_bars: int = 8  # Maximum M1 bars to wait for confirmed retest
    xau_strict_killzones: bool = True  # London (07-09 UTC) & NY Core (13:30-16:30 UTC)
    xau_target_r: float = 2.0
    xau_risk_per_trade: float = 0.0085
    xau_max_trades_per_session: int = 2  # Option A: 2 trades per session
    max_daily_trades: int = 4            # Option A: up to 4 trades per day across portfolio
    max_concurrent_pending_orders: int = 2 # Option A: allow up to 2 concurrent pending limit orders
    xau_fvg_expiry_bars: int = 8         # Option A: 8 M1 bars limit order patience for Gold
    eur_fvg_expiry_bars: int = 12        # Option A: 12 M1 bars limit order patience for EUR/USD
    xau_cooldown_minutes: int = 5
    xau_swing_lookback_m15: int = 20
    xau_mss_lookback_m1: int = 5
    xau_liquidity_source: str = "m15_swings"  # "m15_swings" (authoritative winner)

    # Multi-Strategy Institutional Ecosystem & Advanced Loss-Mitigation
    xau_ecosystem_mode: bool = True
    xau_enable_london_asian_sweep: bool = True
    xau_enable_overlap_pullback: bool = False
    xau_early_invalidation_exit: bool = False
    xau_enable_pre_fill_guard: bool = True
    xau_breakeven_ratchet_enabled: bool = True
    xau_breakeven_trigger_r: float = 1.75
    xau_breakeven_buffer_r: float = 0.05
    xau_stagnation_exit_enabled: bool = False
    xau_stagnation_bars: int = 15
    xau_stagnation_min_r: float = 0.40
    xau_partial_close_enabled: bool = False
    xau_london_displacement_atr_mult: float = 0.75
    xau_london_displacement_body_ratio: float = 0.65
    xau_london_risk_per_trade: float = 0.0085
    xau_overlap_risk_per_trade: float = 0.0065

    eur_target_r: float = 1.6
    eur_breakeven_trigger_r: float = 1.2
    eur_max_holding_bars: int = 180
    eur_session_start_hour: int = 7   # 07:00 UTC (London Open)
    eur_session_end_hour: int = 16    # 16:00 UTC (NY Overlap close - cuts off-hours drift/rollover)
    eur_filter_lunch_chop: bool = True # Filter 10:00-12:00 UTC European lunch dead zone
    eur_lunch_start_hour: int = 10
    eur_lunch_end_hour: int = 12
    eur_require_retest: bool = True
    eur_retest_max_bars: int = 8
    xau_min_sl_distance: float = 5.0  # Minimum $5.00 SL breathing room floor for Gold
    eur_min_sl_distance: float = 0.0  # 0.0 = structural for Forex

    # ── El Professor Hidden Guards ──────────────────────────────────────────────
    # Guard 1 (XAU only): London Close Wall — no new entries after 15:45 UTC
    # Backtested: Win% 41.5%→46.3%, PF 1.54→1.74, blocks 42 low-quality entries
    xau_london_close_guard: bool = True
    xau_london_close_cutoff_hour: int = 15
    xau_london_close_cutoff_min: int = 45

    # Guard 2 (EUR only): ATR Flash-Crash Circuit Breaker
    # Backtested: +$112 PnL boost, blocks 6 spike entries, neutral-to-positive
    eur_atr_circuit_breaker: bool = True
    eur_atr_spike_lookback: int = 20
    eur_atr_spike_mult: float = 3.0

    # Guard 3 (EUR only): H4 Macro Bias Alignment — block counter-H4-trend entries
    # Backtested: Win% 55.4%→63.2%, PF 1.56→2.98 (combined with G4)
    eur_h4_bias_guard: bool = True
    eur_h4_ema_fast: int = 9
    eur_h4_ema_slow: int = 50

    # Guard 4 (EUR only): 3-Consecutive-Loss Cooldown — 2-hour pause after 3 SLs
    # Backtested: neutral standalone, +quality boost inside professor suite
    eur_consec_loss_guard: bool = True
    eur_consec_loss_max: int = 3
    eur_consec_loss_pause_hours: int = 2

    enable_profit_compounding: bool = True
    initial_account_balance: float = 10000.0
    compounding_cap_mult: float = 2.0
    enable_net_beta_gate: bool = True
    correlated_usd_risk_scale: float = 0.60

    # ── Dynamic Profit Maximization Engine ───────────────────────────────────────
    delta_absorption_mode: str = "soft"  # "soft" (additive scoring / telemetry) or "hard" (strict setup veto)
    enable_split_tranche_runner: bool = True
    runner_tranche_pct: float = 0.50
    runner_trail_atr_mult: float = 2.0
    fvg_adaptive_retest_tolerance_pct: float = 0.25
    enable_fvg_pyramiding: bool = True

    # ── XAU Dynamic Breakeven & Multi-Order Risk Engine ──────────────────────────
    xau_breakeven_enabled: bool = True
    xau_breakeven_r: float = 1.25
    xau_breakeven_buffer: float = 0.30
    xau_prevent_duplicate_pending: bool = True
    xau_h4_bias_guard: bool = False
    xau_h4_ema_fast: int = 9
    xau_h4_ema_slow: int = 50

    def is_in_eur_session(self, current_time) -> bool:
        """Evaluate whether current UTC time is within EURUSD active institutional session."""
        if current_time is None:
            return True
        h_utc = current_time.hour if hasattr(current_time, "hour") else 0
        in_main = (self.eur_session_start_hour <= h_utc < self.eur_session_end_hour)
        if not in_main:
            return False
        if self.eur_filter_lunch_chop:
            if self.eur_lunch_start_hour <= h_utc < self.eur_lunch_end_hour:
                return False
        return True

    def get_target_r(self, symbol: str = "XAUUSD") -> float:
        if symbol and "EUR" in symbol.upper():
            return self.eur_target_r
        return self.xau_target_r

    def get_breakeven_trigger_r(self, symbol: str = "XAUUSD") -> float:
        if symbol and "EUR" in symbol.upper():
            return getattr(self, "eur_breakeven_trigger_r", 1.2)
        return self.xau_breakeven_trigger_r

    def get_max_holding_bars(self, symbol: str = "XAUUSD") -> int:
        if symbol and "EUR" in symbol.upper():
            return self.eur_max_holding_bars
        return self.xau_max_holding_bars

    xau_london_start_hour: int = 7
    xau_london_start_minute: int = 45
    xau_london_end_hour: int = 10
    xau_london_end_minute: int = 30

    def is_in_xau_london_killzone(self, dt) -> bool:
        """London killzone aligned to authentic London cash liquidity (07:45 - 10:30 UTC)."""
        if dt is None:
            return False
        h = dt.hour if hasattr(dt, "hour") else 0
        m = dt.minute if hasattr(dt, "minute") else 0
        return (h == 7 and m >= 45) or (8 <= h < 10) or (h == 10 and m <= 30)

    def get_min_sl_distance(self, symbol: str = "XAUUSD", current_price: float = 0.0, m1_atr: float = 0.0) -> float:
        if (symbol and "EUR" in symbol.upper()) or (0 < current_price < 10.0):
            return getattr(self, "eur_min_sl_distance", 0.0)
        dyn_floor = getattr(self, "xau_min_sl_distance", 5.0)
        if current_price > 1000:
            dyn_floor = max(dyn_floor, current_price * 0.0016)
        if m1_atr > 0:
            dyn_floor = max(dyn_floor, m1_atr * 1.5)
        return dyn_floor

    def get_fvg_expiry_bars(self, symbol: str = "XAUUSD") -> int:
        if symbol and "EUR" in symbol.upper():
            return self.eur_fvg_expiry_bars
        return self.xau_fvg_expiry_bars

    deviation_points: int = 20

    vwap_period: int = 20
    enable_vwap_bands: bool = False
    vwap_band_mult_1: float = 1.0
    vwap_band_mult_2: float = 2.0

    # Auction Market Theory (AMT) Volume Profile
    enable_amt_filter: bool = False
    amt_value_area_pct: float = 0.70
    amt_profile_lookback: int = 50
    amt_profile_bins: int = 50

    # Prop-Desk Order Flow & Microstructure Delta
    enable_delta_absorption: bool = True
    enable_delta_divergence: bool = False
    enable_hvn_tp_calibration: bool = True


    min_structure_swing_bars: int = 5
    max_structure_swing_bars: int = 20

    signal_score_a_min: int = 8
    signal_score_b_min: int = 5

    session_london_open: str = "02:00"   # UTC — includes Asian pre-market
    session_london_close: str = "22:00"  # UTC — includes full NY session
    session_ny_open: str = "02:00"       # UTC — open from Asian session
    session_ny_close: str = "23:00"      # UTC — full day coverage

    broker_daily_reset_hour: int = 0
    broker_daily_reset_tz: str = "UTC"

    logging_level: str = "INFO"
    log_file: str = "logs/xauusd_bot.log"
    telegram_token: str = ""
    telegram_chat_id: str = ""

    state_db_path: str = "data/bot_state.db"
    trade_log_path: str = "data/trade_log.csv"

    enable_research_team: bool = True
    research_report_dir: str = "logs/research"
    ai_provider: str = "none"
    ai_api_key: str = ""

    poll_interval_ms: int = 500

    # London Strategic Edge (LSE) Live WebSocket & Vault API
    lse_api_key: str = "lse_live_31f53152fae3fd762294057c154f19b2"
    lse_ws_url: str = "wss://data-ws.londonstrategicedge.com"
    lse_http_url: str = "https://api.londonstrategicedge.com/vault"
    enable_lse_feed: bool = True

    backtest_initial_balance: float = 100000.0
    backtest_commission_pct: float = 0.0
    backtest_slippage_points: float = 0.5
    backtest_spread_points: float = 20.0

    def atr_multiplier_for_tf(self, tf: str) -> float:
        return {
            "M1": self.atr_multiplier_m1,
            "M5": self.atr_multiplier_m5,
            "M15": self.atr_multiplier_m15,
            "M30": self.atr_multiplier_m30,
            "H1": self.atr_multiplier_h1,
        }.get(tf, 1.5)

    @classmethod
    def from_env(cls) -> "TradingConfig":
        raw_symbols = os.getenv("SYMBOLS", "")
        if raw_symbols:
            symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
        else:
            env_sym = os.getenv("SYMBOL", "")
            if env_sym and env_sym != "XAUUSD":
                symbols = [env_sym]
            else:
                symbols = ["XAUUSD", "EURUSD"]

        return cls(
            symbol=os.getenv("SYMBOL", cls.symbol),
            symbols=symbols,
            magic_number=_env_int("MAGIC_NUMBER", cls.magic_number),
            enable_session_filter=_env_bool("ENABLE_SESSION_FILTER", cls.enable_session_filter),
            enable_sideways_filter=_env_bool("ENABLE_SIDEWAYS_FILTER", cls.enable_sideways_filter),
            sideways_chop_threshold=_env_float("SIDEWAYS_CHOP_THRESHOLD", cls.sideways_chop_threshold),
            sideways_adx_threshold=_env_float("SIDEWAYS_ADX_THRESHOLD", cls.sideways_adx_threshold),
            sideways_bandwidth_squeeze_pct=_env_float("SIDEWAYS_BANDWIDTH_SQUEEZE_PCT", cls.sideways_bandwidth_squeeze_pct),
            enable_ao_saucer=_env_bool("ENABLE_AO_SAUCER", cls.enable_ao_saucer),
            enable_ha_filter=_env_bool("ENABLE_HA_FILTER", cls.enable_ha_filter),
            enable_psar_trailing=_env_bool("ENABLE_PSAR_TRAILING", cls.enable_psar_trailing),
            enable_tradingview=_env_bool("ENABLE_TRADINGVIEW", cls.enable_tradingview),
            tradingview_timeframe=os.getenv("TRADINGVIEW_TIMEFRAME", cls.tradingview_timeframe),
            tradingview_cache_ttl=_env_float("TRADINGVIEW_CACHE_TTL", cls.tradingview_cache_ttl),
            max_spread_multiplier=_env_float("MAX_SPREAD_MULTIPLIER", cls.max_spread_multiplier),
            spread_lookback_bars=_env_int("SPREAD_LOOKBACK_BARS", cls.spread_lookback_bars),
            daily_loss_limit_pct=_env_float("DAILY_LOSS_LIMIT_PCT", cls.daily_loss_limit_pct),
            max_dd_limit_pct=_env_float("MAX_DD_LIMIT_PCT", cls.max_dd_limit_pct),
            daily_loss_buffer_pct=_env_float("DAILY_LOSS_BUFFER_PCT", cls.daily_loss_buffer_pct),
            max_dd_buffer_pct=_env_float("MAX_DD_BUFFER_PCT", cls.max_dd_buffer_pct),
            max_pyramid_entries=_env_int("MAX_PYRAMID_ENTRIES", cls.max_pyramid_entries),
            pyramid_add_trigger_r=_env_float("PYRAMID_ADD_TRIGGER_R", cls.pyramid_add_trigger_r),
            pyramid_initial_risk_pct=_env_float("PYRAMID_INITIAL_RISK_PCT", cls.pyramid_initial_risk_pct),
            partial_take_profit_r=_env_float("PARTIAL_TAKE_PROFIT_R", cls.partial_take_profit_r),
            partial_close_pct=_env_float("PARTIAL_CLOSE_PCT", cls.partial_close_pct),
            time_based_exit_minutes=_env_int("TIME_BASED_EXIT_MINUTES", cls.time_based_exit_minutes),
            max_r_multiple=_env_float("MAX_R_MULTIPLE", cls.max_r_multiple),
            news_block_before_minutes=_env_int("NEWS_BLOCK_BEFORE_MINUTES", cls.news_block_before_minutes),
            news_block_after_minutes=_env_int("NEWS_BLOCK_AFTER_MINUTES", cls.news_block_after_minutes),
            high_impact_news_only=_env_bool("HIGH_IMPACT_NEWS_ONLY", cls.high_impact_news_only),
            atr_period=_env_int("ATR_PERIOD", cls.atr_period),
            atr_multiplier_m1=_env_float("ATR_MULTIPLIER_M1", cls.atr_multiplier_m1),
            atr_multiplier_m5=_env_float("ATR_MULTIPLIER_M5", cls.atr_multiplier_m5),
            atr_multiplier_m15=_env_float("ATR_MULTIPLIER_M15", cls.atr_multiplier_m15),
            atr_multiplier_m30=_env_float("ATR_MULTIPLIER_M30", cls.atr_multiplier_m30),
            atr_multiplier_h1=_env_float("ATR_MULTIPLIER_H1", cls.atr_multiplier_h1),
            ema_fast=_env_int("EMA_FAST", cls.ema_fast),
            ema_medium=_env_int("EMA_MEDIUM", cls.ema_medium),
            ema_slow=_env_int("EMA_SLOW", cls.ema_slow),
            rsi_period=_env_int("RSI_PERIOD", cls.rsi_period),
            rsi_overbought=_env_float("RSI_OVERBOUGHT", cls.rsi_overbought),
            rsi_oversold=_env_float("RSI_OVERSOLD", cls.rsi_oversold),
            rsi_mid_upper=_env_float("RSI_MID_UPPER", cls.rsi_mid_upper),
            rsi_mid_lower=_env_float("RSI_MID_LOWER", cls.rsi_mid_lower),
            deviation_points=_env_int("DEVIATION_POINTS", cls.deviation_points),
            vwap_period=_env_int("VWAP_PERIOD", cls.vwap_period),
            min_structure_swing_bars=_env_int("MIN_STRUCTURE_SWING_BARS", cls.min_structure_swing_bars),
            max_structure_swing_bars=_env_int("MAX_STRUCTURE_SWING_BARS", cls.max_structure_swing_bars),
            signal_score_a_min=_env_int("SIGNAL_SCORE_A_MIN", cls.signal_score_a_min),
            signal_score_b_min=_env_int("SIGNAL_SCORE_B_MIN", cls.signal_score_b_min),
            session_london_open=os.getenv("SESSION_LONDON_OPEN", cls.session_london_open),
            session_london_close=os.getenv("SESSION_LONDON_CLOSE", cls.session_london_close),
            session_ny_open=os.getenv("SESSION_NY_OPEN", cls.session_ny_open),
            session_ny_close=os.getenv("SESSION_NY_CLOSE", cls.session_ny_close),
            broker_daily_reset_hour=_env_int("BROKER_DAILY_RESET_HOUR", cls.broker_daily_reset_hour),
            broker_daily_reset_tz=os.getenv("BROKER_DAILY_RESET_TZ", cls.broker_daily_reset_tz),
            logging_level=os.getenv("LOGGING_LEVEL", cls.logging_level),
            log_file=os.getenv("LOG_FILE", cls.log_file),
            telegram_token=os.getenv("TELEGRAM_TOKEN", cls.telegram_token),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", cls.telegram_chat_id),
            state_db_path=os.getenv("STATE_DB_PATH", cls.state_db_path),
            trade_log_path=os.getenv("TRADE_LOG_PATH", cls.trade_log_path),
            enable_research_team=_env_bool("ENABLE_RESEARCH_TEAM", cls.enable_research_team),
            research_report_dir=os.getenv("RESEARCH_REPORT_DIR", cls.research_report_dir),
            ai_provider=os.getenv("AI_PROVIDER", cls.ai_provider),
            ai_api_key=os.getenv("AI_API_KEY", cls.ai_api_key),
            poll_interval_ms=_env_int("POLL_INTERVAL_MS", cls.poll_interval_ms),
            lse_api_key=os.getenv("LSE_API_KEY", cls.lse_api_key),
            lse_ws_url=os.getenv("LSE_WS_URL", cls.lse_ws_url),
            lse_http_url=os.getenv("LSE_HTTP_URL", cls.lse_http_url),
            enable_lse_feed=_env_bool("ENABLE_LSE_FEED", cls.enable_lse_feed),
            backtest_initial_balance=_env_float("BACKTEST_INITIAL_BALANCE", cls.backtest_initial_balance),
            backtest_commission_pct=_env_float("BACKTEST_COMMISSION_PCT", cls.backtest_commission_pct),
            backtest_slippage_points=_env_float("BACKTEST_SLIPPAGE_POINTS", cls.backtest_slippage_points),
            backtest_spread_points=_env_float("BACKTEST_SPREAD_POINTS", cls.backtest_spread_points),
            strategy_trigger_type=os.getenv("STRATEGY_TRIGGER_TYPE", cls.strategy_trigger_type),
            tbh_lookback=_env_int("TBH_LOOKBACK", cls.tbh_lookback),
            tbh_fib_0=_env_float("TBH_FIB_0", cls.tbh_fib_0),
            tbh_fib_1=_env_float("TBH_FIB_1", cls.tbh_fib_1),
            tbh_rsi_length=_env_int("TBH_RSI_LENGTH", cls.tbh_rsi_length),
            tbh_rsi_oversold=_env_float("TBH_RSI_OVERSOLD", cls.tbh_rsi_oversold),
            tbh_rsi_overbought=_env_float("TBH_RSI_OVERBOUGHT", cls.tbh_rsi_overbought),
            tbh_atr_sl_mult=_env_float("TBH_ATR_SL_MULT", cls.tbh_atr_sl_mult),
            tbh_rr_ratio=_env_float("TBH_RR_RATIO", cls.tbh_rr_ratio),
            tbh_use_trend_filter=_env_bool("TBH_USE_TREND_FILTER", cls.tbh_use_trend_filter),
            tbh_trend_sma_len=_env_int("TBH_TREND_SMA_LEN", cls.tbh_trend_sma_len),
            xau_fvg_strategy_enabled=_env_bool("XAU_FVG_STRATEGY_ENABLED", cls.xau_fvg_strategy_enabled),
            xau_context_timeframe=os.getenv("XAU_CONTEXT_TIMEFRAME", cls.xau_context_timeframe),
            xau_execution_timeframe=os.getenv("XAU_EXECUTION_TIMEFRAME", cls.xau_execution_timeframe),
            xau_session_timezone=os.getenv("XAU_SESSION_TIMEZONE", cls.xau_session_timezone),
            xau_session_start=os.getenv("XAU_SESSION_START", cls.xau_session_start),
            xau_session_end=os.getenv("XAU_SESSION_END", cls.xau_session_end),
            xau_atr_period=_env_int("XAU_ATR_PERIOD", cls.xau_atr_period),
            xau_displacement_atr_mult=_env_float("XAU_DISPLACEMENT_ATR_MULT", cls.xau_displacement_atr_mult),
            xau_displacement_body_ratio=_env_float("XAU_DISPLACEMENT_BODY_RATIO", cls.xau_displacement_body_ratio),
            xau_fvg_expiry_bars=_env_int("XAU_FVG_EXPIRY_BARS", cls.xau_fvg_expiry_bars),
            xau_max_holding_bars=_env_int("XAU_MAX_HOLDING_BARS", cls.xau_max_holding_bars),
            xau_target_r=_env_float("XAU_TARGET_R", cls.xau_target_r),
            xau_risk_per_trade=_env_float("XAU_RISK_PER_TRADE", cls.xau_risk_per_trade),
            xau_max_trades_per_session=_env_int("XAU_MAX_TRADES_PER_SESSION", cls.xau_max_trades_per_session),
            xau_cooldown_minutes=_env_int("XAU_COOLDOWN_MINUTES", cls.xau_cooldown_minutes),
            xau_swing_lookback_m15=_env_int("XAU_SWING_LOOKBACK_M15", cls.xau_swing_lookback_m15),
            xau_mss_lookback_m1=_env_int("XAU_MSS_LOOKBACK_M1", cls.xau_mss_lookback_m1),
            enable_vwap_bands=_env_bool("ENABLE_VWAP_BANDS", cls.enable_vwap_bands),
            vwap_band_mult_1=_env_float("VWAP_BAND_MULT_1", cls.vwap_band_mult_1),
            vwap_band_mult_2=_env_float("VWAP_BAND_MULT_2", cls.vwap_band_mult_2),
            enable_amt_filter=_env_bool("ENABLE_AMT_FILTER", cls.enable_amt_filter),
            amt_value_area_pct=_env_float("AMT_VALUE_AREA_PCT", cls.amt_value_area_pct),
            amt_profile_lookback=_env_int("AMT_PROFILE_LOOKBACK", cls.amt_profile_lookback),
            amt_profile_bins=_env_int("AMT_PROFILE_BINS", cls.amt_profile_bins),
            enable_delta_absorption=_env_bool("ENABLE_DELTA_ABSORPTION", cls.enable_delta_absorption),
            enable_delta_divergence=_env_bool("ENABLE_DELTA_DIVERGENCE", cls.enable_delta_divergence),
            enable_hvn_tp_calibration=_env_bool("ENABLE_HVN_TP_CALIBRATION", cls.enable_hvn_tp_calibration),
            xau_ecosystem_mode=_env_bool("XAU_ECOSYSTEM_MODE", cls.xau_ecosystem_mode),
            xau_enable_london_asian_sweep=_env_bool("XAU_ENABLE_LONDON_ASIAN_SWEEP", cls.xau_enable_london_asian_sweep),
            xau_enable_overlap_pullback=_env_bool("XAU_ENABLE_OVERLAP_PULLBACK", cls.xau_enable_overlap_pullback),
            xau_early_invalidation_exit=_env_bool("XAU_EARLY_INVALIDATION_EXIT", cls.xau_early_invalidation_exit),
            xau_enable_pre_fill_guard=_env_bool("XAU_ENABLE_PRE_FILL_GUARD", cls.xau_enable_pre_fill_guard),
            xau_breakeven_ratchet_enabled=_env_bool("XAU_BREAKEVEN_RATCHET_ENABLED", cls.xau_breakeven_ratchet_enabled),
            xau_breakeven_trigger_r=_env_float("XAU_BREAKEVEN_TRIGGER_R", cls.xau_breakeven_trigger_r),
            xau_breakeven_buffer_r=_env_float("XAU_BREAKEVEN_BUFFER_R", cls.xau_breakeven_buffer_r),
            xau_stagnation_exit_enabled=_env_bool("XAU_STAGNATION_EXIT_ENABLED", cls.xau_stagnation_exit_enabled),
            xau_stagnation_bars=_env_int("XAU_STAGNATION_BARS", cls.xau_stagnation_bars),
            xau_stagnation_min_r=_env_float("XAU_STAGNATION_MIN_R", cls.xau_stagnation_min_r),
            xau_partial_close_enabled=_env_bool("XAU_PARTIAL_CLOSE_ENABLED", cls.xau_partial_close_enabled),
            xau_london_displacement_atr_mult=_env_float("XAU_LONDON_DISPLACEMENT_ATR_MULT", cls.xau_london_displacement_atr_mult),
            xau_london_displacement_body_ratio=_env_float("XAU_LONDON_DISPLACEMENT_BODY_RATIO", cls.xau_london_displacement_body_ratio),
            xau_london_risk_per_trade=_env_float("XAU_LONDON_RISK_PER_TRADE", cls.xau_london_risk_per_trade),
            xau_overlap_risk_per_trade=_env_float("XAU_OVERLAP_RISK_PER_TRADE", cls.xau_overlap_risk_per_trade),
            eur_target_r=_env_float("EUR_TARGET_R", cls.eur_target_r),
            eur_breakeven_trigger_r=_env_float("EUR_BREAKEVEN_TRIGGER_R", cls.eur_breakeven_trigger_r),
            eur_max_holding_bars=_env_int("EUR_MAX_HOLDING_BARS", cls.eur_max_holding_bars),
            eur_session_start_hour=_env_int("EUR_SESSION_START_HOUR", cls.eur_session_start_hour),
            eur_session_end_hour=_env_int("EUR_SESSION_END_HOUR", cls.eur_session_end_hour),
            eur_filter_lunch_chop=_env_bool("EUR_FILTER_LUNCH_CHOP", cls.eur_filter_lunch_chop),
            eur_lunch_start_hour=_env_int("EUR_LUNCH_START_HOUR", cls.eur_lunch_start_hour),
            eur_lunch_end_hour=_env_int("EUR_LUNCH_END_HOUR", cls.eur_lunch_end_hour),
            eur_require_retest=_env_bool("EUR_REQUIRE_RETEST", cls.eur_require_retest),
            eur_retest_max_bars=_env_int("EUR_RETEST_MAX_BARS", cls.eur_retest_max_bars),
            xau_require_retest=_env_bool("XAU_REQUIRE_RETEST", cls.xau_require_retest),
            xau_retest_max_bars=_env_int("XAU_RETEST_MAX_BARS", cls.xau_retest_max_bars),
            xau_strict_killzones=_env_bool("XAU_STRICT_KILLZONES", cls.xau_strict_killzones),
            xau_min_sl_distance=_env_float("XAU_MIN_SL_DISTANCE", cls.xau_min_sl_distance),
            eur_min_sl_distance=_env_float("EUR_MIN_SL_DISTANCE", cls.eur_min_sl_distance),
            # El Professor Hidden Guards
            xau_london_close_guard=_env_bool("XAU_LONDON_CLOSE_GUARD", cls.xau_london_close_guard),
            xau_london_close_cutoff_hour=_env_int("XAU_LONDON_CLOSE_CUTOFF_HOUR", cls.xau_london_close_cutoff_hour),
            xau_london_close_cutoff_min=_env_int("XAU_LONDON_CLOSE_CUTOFF_MIN", cls.xau_london_close_cutoff_min),
            eur_atr_circuit_breaker=_env_bool("EUR_ATR_CIRCUIT_BREAKER", cls.eur_atr_circuit_breaker),
            eur_atr_spike_lookback=_env_int("EUR_ATR_SPIKE_LOOKBACK", cls.eur_atr_spike_lookback),
            eur_atr_spike_mult=_env_float("EUR_ATR_SPIKE_MULT", cls.eur_atr_spike_mult),
            eur_h4_bias_guard=_env_bool("EUR_H4_BIAS_GUARD", cls.eur_h4_bias_guard),
            eur_h4_ema_fast=_env_int("EUR_H4_EMA_FAST", cls.eur_h4_ema_fast),
            eur_h4_ema_slow=_env_int("EUR_H4_EMA_SLOW", cls.eur_h4_ema_slow),
            eur_consec_loss_guard=_env_bool("EUR_CONSEC_LOSS_GUARD", cls.eur_consec_loss_guard),
            eur_consec_loss_max=_env_int("EUR_CONSEC_LOSS_MAX", cls.eur_consec_loss_max),
            eur_consec_loss_pause_hours=_env_int("EUR_CONSEC_LOSS_PAUSE_HOURS", cls.eur_consec_loss_pause_hours),
            # Option A Scaling Parameters
            max_daily_trades=_env_int("MAX_DAILY_TRADES", cls.max_daily_trades),
            max_concurrent_pending_orders=_env_int("MAX_CONCURRENT_PENDING_ORDERS", cls.max_concurrent_pending_orders),
            eur_fvg_expiry_bars=_env_int("EUR_FVG_EXPIRY_BARS", cls.eur_fvg_expiry_bars),
            # Dynamic Profit Compounding & Net Dollar Beta Gate
            enable_profit_compounding=_env_bool("ENABLE_PROFIT_COMPOUNDING", cls.enable_profit_compounding),
            initial_account_balance=_env_float("INITIAL_ACCOUNT_BALANCE", cls.initial_account_balance),
            compounding_cap_mult=_env_float("COMPOUNDING_CAP_MULT", cls.compounding_cap_mult),
            enable_net_beta_gate=_env_bool("ENABLE_NET_BETA_GATE", cls.enable_net_beta_gate),
            correlated_usd_risk_scale=_env_float("CORRELATED_USD_RISK_SCALE", cls.correlated_usd_risk_scale),
            # Dynamic Profit Maximization Engine
            delta_absorption_mode=os.getenv("DELTA_ABSORPTION_MODE", cls.delta_absorption_mode),
            enable_split_tranche_runner=_env_bool("ENABLE_SPLIT_TRANCHE_RUNNER", cls.enable_split_tranche_runner),
            runner_tranche_pct=_env_float("RUNNER_TRANCHE_PCT", cls.runner_tranche_pct),
            runner_trail_atr_mult=_env_float("RUNNER_TRAIL_ATR_MULT", cls.runner_trail_atr_mult),
            fvg_adaptive_retest_tolerance_pct=_env_float("FVG_ADAPTIVE_RETEST_TOLERANCE_PCT", cls.fvg_adaptive_retest_tolerance_pct),
            enable_fvg_pyramiding=_env_bool("ENABLE_FVG_PYRAMIDING", cls.enable_fvg_pyramiding),
        )



@dataclass
class Config:
    mt5: MT5Config = field(default_factory=MT5Config)
    trading: TradingConfig = field(default_factory=TradingConfig)

    @classmethod
    def load(cls, env_path: Optional[str] = None, config_path: Optional[str] = None) -> "Config":
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()
        cfg = cls(mt5=MT5Config.from_env(), trading=TradingConfig.from_env())
        if config_path:
            resolved = Path(config_path).resolve()
            if not resolved.exists():
                log.warning("Config file not found: %s", config_path)
            else:
                with open(resolved) as f:
                    overrides = json.load(f)
                mt5_overrides = overrides.get("mt5", {})
                for k, v in mt5_overrides.items():
                    if hasattr(cfg.mt5, k):
                        setattr(cfg.mt5, k, v)
                trading_overrides = overrides.get("trading", {})
                for k, v in trading_overrides.items():
                    if hasattr(cfg.trading, k):
                        setattr(cfg.trading, k, v)
        # Validate file paths to prevent injection
        for attr in ("state_db_path", "trade_log_path", "log_file", "research_report_dir"):
            p = Path(getattr(cfg.trading, attr))
            if ".." in p.parts:
                log.warning("Path traversal detected in %s: %s — using default", attr, p)
                setattr(cfg.trading, attr, getattr(TradingConfig, attr))
        return cfg
