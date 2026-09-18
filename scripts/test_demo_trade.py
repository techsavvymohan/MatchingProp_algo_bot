"""
Instant Demo Test Trade Executor
Places a single verified demo order directly via MetaTrader 5 using .env configuration.
Useful for testing terminal connection, broker execution latency, and order visibility in MT5.
"""

import argparse
import sys
import time
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from xauusd_bot.config import Config


def main():
    parser = argparse.ArgumentParser(description="Place a single test trade on MT5 demo account")
    parser.add_argument("--symbol", type=str, default="XAUUSD", help="Symbol to trade (default: XAUUSD)")
    parser.add_argument("--action", type=str, default="BUY", choices=["BUY", "SELL"], help="Trade direction (default: BUY)")
    parser.add_argument("--lots", type=float, default=0.01, help="Lot volume (default: 0.01)")
    parser.add_argument("--close-after", type=int, default=0, help="Automatically close position after N seconds (0 = keep open)")
    args = parser.parse_args()

    cfg = Config.load()

    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("[ERROR] MetaTrader5 package is not installed in current Python environment.")
        return 1

    print(f"\n=================================================================")
    print(f"  INSTANT DEMO TRADE TEST")
    print(f"=================================================================")
    print(f"Connecting to MT5 account: {cfg.mt5.login} on {cfg.mt5.server}...")

    if not mt5.initialize(
        path=cfg.mt5.path or "",
        login=cfg.mt5.login,
        password=cfg.mt5.password,
        server=cfg.mt5.server,
        timeout=cfg.mt5.timeout_ms or 15000,
    ):
        print(f"[ERROR] MT5 connection failed: {mt5.last_error()}")
        return 1

    terminal = mt5.terminal_info()
    if terminal and not terminal.trade_allowed:
        print("[WARNING] MT5 'Algo Trading' button is disabled! Please click 'Algo Trading' in MT5.")
        return 1

    symbol = args.symbol.upper()
    if not mt5.symbol_select(symbol, True):
        # Try finding broker symbol with suffix (e.g. XAUUSD.r, XAUUSD.x, GOLD)
        all_syms = [s.name for s in mt5.symbols_get() or []]
        matches = [s for s in all_syms if symbol in s.upper()]
        if matches:
            symbol = matches[0]
            mt5.symbol_select(symbol, True)
        else:
            print(f"[ERROR] Symbol {symbol} not found on broker feed.")
            mt5.shutdown()
            return 1

    tick = mt5.symbol_info_tick(symbol)
    sym_info = mt5.symbol_info(symbol)
    if not tick or not sym_info:
        print(f"[ERROR] Failed to retrieve live quote for {symbol}.")
        mt5.shutdown()
        return 1

    digits = sym_info.digits
    point = sym_info.point
    min_lot = sym_info.volume_min or 0.01
    lots = max(args.lots, min_lot)

    # Determine execution price, SL, and TP
    is_gold = "XAU" in symbol or "GOLD" in symbol
    if args.action == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        sl_dist = 5.0 if is_gold else 0.0015
        tp_dist = 10.0 if is_gold else 0.0030
        sl = round(price - sl_dist, digits)
        tp = round(price + tp_dist, digits)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        sl_dist = 5.0 if is_gold else 0.0015
        tp_dist = 10.0 if is_gold else 0.0030
        sl = round(price + sl_dist, digits)
        tp = round(price - tp_dist, digits)

    # Determine filling mode
    filling_mode = mt5.ORDER_FILLING_IOC
    if sym_info.filling_mode & 2:
        filling_mode = mt5.ORDER_FILLING_IOC
    elif sym_info.filling_mode & 1:
        filling_mode = mt5.ORDER_FILLING_FOK
    elif sym_info.filling_mode == 0:
        filling_mode = mt5.ORDER_FILLING_RETURN

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": cfg.trading.magic_number,
        "comment": "DEMO_TEST_TRADE",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    print(f"\nSubmitting Order:")
    print(f"  * Symbol      : {symbol}")
    print(f"  * Direction   : {args.action}")
    print(f"  * Volume      : {lots} lot")
    print(f"  * Market Price: {price}")
    print(f"  * Stop Loss   : {sl}")
    print(f"  * Take Profit : {tp}")

    result = mt5.order_send(request)
    if result is None:
        print(f"[FAIL] Order placement failed: {mt5.last_error()}")
        mt5.shutdown()
        return 1

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        # Try fallback filling mode
        alt_mode = mt5.ORDER_FILLING_FOK if filling_mode == mt5.ORDER_FILLING_IOC else mt5.ORDER_FILLING_IOC
        request["type_filling"] = alt_mode
        result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"\n=================================================================")
        print(f"  [SUCCESS] DEMO TRADE EXECUTED SUCCESSFULLY!")
        print(f"=================================================================")
        print(f"  * MT5 Order Ticket : #{result.order}")
        print(f"  * Executed Price   : {result.price}")
        print(f"  * Volume Filled    : {result.volume} lots")
        print(f"  * Broker Retcode   : {result.retcode} (DONE)")
        print(f"\n-> Look at your MetaTrader 5 Trade Terminal tab: Order #{result.order} is now LIVE!")

        if args.close_after > 0:
            print(f"\nAuto-close requested: waiting {args.close_after} seconds before closing...")
            time.sleep(args.close_after)
            close_tick = mt5.symbol_info_tick(symbol)
            close_price = close_tick.bid if args.action == "BUY" else close_tick.ask
            close_type = mt5.ORDER_TYPE_SELL if args.action == "BUY" else mt5.ORDER_TYPE_BUY
            close_request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": result.volume,
                "type": close_type,
                "position": result.order,
                "price": close_price,
                "deviation": 20,
                "magic": cfg.trading.magic_number,
                "comment": "DEMO_TEST_CLOSE",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }
            close_res = mt5.order_send(close_request)
            if close_res and close_res.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"  [SUCCESS] Position #{result.order} closed at {close_price}!")
            else:
                print(f"  [WARN] Failed to auto-close: {getattr(close_res, 'comment', 'N/A')}")
    else:
        print(f"\n[FAIL] Broker rejected order with retcode {result.retcode}: {result.comment}")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
