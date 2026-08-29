import logging
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5

from ..config import TradingConfig
from ..models import AccountInfo, Signal, TradeDirection, TradeLeg, TradeStatus
from ..broker.mt5_connector import MT5Connector

log = logging.getLogger("xauusd_bot.order.entry")


class OrderEntry:
    def __init__(self, connector: MT5Connector, config: TradingConfig):
        self.connector = connector
        self.config = config

    def _validate_lot(self, volume: float) -> bool:
        if volume <= 0:
            log.error("Invalid lot size: %.4f", volume)
            return False
        return True

    def _validate_ticket(self, ticket: int) -> bool:
        if ticket <= 0:
            log.error("Invalid ticket: %d", ticket)
            return False
        return True

    def place_market_order(
        self,
        signal: Signal,
        account: AccountInfo,
        point_value: float,
        contract_size: int,
        min_lot: float = 0.01,
        lot_step: float = 0.01,
    ) -> Optional[TradeLeg]:
        if not self.connector.ensure_connected():
            return None
        if not self._validate_lot(signal.lot_size):
            return None
        symbol = getattr(signal, "symbol", None) or self.config.symbol
        tick = self.connector.symbol_info_tick(symbol)
        if tick is None:
            log.error("No tick for %s", symbol)
            return None
        if signal.direction == TradeDirection.BUY:
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY
        else:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": signal.lot_size,
            "type": order_type,
            "price": price,
            "sl": signal.sl_price,
            "tp": signal.tp_price,
            "deviation": self.config.deviation_points,
            "magic": self.config.magic_number,
            "comment": self.config.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = self.connector.order_send(request)
        if result is None:
            return None
        leg = TradeLeg(
            position_ticket=result.order,
            symbol=symbol,
            direction=signal.direction,
            entry_price=result.price,
            lot_size=signal.lot_size,
            sl_price=signal.sl_price,
            tp_price=signal.tp_price,
            open_time=datetime.now(timezone.utc).replace(tzinfo=None),
            status=TradeStatus.OPEN,
        )
        log.info("Order filled: %s %s %.2f lots at %.2f (ticket=%d)",
                 symbol, signal.direction.value, signal.lot_size, result.price, result.order)
        return leg

    def modify_sl_tp(self, ticket: int, sl: float, tp: float) -> bool:
        if not self._validate_ticket(ticket):
            return False
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": sl,
            "tp": tp,
        }
        result = self.connector.order_send(request)
        if result is None:
            return False
        return True

    def close_position(self, ticket: int, volume: float, direction: TradeDirection, symbol: str = "") -> bool:
        if not self._validate_ticket(ticket) or not self._validate_lot(volume):
            return False
        sym = symbol or self.config.symbol
        tick = self.connector.symbol_info_tick(sym)
        if tick is None:
            return False
        close_type = mt5.ORDER_TYPE_SELL if direction == TradeDirection.BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if direction == TradeDirection.BUY else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": self.config.deviation_points,
            "magic": self.config.magic_number,
            "comment": "close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = self.connector.order_send(request)
        return result is not None
