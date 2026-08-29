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
    daily_loss_buffer_pct: float = 1.0
    max_dd_buffer_pct: float = 2.0

    max_pyramid_entries: int = 4
    pyramid_add_trigger_r: float = 0.5
    pyramid_initial_risk_pct: float = 0.25

    partial_take_profit_r: float = 1.0
    partial_close_pct: float = 50.0

    time_based_exit_minutes: int = 120
    max_r_multiple: float = 2.5

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

    deviation_points: int = 20

    vwap_period: int = 20

    min_structure_swing_bars: int = 5
    max_structure_swing_bars: int = 20

    signal_score_a_min: int = 8
    signal_score_b_min: int = 5

    session_london_open: str = "08:00"
    session_london_close: str = "17:00"
    session_ny_open: str = "13:00"
    session_ny_close: str = "22:00"

    broker_daily_reset_hour: int = 0
    broker_daily_reset_tz: str = "UTC"

    logging_level: str = "INFO"
    log_file: str = "logs/xauusd_bot.log"
    telegram_token: str = ""
    telegram_chat_id: str = ""

    state_db_path: str = "data/bot_state.db"
    trade_log_path: str = "data/trade_log.csv"

    poll_interval_ms: int = 1000

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
            poll_interval_ms=_env_int("POLL_INTERVAL_MS", cls.poll_interval_ms),
            backtest_initial_balance=_env_float("BACKTEST_INITIAL_BALANCE", cls.backtest_initial_balance),
            backtest_commission_pct=_env_float("BACKTEST_COMMISSION_PCT", cls.backtest_commission_pct),
            backtest_slippage_points=_env_float("BACKTEST_SLIPPAGE_POINTS", cls.backtest_slippage_points),
            backtest_spread_points=_env_float("BACKTEST_SPREAD_POINTS", cls.backtest_spread_points),
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
        for attr in ("state_db_path", "trade_log_path", "log_file"):
            p = Path(getattr(cfg.trading, attr))
            if ".." in p.parts:
                log.warning("Path traversal detected in %s: %s — using default", attr, p)
                setattr(cfg.trading, attr, getattr(TradingConfig, attr))
        return cfg
