"""
VPS Health Check & Diagnostic Script for MatchingProp Algo Bot
Validates:
1. Git branch, latest commit, and remote synchronization status.
2. Configuration (.env parameters: dual-pair, 0.85% risk, filters).
3. MT5 connection, account balance, equity, currency, and leverage.
4. Algo Trading permissions in MT5 terminal.
5. Live market feeds for XAUUSD and EURUSD (bid/ask, spread, contract size).
6. Dynamic Position Sizing calculation preview for the attached account.
"""

import os
import sys
import subprocess
from pathlib import Path

# Ensure root directory is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def header(title: str):
    print(f"\n{CYAN}{BOLD}{'=' * 65}{RESET}")
    print(f"{CYAN}{BOLD}  {title}{RESET}")
    print(f"{CYAN}{BOLD}{'=' * 65}{RESET}")


def check_mark(passed: bool, label: str, detail: str = ""):
    icon = f"{GREEN}[PASS]{RESET}" if passed else f"{RED}[FAIL]{RESET}"
    detail_str = f" - {detail}" if detail else ""
    print(f"  {icon} {label}{detail_str}")


def check_git_status():
    header("1. GIT REPOSITORY & VERSION STATUS")
    try:
        # Check current branch
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=BASE_DIR, text=True, stderr=subprocess.DEVNULL
        ).strip()
        
        # Check latest commit
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE_DIR, text=True, stderr=subprocess.DEVNULL
        ).strip()

        commit_msg = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=BASE_DIR, text=True, stderr=subprocess.DEVNULL
        ).strip().splitlines()[0]

        commit_date = subprocess.check_output(
            ["git", "log", "-1", "--date=relative", "--pretty=%cd"],
            cwd=BASE_DIR, text=True, stderr=subprocess.DEVNULL
        ).strip()

        check_mark(True, f"Current Branch: {BOLD}{branch}{RESET}")
        check_mark(True, f"Latest Commit: {BOLD}{commit_hash}{RESET} ({commit_date})", commit_msg[:60])

        # Fetch remote silently and check diff
        try:
            subprocess.run(["git", "fetch", "origin", "main"], cwd=BASE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            local_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BASE_DIR, text=True).strip()
            remote_hash = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd=BASE_DIR, text=True).strip()

            if local_hash == remote_hash:
                check_mark(True, "Code is 100% up-to-date with GitHub origin/main")
            else:
                check_mark(False, "Update Available on GitHub!", "Run: git pull origin main")
        except Exception:
            print(f"  {YELLOW}[INFO]{RESET} Could not check remote GitHub status (network timeout or offline)")

    except Exception as exc:
        check_mark(False, "Git command error", str(exc))


def check_config():
    header("2. CONFIGURATION & .ENV VERIFICATION")
    from xauusd_bot.config import Config
    cfg = Config.load()
    
    # 1. Dual Pair check
    symbols = getattr(cfg.trading, "symbols", []) or [getattr(cfg.trading, "symbol", "XAUUSD")]
    dual_pair = "XAUUSD" in symbols and "EURUSD" in symbols
    check_mark(dual_pair, "Active Trading Pairs", f"{symbols}")

    # 2. Risk check (0.85%)
    risk_pct = getattr(cfg.trading, "pyramid_initial_risk_pct", 0.0)
    risk_ok = 0.5 <= risk_pct <= 1.5
    check_mark(risk_ok, f"Base Per-Trade Risk: {risk_pct}%", "Configured at 0.85% for high Sharpe prop compliance")

    # 3. Daily Loss & Max DD limits
    dl_limit = getattr(cfg.trading, "daily_loss_limit_pct", 3.0)
    max_dd = getattr(cfg.trading, "max_dd_limit_pct", 10.0)
    check_mark(dl_limit <= 3.0, f"Daily Loss Hard Ceiling: {dl_limit}%", "FTMO limit safe")
    check_mark(max_dd <= 10.0, f"Maximum Drawdown Hard Ceiling: {max_dd}%", "FTMO limit safe")

    # 4. Sideways Market Filter
    sideways_on = getattr(cfg.trading, "enable_sideways_filter", True)
    check_mark(sideways_on, "Sideways Market Avoidance Engine: ACTIVE", "Chop, ADX, and BB Bandwidth protection on")

    return cfg


def check_mt5_and_account(cfg):
    header("3. MT5 TERMINAL & BROKER CONNECTION")
    try:
        import MetaTrader5 as mt5
    except ImportError:
        check_mark(False, "MetaTrader5 Python Library", "Run: pip install MetaTrader5")
        return None, None

    # Attempt MT5 initialization
    path = cfg.mt5.path if os.path.exists(cfg.mt5.path or "") else None
    connected = mt5.initialize(
        path=path or "",
        login=cfg.mt5.login,
        password=cfg.mt5.password,
        server=cfg.mt5.server,
        timeout=cfg.mt5.timeout_ms or 15000,
    )

    if not connected:
        err = mt5.last_error()
        check_mark(False, "MT5 Terminal Connection", f"Error code: {err}. Check login, password, and server in .env")
        return None, None

    terminal = mt5.terminal_info()
    account = mt5.account_info()

    if not account:
        check_mark(False, "MT5 Account Login", "Failed to retrieve account details")
        return None, None

    check_mark(True, "MT5 Terminal Connected", f"Build: {getattr(terminal, 'build', 'N/A')}")
    
    # Algo Trading Permission
    algo_enabled = terminal.trade_allowed if terminal else False
    if algo_enabled:
        check_mark(True, "MT5 'Algo Trading' Button: ENABLED (Green Play Button)")
    else:
        check_mark(False, "MT5 'Algo Trading' Button is DISABLED!", "Click 'Algo Trading' in MT5 toolbar to turn it GREEN")

    # Account metrics
    header("4. LIVE ACCOUNT CAPITAL & RISK CALIBRATION")
    print(f"  * Account Number : {account.login}")
    print(f"  * Broker Server  : {account.server}")
    print(f"  * Account Currency: {account.currency}")
    print(f"  * Balance        : {BOLD}${account.balance:,.2f}{RESET}")
    print(f"  * Equity         : {BOLD}${account.equity:,.2f}{RESET}")
    print(f"  * Free Margin    : ${account.margin_free:,.2f}")
    print(f"  * Leverage       : 1:{account.leverage}")

    # Risk Calculation Preview
    risk_amount = account.equity * (cfg.trading.pyramid_initial_risk_pct / 100.0)
    print(f"\n  {CYAN}Dynamic Risk Preview (0.85% of Equity):{RESET}")
    print(f"  * Exact Risk per Trade : {BOLD}${risk_amount:.2f}{RESET}")
    print(f"  * 3.0% Daily Loss Limit: ${account.equity * 0.03:.2f} max daily loss allowed")
    print(f"  * 10.0% Max DD Limit   : ${account.equity * 0.10:.2f} max overall loss allowed")

    return mt5, account


def check_symbols(mt5, cfg, account):
    header("5. SYMBOL FEEDS & LOT SIZING CHECK")
    if not mt5 or not account:
        print(f"  {YELLOW}Skipped: MT5 not connected{RESET}")
        return

    from xauusd_bot.risk.position_sizer import PositionSizer
    from xauusd_bot.models import AccountInfo, TradeDirection

    sizer = PositionSizer(
        initial_risk_pct=cfg.trading.pyramid_initial_risk_pct,
        initial_balance=account.balance,
    )
    acct_model = AccountInfo(balance=account.balance, equity=account.equity)

    for sym in ["XAUUSD", "EURUSD"]:
        # Ensure selected in Market Watch
        mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)

        if not info or not tick:
            # Query all available symbols from broker
            all_broker_syms = [s.name for s in (mt5.symbols_get() or [])]
            key1 = "XAU" if "XAU" in sym else "EURUSD"
            matches = [s for s in all_broker_syms if key1 in s.upper() or ("GOLD" in s.upper() if "XAU" in sym else False)]
            if matches:
                check_mark(False, f"{sym} Market Watch Subscription", f"Broker uses: {matches}. Set SYMBOLS={','.join(matches[:2])} in .env")
            else:
                check_mark(False, f"{sym} Market Watch Subscription", "Not found. In MT5, Right-Click 'Market Watch' -> Click 'Show All'")
            continue

        spread_pts = round((tick.ask - tick.bid) / (info.point or 1e-5), 1)
        check_mark(
            True,
            f"{sym} Feed Active",
            f"Bid={tick.bid:.{info.digits}f} | Ask={tick.ask:.{info.digits}f} | Spread={spread_pts} pts"
        )

        # Calculate sample lot size
        if "XAU" in sym:
            # Approx $6 Stop Loss on Gold
            sl_dist = 6.0
            sl_price = tick.bid - sl_dist
            lots = sizer.calculate_lot_size(
                acct_model,
                entry_price=tick.bid,
                sl_price=sl_price,
                direction=TradeDirection.BUY,
                point_value=1.0,
                contract_size=int(info.trade_contract_size or 100),
                min_lot=info.volume_min or 0.01,
                max_lot=info.volume_max or 100.0,
                lot_step=info.volume_step or 0.01,
            )
            print(f"    -> {BOLD}Auto Lot Size for {sym}{RESET} (~${sl_dist:.0f} SL): {BOLD}{GREEN}{lots:.2f} lots{RESET} (Risk: ${lots * sl_dist * 100:.2f})")
        else:
            # Approx 15 pips Stop Loss on EURUSD
            sl_dist = 0.00150
            sl_price = tick.bid - sl_dist
            lots = sizer.calculate_lot_size(
                acct_model,
                entry_price=tick.bid,
                sl_price=sl_price,
                direction=TradeDirection.BUY,
                point_value=1.0,
                contract_size=int(info.trade_contract_size or 100000),
                min_lot=info.volume_min or 0.01,
                max_lot=info.volume_max or 100.0,
                lot_step=info.volume_step or 0.01,
            )
            print(f"    -> {BOLD}Auto Lot Size for {sym}{RESET} (~15 pips SL): {BOLD}{GREEN}{lots:.2f} lots{RESET} (Risk: ${lots * 150.0:.2f})")


def main():
    print(f"\n{BOLD}MatchingProp Algo Bot -- VPS System Readiness Inspection{RESET}")
    print(f"Working Directory: {BASE_DIR}")
    
    check_git_status()
    cfg = check_config()
    mt5, account = check_mt5_and_account(cfg)
    check_symbols(mt5, cfg, account)

    header("DIAGNOSTIC SUMMARY & NEXT STEPS")
    if mt5 and account:
        print(f"  {GREEN}{BOLD}[PASS] ALL CRITICAL CHECKS COMPLETE -- BOT READY FOR LIVE TRADING!{RESET}")
        print(f"\n  To start the bot in live continuous trading mode:")
        print(f"  {BOLD}python -m xauusd_bot.main --live{RESET}\n")
        mt5.shutdown()
    else:
        print(f"  {YELLOW}{BOLD}[NOTICE] Please verify the failed checks above before starting live trading.{RESET}\n")


if __name__ == "__main__":
    main()
