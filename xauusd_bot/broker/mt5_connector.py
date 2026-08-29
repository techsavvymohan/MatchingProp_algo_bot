import logging
import time
from typing import Optional

import MetaTrader5 as mt5

from ..config import MT5Config

log = logging.getLogger("xauusd_bot.broker")


class MT5Connector:
    def __init__(self, cfg: MT5Config):
        self.cfg = cfg
        self._connected = False
        self._reconnect_attempts = 0

    def connect(self) -> bool:
        if self._connected:
            return True
        if not mt5.initialize(
            path=self.cfg.path,
            login=self.cfg.login,
            password=self.cfg.password,
            server=self.cfg.server,
            timeout=self.cfg.timeout_ms,
        ):
            err = mt5.last_error()
            log.error("MT5 initialize failed: %s", err)
            return False
        self._connected = True
        log.info("MT5 connected — account %s on %s", self.cfg.login, self.cfg.server)
        return True

    def disconnect(self):
        if self._connected:
            mt5.shutdown()
            self._connected = False
            log.info("MT5 disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected and mt5.terminal_info() is not None

    def reconnect(self, max_retries: int = 3, delay: float = 2.0) -> bool:
        self.disconnect()
        for attempt in range(1, max_retries + 1):
            log.info("Reconnect attempt %d/%d", attempt, max_retries)
            if self.connect():
                return True
            if attempt < max_retries:
                time.sleep(delay)
        return False

    def ensure_connected(self) -> bool:
        if self.is_connected:
            self._reconnect_attempts = 0
            return True
        delay = min(2.0 * (2 ** self._reconnect_attempts), 60.0)
        self._reconnect_attempts += 1
        log.warning("Reconnecting (attempt %d, delay=%.1fs)", self._reconnect_attempts, delay)
        time.sleep(delay)
        return self.reconnect()

    def symbol_info(self, symbol: str = "XAUUSD"):
        return mt5.symbol_info(symbol)

    def symbol_info_tick(self, symbol: str = "XAUUSD"):
        return mt5.symbol_info_tick(symbol)

    def account_info(self):
        return mt5.account_info()

    def positions_get(self, symbol: str = "", magic: int = 0):
        kwargs = {}
        if symbol:
            kwargs["symbol"] = symbol
        if magic:
            kwargs["magic"] = magic
        return mt5.positions_get(**kwargs) or []

    def order_send(self, request: dict) -> Optional[dict]:
        result = mt5.order_send(request)
        if result is None:
            log.error("order_send returned None — MT5 error: %s", mt5.last_error())
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error("Order failed retcode=%d: %s", result.retcode, result.comment)
            return None
        return result

    def history_deals_get(self, from_date, to_date):
        return mt5.history_deals_get(from_date, to_date) or []

    def copy_rates_from_pos(self, symbol: str, tf: int, start: int, count: int):
        return mt5.copy_rates_from_pos(symbol, tf, start, count)

    @staticmethod
    def tf_to_mt5(tf: str) -> int:
        mapping = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
        }
        return mapping.get(tf, mt5.TIMEFRAME_M1)
