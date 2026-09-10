import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Set, Tuple

import MetaTrader5 as mt5

from ..config import TradingConfig
from ..models import AccountInfo, Signal, TradeDirection, TradeLeg, TradeStatus
from ..broker.mt5_connector import MT5Connector

log = logging.getLogger("xauusd_bot.order.entry")


class SignalReservation:
    """Thread-safe atomic reservation tracker to eliminate duplicate fill race conditions."""
    def __init__(self):
        self._lock = threading.Lock()
        self._reserved_signals: Set[str] = set()
        self._active_symbols: Set[str] = set()

    def reserve(self, signal_id: str, symbol: str) -> bool:
        with self._lock:
            if signal_id in self._reserved_signals:
                log.warning("Signal %s already reserved — rejecting duplicate order", signal_id)
                return False
            if symbol in self._active_symbols:
                log.warning("Symbol %s has active in-flight order — rejecting concurrent submission", symbol)
                return False
            self._reserved_signals.add(signal_id)
            self._active_symbols.add(symbol)
            return True

    def release(self, signal_id: str, symbol: str):
        with self._lock:
            self._reserved_signals.discard(signal_id)
            self._active_symbols.discard(symbol)


class OrderEntry:
    def __init__(self, connector: MT5Connector, config: TradingConfig):
        self.connector = connector
        self.config = config
        self.reservation = SignalReservation()

    def validate_broker_spec(self, symbol: str, entry_price: float, sl_price: float) -> Tuple[bool, str]:
        """Validate broker contract specification, tick size, and minimum stop level."""
        info = self.connector.symbol_info(symbol)
        if info is None:
            return True, "No MT5 symbol info available (offline/dry-run)"

        point = getattr(info, "point", 0.01)
        stops_level = getattr(info, "trade_stops_level", 0) * point
        freeze_level = getattr(info, "trade_freeze_level", 0) * point
        sl_dist = abs(entry_price - sl_price)

        min_allowed = max(stops_level, freeze_level, 2 * point)
        if sl_dist < min_allowed:
            return False, f"SL distance {sl_dist:.4f} below broker minimum stops_level {min_allowed:.4f}"
        return True, "Broker spec valid"

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


    def get_filling_mode(self, symbol: str) -> int:
        """Resolve the appropriate MT5 order filling mode based on symbol specs.
        
        MT5 filling_mode bitmask:
            SYMBOL_FILLING_FOK = 1
            SYMBOL_FILLING_IOC = 2
        """
        try:
            info = self.connector.symbol_info(symbol)
            if info is not None and hasattr(info, "filling_mode"):
                mode = info.filling_mode
                if mode & 2:
                    return mt5.ORDER_FILLING_IOC
                elif mode & 1:
                    return mt5.ORDER_FILLING_FOK
                elif mode == 0:
                    return mt5.ORDER_FILLING_RETURN
        except Exception as e:
            log.warning("Could not query filling_mode for %s: %s", symbol, e)
        return mt5.ORDER_FILLING_IOC

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

        filling_mode = self.get_filling_mode(symbol)
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
            "type_filling": filling_mode,
        }
        result = self.connector.order_send(request)
        if result is None:
            # Fallback across supported filling modes in case broker rejects mode (Error 10030)
            fallback_modes = [m for m in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN) if m != filling_mode]
            for alt_mode in fallback_modes:
                log.info("Retrying order_send with fallback filling mode %d for %s", alt_mode, symbol)
                request["type_filling"] = alt_mode
                result = self.connector.order_send(request)
                if result is not None:
                    break

        if result is None:
            return None

        # Slippage audit and verification against configured deviation points
        point = 0.01
        try:
            sym_info = self.connector.symbol_info(symbol)
            if sym_info and getattr(sym_info, "point", 0):
                point = sym_info.point
        except Exception:
            pass
        slippage_points = abs(result.price - price) / point
        if slippage_points > self.config.deviation_points:
            log.warning(
                "Execution slippage on %s: %.1f points (deviation limit: %d) — requested %.5f, filled %.5f",
                symbol, slippage_points, self.config.deviation_points, price, result.price
            )
        else:
            log.debug("Execution slippage on %s: %.1f points", symbol, slippage_points)

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
        filling_mode = self.get_filling_mode(sym)
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
            "type_filling": filling_mode,
        }
        result = self.connector.order_send(request)
        if result is None:
            fallback_modes = [m for m in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN) if m != filling_mode]
            for alt_mode in fallback_modes:
                request["type_filling"] = alt_mode
                result = self.connector.order_send(request)
                if result is not None:
                    break
        return result is not None

    def place_limit_order(
        self,
        signal: Signal,
        limit_price: float,
        account: AccountInfo,
        point_value: float = 1.0,
        contract_size: int = 100,
        min_lot: float = 0.01,
        lot_step: float = 0.01,
        comment: str = "FVG_Limit",
    ) -> Optional[TradeLeg]:
        """Place an MT5 Pending Limit Order (BUY_LIMIT / SELL_LIMIT) at the FVG retracement level."""
        if not self.connector.ensure_connected():
            return None
        if not self._validate_lot(signal.lot_size):
            return None

        symbol = getattr(signal, "symbol", None) or self.config.symbol

        # 1. Atomic order reservation to prevent duplicate-fill race conditions
        if not self.reservation.reserve(signal.id, symbol):
            log.warning("[%s] Signal %s blocked by atomic reservation", symbol, signal.id[:8])
            return None

        # 2. Broker contract specification & stops_level validation
        spec_ok, spec_msg = self.validate_broker_spec(symbol, limit_price, signal.sl_price)
        if not spec_ok:
            log.warning("[%s] Broker spec validation failed: %s", symbol, spec_msg)
            self.reservation.release(signal.id, symbol)
            return None

        order_type = mt5.ORDER_TYPE_BUY_LIMIT if signal.direction == TradeDirection.BUY else mt5.ORDER_TYPE_SELL_LIMIT
        filling_mode = self.get_filling_mode(symbol)

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": signal.lot_size,
            "type": order_type,
            "price": limit_price,
            "sl": signal.sl_price,
            "tp": signal.tp_price,
            "deviation": self.config.deviation_points,
            "magic": self.config.magic_number,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }
        result = self.connector.order_send(request)
        if result is None:
            fallback_modes = [m for m in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN) if m != filling_mode]
            for alt_mode in fallback_modes:
                request["type_filling"] = alt_mode
                result = self.connector.order_send(request)
                if result is not None:
                    break

        if result is None:
            log.error("Failed placing limit order on %s at %.2f", symbol, limit_price)
            self.reservation.release(signal.id, symbol)
            return None

        leg = TradeLeg(
            position_ticket=result.order,
            symbol=symbol,
            direction=signal.direction,
            entry_price=limit_price,
            lot_size=signal.lot_size,
            sl_price=signal.sl_price,
            tp_price=signal.tp_price,
            open_time=datetime.now(timezone.utc).replace(tzinfo=None),
            status=TradeStatus.PENDING,
        )
        log.info("Limit order placed: %s %s %.2f lots at %.2f (ticket=%d)",
                 symbol, signal.direction.value, signal.lot_size, limit_price, result.order)
        return leg

    def cancel_order(self, ticket: int, signal_id: Optional[str] = None, symbol: Optional[str] = None) -> bool:
        """Cancel a pending MT5 limit order (e.g. upon FVG 5-bar expiration)."""
        if not self._validate_ticket(ticket):
            return False
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
        }
        result = self.connector.order_send(request)
        if signal_id and symbol:
            self.reservation.release(signal_id, symbol)
        if result is None:
            log.warning("Failed cancelling pending order ticket %d", ticket)
            return False
        log.info("Cancelled pending order ticket %d", ticket)
        return True


