import logging
from datetime import datetime
from typing import Optional

from ..models import AccountInfo

log = logging.getLogger("xauusd_bot.broker.account")


class AccountManager:
    def __init__(self, connector):
        self.connector = connector
        self._last_info: Optional[AccountInfo] = None
        self._symbol_cache: dict = {}
        self._tick_cache: dict = {}

    def _symbol_info(self, symbol: str = "XAUUSD"):
        if symbol not in self._symbol_cache:
            info = self.connector.symbol_info(symbol)
            if info is not None:
                self._symbol_cache[symbol] = info
        return self._symbol_cache.get(symbol)

    def _symbol_info_tick(self, symbol: str = "XAUUSD"):
        tick = self.connector.symbol_info_tick(symbol)
        if tick is not None:
            self._tick_cache[symbol] = tick
        return self._tick_cache.get(symbol)

    def refresh(self) -> Optional[AccountInfo]:
        if not self.connector.ensure_connected():
            return None
        info = self.connector.account_info()
        if info is None:
            log.warning("account_info returned None")
            return None
        self._symbol_cache.clear()
        self._tick_cache.clear()
        tick = self._symbol_info_tick()
        self._last_info = AccountInfo(
            balance=info.balance,
            equity=info.equity,
            margin=info.margin,
            margin_free=info.margin_free,
            margin_level=info.margin_level,
            leverage=info.leverage,
            currency=info.currency,
            server_time=datetime.fromtimestamp(tick.time) if tick else None,
        )
        return self._last_info

    @property
    def current(self) -> Optional[AccountInfo]:
        return self._last_info

    def current_spread(self, symbol: str = "XAUUSD") -> float:
        tick = self._symbol_info_tick(symbol)
        if tick is None:
            return 0.0
        point = getattr(tick, "point", None) or self.point_size(symbol)
        return (tick.ask - tick.bid) / point if point else 0.0

    def point_value(self, symbol: str = "XAUUSD") -> float:
        info = self._symbol_info(symbol)
        if info is None:
            return 1.0 if "EUR" in symbol else 0.01
        return info.trade_tick_value or (1.0 if "EUR" in symbol else 0.01)

    def contract_size(self, symbol: str = "XAUUSD") -> int:
        info = self._symbol_info(symbol)
        if info and info.trade_contract_size:
            return int(info.trade_contract_size)
        return 100000 if "EUR" in symbol else 100

    def lot_step(self, symbol: str = "XAUUSD") -> float:
        info = self._symbol_info(symbol)
        return info.volume_step if info and info.volume_step else 0.01

    def min_lot(self, symbol: str = "XAUUSD") -> float:
        info = self._symbol_info(symbol)
        return info.volume_min if info and info.volume_min else 0.01

    def max_lot(self, symbol: str = "XAUUSD") -> float:
        info = self._symbol_info(symbol)
        return info.volume_max if info and info.volume_max else 100.0

    def point_size(self, symbol: str = "XAUUSD") -> float:
        info = self._symbol_info(symbol)
        if info and hasattr(info, "point") and isinstance(info.point, (int, float)):
            return info.point
        return 0.0001

    def tick_size(self, symbol: str = "XAUUSD") -> float:
        info = self._symbol_info(symbol)
        if info and getattr(info, "trade_tick_size", None):
            return info.trade_tick_size
        return self.point_size(symbol)
