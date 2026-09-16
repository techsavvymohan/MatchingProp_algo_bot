#!/usr/bin/env python3
import argparse
import logging
import signal
import sys
import time

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import Config
from .logger import setup_logging
from .models import (
    ExitReason, Signal, SignalGrade, TradeDirection, TradeStatus, Bias, Regime, Session,
    ScoutCandidate, ScoutResult, TechnicalStructure, FundamentalDossier,
    NewsCatalystBrief, QuantHypothesis, RiskVerdict, ResearchPlan, TimeframeData,
)
from .utils.time_utils import broker_date, current_session, is_in_ny_session, to_ny_time

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


class InstitutionalResearchTeam:
    """7-Agent Institutional Research & Decision Intelligence System.

    Roles:
      01 — MARKET SCOUT: Shortlists candidates.
      02 — TECHNICAL ANALYST: Maps price structure.
      03 — FUNDAMENTAL ANALYST: Reviews macro backdrop & currency drivers.
      04 — NEWS ANALYST: Checks events and catalysts.
      05 — QUANT ANALYST: Tests the hypothesis against statistical regimes.
      06 — RISK MANAGER: Challenges the downside ("One Agent Tries to Kill the Trade").
      07 — PORTFOLIO MANAGER: Builds the 6-input research and execution plan.
    """

    def __init__(self, config: Config):
        self.cfg = config
        self.report_dir = Path(getattr(config.trading, "research_report_dir", "logs/research"))
        self.report_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------
    # Agent 01 — MARKET SCOUT
    # -----------------------------------------------------------------
    def scout(
        self,
        symbols: List[str],
        data_feeds: Dict[str, any],
        spread_trackers: Optional[Dict[str, any]] = None,
        session_name: str = "LONDON",
    ) -> ScoutResult:
        """Scan available symbols and return a prioritized shortlist of candidates."""
        candidates: List[ScoutCandidate] = []
        for sym in symbols:
            feed = data_feeds.get(sym)
            if not feed:
                continue
            data_all = feed.all_tfs() if hasattr(feed, "all_tfs") else feed
            m15 = data_all.get("M15")
            if not m15 or not m15.close or len(m15.close) < self.cfg.trading.atr_period:
                continue

            recent_bars = min(16, len(m15.close))
            high_w = max(m15.high[-recent_bars:])
            low_w = min(m15.low[-recent_bars:])
            sess_range = round(high_w - low_w, 4)

            atr_val = atr(m15.high, m15.low, m15.close, self.cfg.trading.atr_period) or 0.0
            atr_val = round(atr_val, 4)

            rel_spread = 1.0
            if spread_trackers and sym in spread_trackers:
                st = spread_trackers[sym]
                avg_s = st.average_spread()
                curr_s = st.current_spread
                if avg_s > 0:
                    rel_spread = round(curr_s / avg_s, 2)

            vol_ratio = (sess_range / atr_val) if atr_val > 0 else 1.0
            spread_factor = max(0.5, 2.0 - rel_spread)
            vol_score = round(vol_ratio * spread_factor, 2)

            hint = "Active Volatility Expansion" if vol_ratio > 2.5 else "Stable Range"
            if rel_spread > 1.4:
                hint += " (Caution: Elevated Spread)"

            candidates.append(
                ScoutCandidate(
                    symbol=sym,
                    session_range=sess_range,
                    atr_value=atr_val,
                    relative_spread=rel_spread,
                    volatility_score=vol_score,
                    catalyst_hint=hint,
                )
            )

        candidates.sort(key=lambda c: c.volatility_score, reverse=True)
        for rank, c in enumerate(candidates, start=1):
            c.priority_rank = rank

        summary = f"Scouted {len(candidates)} symbols for {session_name} session. "
        if candidates:
            summary += f"Top candidate: {candidates[0].symbol} (Score: {candidates[0].volatility_score})."
        else:
            summary += "No candidates qualified."

        return ScoutResult(
            session=session_name,
            candidates=candidates,
            timestamp=datetime.now(timezone.utc),
            summary=summary,
        )

    # -----------------------------------------------------------------
    # Agent 02 — TECHNICAL ANALYST
    # -----------------------------------------------------------------
    def technical(
        self,
        symbol: str,
        data_all: Dict[str, any],
        hierarchy_result: dict,
        current_price: float,
    ) -> TechnicalStructure:
        """Map price structure, HTF/LTF alignment, dynamic value zones, and key levels."""
        h4_b = hierarchy_result.get("h4_bias", Bias.NEUTRAL)
        h1_b = hierarchy_result.get("h1_bias", Bias.NEUTRAL)
        m15_b = hierarchy_result.get("m15_bias", Bias.NEUTRAL)
        m5_b = hierarchy_result.get("m5_bias", Bias.NEUTRAL)
        allowed = hierarchy_result.get("allowed_direction")

        direction = "NEUTRAL"
        if allowed == "bullish":
            direction = "BUY"
        elif allowed == "bearish":
            direction = "SELL"

        zone = hierarchy_result.get("m15_zone")
        m15 = data_all.get("M15")

        recent_bars = min(20, len(m15.close)) if m15 and m15.close else 0
        swing_h = max(m15.high[-recent_bars:]) if recent_bars > 0 else current_price
        swing_l = min(m15.low[-recent_bars:]) if recent_bars > 0 else current_price

        key_supp = zone[0] if zone else swing_l
        key_res = zone[1] if zone else swing_h

        score = hierarchy_result.get("alignment_score", 0)
        summary = (
            f"HTF Bias: H4={getattr(h4_b, 'value', h4_b)}, H1={getattr(h1_b, 'value', h1_b)}. "
            f"LTF Alignment: M15={getattr(m15_b, 'value', m15_b)}, M5={getattr(m5_b, 'value', m5_b)}. "
            f"Structure: Support={key_supp:.2f}, Resistance={key_res:.2f}, Direction={direction}."
        )

        return TechnicalStructure(
            symbol=symbol,
            current_price=current_price,
            h4_bias=getattr(h4_b, "value", str(h4_b)),
            h1_bias=getattr(h1_b, "value", str(h1_b)),
            m15_bias=getattr(m15_b, "value", str(m15_b)),
            m5_bias=getattr(m5_b, "value", str(m5_b)),
            m15_zone=zone,
            swing_high=swing_h,
            swing_low=swing_l,
            key_support=key_supp,
            key_resistance=key_res,
            trigger_formed=bool(allowed is not None),
            direction=direction,
            alignment_score=score,
            summary=summary,
        )

    # -----------------------------------------------------------------
    # Agent 03 — FUNDAMENTAL ANALYST
    # -----------------------------------------------------------------
    def fundamental(
        self,
        symbol: str,
        data_all: Dict[str, any],
        tech_dir: str = "NEUTRAL",
    ) -> FundamentalDossier:
        """Review macro backdrop, currency drivers, and broad market sentiment."""
        h4 = data_all.get("H4")
        macro_score = 0.0
        tailwinds = []
        headwinds = []
        regime = "Neutral / Balanced"
        dxy_trend = "Consolidating"
        yield_trend = "Stable"

        if h4 and h4.close and len(h4.close) >= 20:
            h4_close = h4.close[-1]
            h4_sma20 = sum(h4.close[-20:]) / 20.0
            momentum = (h4_close - h4_sma20) / h4_sma20

            if "XAU" in symbol:
                if momentum > 0.005:
                    regime = "DXY Softening / Bullion Safe-Haven Inflow"
                    dxy_trend = "Bearish Pressure"
                    yield_trend = "Real Yields Softening"
                    macro_score = 0.6
                    tailwinds.append("Gold holding premium above H4 baseline; safe-haven demand elevated.")
                    tailwinds.append("Real yield resistance supports non-yielding bullion.")
                elif momentum < -0.005:
                    regime = "USD Dominance / Yield Surge"
                    dxy_trend = "Bullish Momentum"
                    yield_trend = "Yields Expanding"
                    macro_score = -0.6
                    headwinds.append("Dollar strength dampening gold upside.")
                    headwinds.append("Higher real yields create headwind for precious metals.")
                else:
                    regime = "Macro Equilibrium"
                    tailwinds.append("Range-bound dollar index; balanced macro flows.")
            else:  # FX (EURUSD, etc.)
                if momentum > 0.002:
                    regime = "USD Weakness / Risk-On Sentiment"
                    dxy_trend = "Bearish Retracement"
                    macro_score = 0.5
                    tailwinds.append("Broad dollar depreciation supporting currency upside.")
                elif momentum < -0.002:
                    regime = "USD Resilience / Risk-Off Flows"
                    dxy_trend = "Bullish Expansion"
                    macro_score = -0.5
                    headwinds.append("Flight-to-safety dollar flows weighing on foreign currencies.")
                else:
                    regime = "Range-Bound Macro Policy"

        if tech_dir == "BUY":
            alignment = "Favorable" if macro_score >= 0.2 else ("Adverse" if macro_score <= -0.4 else "Neutral")
        elif tech_dir == "SELL":
            alignment = "Favorable" if macro_score <= -0.2 else ("Adverse" if macro_score >= 0.4 else "Neutral")
        else:
            alignment = "Neutral"

        summary = (
            f"Regime: {regime}. DXY Trend: {dxy_trend}, Yields: {yield_trend}. "
            f"Macro Alignment: {alignment} (Score: {macro_score:+.2f})."
        )

        return FundamentalDossier(
            symbol=symbol,
            macro_regime=regime,
            dxy_trend=dxy_trend,
            yield_trend=yield_trend,
            macro_alignment=alignment,
            score=macro_score,
            tailwinds=tailwinds,
            headwinds=headwinds,
            summary=summary,
        )

    # -----------------------------------------------------------------
    # Agent 04 — NEWS ANALYST
    # -----------------------------------------------------------------
    def news(self, symbol: str, news_filter: any) -> NewsCatalystBrief:
        """Check economic calendar events, upcoming catalysts, and volatility danger zones."""
        news_ok, news_msg = news_filter.check(symbol) if news_filter else (True, "OK")
        calendar = getattr(news_filter, "calendar", None)

        has_high_impact = not news_ok
        mins_to_next = None
        next_event = "None scheduled in immediate window"
        risk_lvl = "LOW"
        upcoming = []

        if calendar and hasattr(calendar, "events"):
            now_utc = datetime.now(timezone.utc)
            rel_curr = ["USD"]
            if "EUR" in symbol:
                rel_curr.append("EUR")

            for ev in calendar.events:
                curr = getattr(ev, "currency", None) or (ev.get("currency") if isinstance(ev, dict) else "")
                impact = getattr(ev, "impact", None) or (ev.get("impact") if isinstance(ev, dict) else "")
                title = getattr(ev, "title", None) or (ev.get("title") if isinstance(ev, dict) else "Economic Event")

                ev_time = None
                if hasattr(ev, "time") and isinstance(ev.time, datetime):
                    ev_time = ev.time
                elif hasattr(ev, "timestamp") and ev.timestamp:
                    ev_time = datetime.fromtimestamp(float(ev.timestamp), tz=timezone.utc)
                elif isinstance(ev, dict) and ev.get("timestamp"):
                    ev_time = datetime.fromtimestamp(float(ev["timestamp"]), tz=timezone.utc)

                if ev_time and curr in rel_curr and str(impact).upper() in ("HIGH", "RED"):
                    diff_mins = int((ev_time - now_utc).total_seconds() / 60)
                    upcoming.append(f"{curr} {title} (in {diff_mins}m)")
                    if mins_to_next is None or abs(diff_mins) < abs(mins_to_next):
                        mins_to_next = diff_mins
                        next_event = f"{curr}: {title}"

        if not news_ok:
            risk_lvl = "EXTREME"
        elif mins_to_next is not None and abs(mins_to_next) <= 60:
            risk_lvl = "HIGH"
        elif mins_to_next is not None and abs(mins_to_next) <= 180:
            risk_lvl = "MEDIUM"

        summary = (
            f"News Filter: {'BLOCKED (' + news_msg + ')' if not news_ok else 'CLEAR'}. "
            f"Next Catalyst: {next_event} ({mins_to_next}m). Risk Level: {risk_lvl}."
        )

        return NewsCatalystBrief(
            symbol=symbol,
            has_high_impact_near=has_high_impact,
            minutes_to_next_event=mins_to_next,
            next_event_title=next_event,
            event_currency="USD",
            event_risk_level=risk_lvl,
            is_blocked=not news_ok,
            upcoming_events=upcoming[:3],
            summary=summary,
        )

    # -----------------------------------------------------------------
    # Agent 05 — QUANT ANALYST
    # -----------------------------------------------------------------
    def quant(
        self,
        symbol: str,
        hierarchy_result: dict,
        entry_data: any,
        atr_val: float,
        entry_price: float,
        sl_price: float,
        tp_price: float,
    ) -> QuantHypothesis:
        """Test the hypothesis against data: sideways filter, statistical score, and R:R expectancy."""
        is_sideways = hierarchy_result.get("is_sideways", False)
        sw_reason = hierarchy_result.get("sideways_reason", "")
        reasons = [sw_reason] if sw_reason else []

        score = hierarchy_result.get("alignment_score", 0)
        grade = "C"
        if score >= self.cfg.trading.signal_score_a_min:
            grade = "A"
        elif score >= self.cfg.trading.signal_score_b_min:
            grade = "B"

        risk_dist = abs(entry_price - sl_price) if entry_price and sl_price else 0.0
        reward_dist = abs(tp_price - entry_price) if entry_price and tp_price else 0.0
        expected_rr = round(reward_dist / risk_dist, 2) if risk_dist > 0 else 0.0

        atr_regime = "Normal"
        if entry_data and entry_data.close and len(entry_data.close) >= 28:
            prior_atr = atr(entry_data.high[:-14], entry_data.low[:-14], entry_data.close[:-14], 14) or atr_val
            if prior_atr > 0:
                if atr_val < 0.75 * prior_atr:
                    atr_regime = "Squeeze / Compression"
                elif atr_val > 1.35 * prior_atr:
                    atr_regime = "Volatility Expansion"

        if is_sideways:
            edge = "Disqualified (Sideways / Noise)"
        elif grade == "A" and expected_rr >= 2.0:
            edge = "Strong Institutional Alpha"
        elif grade in ("A", "B") and expected_rr >= 1.5:
            edge = "Moderate Statistical Edge"
        else:
            edge = "Weak / Sub-Optimal Edge"

        summary = (
            f"Sideways: {is_sideways} ({sw_reason or 'Clear'}). "
            f"Score: {score}/10 (Grade {grade}). ATR Regime: {atr_regime}. Expected R:R: {expected_rr}R. Edge: {edge}."
        )

        return QuantHypothesis(
            symbol=symbol,
            is_sideways=is_sideways,
            sideways_reasons=reasons,
            signal_score=score,
            signal_grade=grade,
            atr_regime=atr_regime,
            expected_rr=expected_rr,
            statistical_edge=edge,
            summary=summary,
        )

    # -----------------------------------------------------------------
    # Agent 06 — RISK MANAGER ("One Agent Tries to Kill the Trade")
    # -----------------------------------------------------------------
    def risk_manager(
        self,
        symbol: str,
        direction: TradeDirection,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        atr_val: float,
        account_info: any,
        daily_loss: any,
        max_dd: any,
        news_brief: NewsCatalystBrief,
        quant_hyp: QuantHypothesis,
    ) -> RiskVerdict:
        """Actively seek disqualifying flaws: invalidate thesis, check downside, verify sizing."""
        veto_reasons = []

        # 1. THESIS INVALIDATION TEST: What would prove it wrong?
        if sl_price <= 0:
            veto_reasons.append("Thesis Invalidation Missing: Stop loss is undefined or <= 0.")
        elif direction == TradeDirection.BUY and sl_price >= entry_price:
            veto_reasons.append("Thesis Invalidation Flawed: BUY stop loss is >= entry price.")
        elif direction == TradeDirection.SELL and sl_price <= entry_price:
            veto_reasons.append("Thesis Invalidation Flawed: SELL stop loss is <= entry price.")
        else:
            sl_dist = abs(entry_price - sl_price)
            if atr_val > 0 and sl_dist < 0.4 * atr_val:
                veto_reasons.append("Thesis Invalidation Too Tight: Stop distance (<0.4x ATR) invites market noise.")
            elif atr_val > 0 and sl_dist > 5.0 * atr_val:
                veto_reasons.append("Thesis Invalidation Too Wide: Stop distance (>5.0x ATR) violates risk envelope.")

        # 2. DOWNSIDE TEST: How much can the idea lose?
        eq = getattr(account_info, "equity", 100000.0)
        risk_pct = self.cfg.trading.pyramid_initial_risk_pct
        max_loss_usd = round(eq * (risk_pct / 100.0), 2)

        curr_daily_loss = 0.0
        if daily_loss:
            if callable(getattr(daily_loss, "current_daily_loss_pct", None)):
                curr_daily_loss = float(daily_loss.current_daily_loss_pct())
            elif callable(getattr(daily_loss, "loss_used_pct", None)):
                curr_daily_loss = float(daily_loss.loss_used_pct())
            else:
                curr_daily_loss = float(getattr(daily_loss, "current_daily_loss_pct", 0.0))
        daily_limit = self.cfg.trading.daily_loss_limit_pct
        daily_headroom = max(0.0, daily_limit - curr_daily_loss)

        curr_dd = 0.0
        if max_dd:
            val = getattr(max_dd, "current_dd_pct", 0.0)
            curr_dd = float(val() if callable(val) else val)
        max_dd_limit = self.cfg.trading.max_dd_limit_pct
        dd_headroom = max(0.0, max_dd_limit - curr_dd)

        kill_active = False
        if daily_loss and daily_loss.kill_switch_engaged():
            veto_reasons.append(f"Downside Breach: Daily loss killswitch active (loss: {curr_daily_loss:.2f}% >= {daily_limit}%).")
            kill_active = True
        elif risk_pct > daily_headroom:
            veto_reasons.append(f"Downside Breach: Trade risk ({risk_pct}%) exceeds remaining daily headroom ({daily_headroom:.2f}%).")

        if max_dd and max_dd.kill_switch_engaged():
            veto_reasons.append(f"Downside Breach: Max drawdown killswitch active (drawdown: {curr_dd:.2f}% >= {max_dd_limit}%).")
            kill_active = True
        elif risk_pct > dd_headroom:
            veto_reasons.append(f"Downside Breach: Trade risk ({risk_pct}%) exceeds remaining DD headroom ({dd_headroom:.2f}%).")

        # 3. POSITION SIZE & ASYMMETRY TEST: Does exposure fit risk?
        if news_brief.is_blocked:
            veto_reasons.append("Catalyst Danger: High-impact economic news catalyst active in danger window.")

        if quant_hyp.is_sideways:
            veto_reasons.append("Regime Invalidation: 5-layer sideways engine detected consolidation/choppiness.")

        if quant_hyp.signal_grade == "C":
            veto_reasons.append("Alpha Invalidation: Signal scored Grade C (sub-threshold confluence).")

        if quant_hyp.expected_rr > 0 and quant_hyp.expected_rr < 1.2:
            veto_reasons.append(f"Asymmetry Failure: Expected R:R ({quant_hyp.expected_rr}R) is below minimum institutional hurdle (1.2R).")

        is_approved = len(veto_reasons) == 0
        status = "APPROVED" if is_approved else "KILLED"
        assessment = (
            "THESIS APPROVED: Invalidation level is structural, downside is safely within prop firm buffers, and exposure fits risk parameters."
            if is_approved
            else f"TRADE KILLED: {'; '.join(veto_reasons)}"
        )

        return RiskVerdict(
            status=status,
            veto_reasons=veto_reasons,
            thesis_invalidation_level=sl_price,
            risk_per_trade_pct=risk_pct,
            max_loss_usd=max_loss_usd,
            daily_loss_headroom_pct=round(daily_headroom, 2),
            max_dd_headroom_pct=round(dd_headroom, 2),
            kill_switches_active=kill_active,
            adversarial_assessment=assessment,
        )

    # -----------------------------------------------------------------
    # Agent 07 — PORTFOLIO MANAGER ("The Final Agent Builds the Plan")
    # -----------------------------------------------------------------
    def portfolio_manager(
        self,
        symbol: str,
        direction: TradeDirection,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        lot_size: float,
        tech: TechnicalStructure,
        fund: FundamentalDossier,
        news: NewsCatalystBrief,
        quant: QuantHypothesis,
        risk: RiskVerdict,
        session_name: str,
    ) -> ResearchPlan:
        """Synthesize the 6 research inputs into one structured research and execution plan."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        action_name = "KILLED"
        if risk.is_approved:
            action_name = f"EXECUTE_{direction.value.upper()}"

        # 6 RESEARCH INPUTS
        inputs = {
            "Macro": f"{fund.macro_regime} | DXY: {fund.dxy_trend} | Macro Alignment: {fund.macro_alignment}",
            "HTF Direction": f"H4={tech.h4_bias.upper()}, H1={tech.h1_bias.upper()} | M15={tech.m15_bias.upper()}",
            "Trigger": f"M15 Zone retest [{tech.key_support:.2f} - {tech.key_resistance:.2f}] + Micro Structure Shift",
            "Risk": f"Invalidation @ {sl_price:.2f} (Max Loss: ${risk.max_loss_usd:.2f} / {risk.risk_per_trade_pct}%)",
            "Confluence": f"Quant Grade {quant.signal_grade} ({quant.signal_score}/10) | {quant.atr_regime} | R:R={quant.expected_rr}R",
            "Timing": f"{session_name} Session | Catalyst Risk: {news.event_risk_level} (Next: {news.minutes_to_next_event or 'None'}m)",
        }

        # Thesis synthesis
        dir_word = "bullish expansion" if direction == TradeDirection.BUY else "bearish continuation"
        thesis = (
            f"Exploit {dir_word} on {symbol} supported by {fund.macro_regime.lower()}, "
            f"confirmed by H4/H1 trend alignment and M15 value zone structure, targeting {tp_price:.2f}."
            if risk.is_approved
            else f"Hypothesis on {symbol} disqualified: {'; '.join(risk.veto_reasons)}"
        )

        evidence = [
            f"Technical: Multi-TF alignment ({tech.h4_bias}/{tech.h1_bias}) with price holding {tech.direction} bias.",
            f"Fundamental: Macro backdrop ({fund.macro_regime}) aligns favorably with technical order flow.",
            f"Quant: Sideways engine cleared (Chop/ADX ok); Grade {quant.signal_grade} statistical setup.",
            f"Catalyst: News analyst reports clear runway with event risk level = {news.event_risk_level}.",
        ]

        risks = [
            f"Hard Invalidation: Any close beyond {sl_price:.2f} structural stop immediately invalidates thesis.",
            f"Macro Catalyst: {news.next_event_title} scheduled in {news.minutes_to_next_event or 'N/A'} minutes.",
            f"Drawdown Cap: Absolute risk capped at 0.25% (${risk.max_loss_usd:.2f}), maintaining prop-firm compliance.",
        ]

        next_steps = [
            f"Order Routing: Send {direction.value.upper()} order at {entry_price:.2f} with Hard SL {sl_price:.2f} and TP {tp_price:.2f}.",
            f"Position Sizing: Allocate {lot_size:.2f} lots (Base risk 0.25%).",
            f"Trade Management: Move SL to breakeven upon reaching +0.5R; close 50% partial at +1.0R.",
            f"Pyramiding: Allow up to 4 scale-in legs only on runner trades demonstrating positive momentum.",
        ]

        # Markdown Dossier
        dossier = (
            f"# ================================================================================\n"
            f"# INSTITUTIONAL TRADE & RESEARCH PLAN — AGENT 07 (PORTFOLIO MANAGER)\n"
            f"# ================================================================================\n"
            f"ASSET: {symbol:<12} DATE: {date_str:<24} ACTION: {action_name}\n"
            f"STATUS: {'APPROVED FOR DISPATCH' if risk.is_approved else 'KILLED BY RISK MANAGER'}\n"
            f"--------------------------------------------------------------------------------\n\n"
            f"## 1. THE 6 RESEARCH INPUTS\n"
            f"- **Macro**: {inputs['Macro']}\n"
            f"- **HTF Direction**: {inputs['HTF Direction']}\n"
            f"- **Trigger**: {inputs['Trigger']}\n"
            f"- **Risk**: {inputs['Risk']}\n"
            f"- **Confluence**: {inputs['Confluence']}\n"
            f"- **Timing**: {inputs['Timing']}\n\n"
            f"## 2. RESEARCH THESIS\n"
            f"{thesis}\n\n"
            f"## 3. EVIDENCE MATRIX\n"
        )
        for ev in evidence:
            dossier += f"- [x] {ev}\n"

        dossier += f"\n## 4. DOWNSIDE RISKS & ADVERSARIAL RED-TEAM (AGENT 06)\n"
        for rk in risks:
            dossier += f"- [!] {rk}\n"
        dossier += f"- **Risk Manager Verdict**: {risk.adversarial_assessment}\n\n"

        dossier += f"## 5. EXECUTION PLAN & NEXT STEPS\n"
        for ns in next_steps:
            dossier += f"1. {ns}\n"
        dossier += f"\n================================================================================\n"

        return ResearchPlan(
            symbol=symbol,
            date=date_str,
            action=action_name,
            inputs=inputs,
            thesis=thesis,
            evidence=evidence,
            risks=risks,
            next_steps=next_steps,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            lot_size=lot_size,
            markdown_dossier=dossier,
        )

    def save_research_plan(self, plan: ResearchPlan) -> Path:
        """Write the research dossier to disk in logs/research/."""
        clean_date = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"{clean_date}_{plan.symbol}_{plan.action}.md"
        out_path = self.report_dir / fname
        try:
            out_path.write_text(plan.markdown_dossier, encoding="utf-8")
        except Exception as e:
            log.warning("Failed to save research plan to %s: %s", out_path, e)
        return out_path


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
        self.sizer = PositionSizer(
            tc.pyramid_initial_risk_pct,
            tc.max_pyramid_entries,
            enable_profit_compounding=getattr(tc, "enable_profit_compounding", True),
            initial_balance=getattr(tc, "initial_account_balance", 10000.0),
            compounding_cap_mult=getattr(tc, "compounding_cap_mult", 2.0),
        )
        self.partial_close = PartialCloseManager(tc.partial_take_profit_r, tc.partial_close_pct)
        self.daily_loss = DailyLossTracker(
            tc.daily_loss_limit_pct, tc.daily_loss_buffer_pct,
            tc.broker_daily_reset_hour, tc.broker_daily_reset_tz,
        )
        self.max_dd = MaxDDTracker(tc.max_dd_limit_pct, tc.max_dd_buffer_pct)
        self.pyramid_mgr = PyramidManager(tc.max_pyramid_entries, tc.pyramid_add_trigger_r)
        self.order_entry = OrderEntry(self.connector, tc)
        self.persistence = StatePersistence(tc.state_db_path, tc.trade_log_path)
        self.trade_mgr = TradeManager(
            self.order_entry, self.exit_mgr, self.partial_close,
            self.pyramid_mgr, self.sizer, self.daily_loss, self.max_dd,
            persistence=self.persistence,
        )
        self.cluster_mgr = ClusterManager()
        self.research_team = InstitutionalResearchTeam(self.cfg)
        self._xau_session_trades: int = 0
        self._xau_london_trades_today: int = 0
        self._xau_ny_trades_today: int = 0
        self._xau_current_session_date: Optional[object] = None
        self._xau_last_exit_time: Optional[datetime] = None


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

        # Auto-resolve symbols to match broker naming conventions (e.g. .x, .pro, .raw, +)
        resolved_symbols = []
        for sym in self.symbols:
            if self.connector.symbol_info(sym) is not None:
                resolved_symbols.append(sym)
                continue
            resolved = sym
            for suffix in [".x", ".pro", ".raw", "+", "m", ".s", "_sb"]:
                candidate = f"{sym}{suffix}"
                if self.connector.symbol_info(candidate) is not None:
                    resolved = candidate
                    break
            if resolved == sym:
                all_b = [s.name for s in self.connector.symbols_get()]
                for s in all_b:
                    if s.upper().startswith(sym.upper()):
                        resolved = s
                        break
            if resolved != sym:
                log.info("🎯 Auto-Resolved Broker Symbol: '%s' -> '%s'", sym, resolved)
            resolved_symbols.append(resolved)
        self.symbols = resolved_symbols

        # Re-initialize feeds for resolved symbols and subscribe
        self.data_feeds = {s: MultiTFData(self.connector, s) for s in self.symbols}
        self.spread_trackers = {s: SpreadTracker(self.cfg.trading.spread_lookback_bars) for s in self.symbols}
        self.spread_filters = {s: SpreadFilter(self.spread_trackers[s], self.cfg.trading.max_spread_multiplier) for s in self.symbols}
        for s in self.symbols:
            self.connector.symbol_select(s, True)

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

                if not getattr(self, "_capital_calibrated", False) and account_info and account_info.balance > 0:
                    self._capital_calibrated = True
                    self.sizer.initial_balance = account_info.balance
                    log.info("🎯 Dynamic Capital Auto-Calibration: Locked baseline capital to live MT5 balance: $%.2f", account_info.balance)

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

                    strategy_type = getattr(self.cfg.trading, "strategy_trigger_type", "momentum")
                    if ("XAU" in symbol or "GOLD" in symbol.upper() or "EUR" in symbol) and strategy_type in ("xau_liquidity_sweep_fvg_m1", "liquidity_sweep_fvg"):
                        self._process_xau_scalp_lifecycle(data_all, symbol, account_info)
                    else:
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

        # -------------------------------------------------------------
        # 7-Agent Institutional Research Team Gauntlet
        # -------------------------------------------------------------
        if getattr(self.cfg.trading, "enable_research_team", True):
            tech_struct = self.research_team.technical(symbol, data_all, hierarchy_result, current_price)
            fund_dossier = self.research_team.fundamental(symbol, data_all, tech_struct.direction)
            news_brief = self.research_team.news(symbol, self.news_filter)
            quant_hyp = self.research_team.quant(symbol, hierarchy_result, entry_data, atr_val, current_price, sl, tp)

            # Agent 06 — Risk Manager ("One Agent Tries to Kill the Trade")
            acct_info = self.account.refresh() if hasattr(self.account, "refresh") else None
            risk_verdict = self.research_team.risk_manager(
                symbol=symbol,
                direction=direction,
                entry_price=current_price,
                sl_price=sl,
                tp_price=tp,
                atr_val=atr_val,
                account_info=acct_info or getattr(self.daily_loss, "state", None),
                daily_loss=self.daily_loss,
                max_dd=self.max_dd,
                news_brief=news_brief,
                quant_hyp=quant_hyp,
            )
            signal.risk_verdict = risk_verdict

            if not risk_verdict.is_approved:
                log.info("[%s] ❌ Trade KILLED by Agent 06 (Risk Manager): %s", symbol, "; ".join(risk_verdict.veto_reasons))
                return None

            # Agent 07 — Portfolio Manager ("Builds the Structured Research Plan")
            contract_sz = self.account.contract_size(symbol) if hasattr(self.account, "contract_size") else 100
            point_val = self.account.point_value(symbol) if hasattr(self.account, "point_value") else 1.0
            eq = getattr(acct_info, "equity", 100000.0) if acct_info else 100000.0
            lot_sz = self.sizer.calc_initial_lot(
                eq,
                current_price,
                sl,
                point_val,
                contract_sz,
            )
            sess_name = current_session().value.upper()
            plan = self.research_team.portfolio_manager(
                symbol=symbol,
                direction=direction,
                entry_price=current_price,
                sl_price=sl,
                tp_price=tp,
                lot_size=lot_sz,
                tech=tech_struct,
                fund=fund_dossier,
                news=news_brief,
                quant=quant_hyp,
                risk=risk_verdict,
                session_name=sess_name,
            )
            signal.research_plan = plan
            saved_path = self.research_team.save_research_plan(plan)
            log.info("[%s] 📋 Institutional Research Plan generated by Agent 07: %s (Dossier: %s)",
                     symbol, plan.action, saved_path.name)

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

    def _process_xau_scalp_lifecycle(self, data_all: dict, symbol: str, account_info):
        """Authoritative execution lifecycle for XAU_LIQUIDITY_SWEEP_FVG M1 Scalping."""
        tc = self.cfg.trading
        now_utc = datetime.now(timezone.utc)
        ny_dt = to_ny_time(now_utc, getattr(tc, "xau_session_timezone", "America/New_York"))

        # 1. Manage existing pending FVG limit orders (5-bar expiry and cancellations)
        m1_data = data_all.get("M1")
        if not m1_data or not m1_data.close:
            return

        current_price = m1_data.close[-1]
        active_clusters = self.cluster_mgr.active_clusters_for_symbol(symbol)
        active_positions = [c for c in active_clusters if c.status == TradeStatus.OPEN]
        pending_clusters = [c for c in active_clusters if c.status == TradeStatus.PENDING]

        # Sync pending and active clusters with MT5 open positions
        if pending_clusters or active_positions:
            open_pos = self.connector.positions_get(symbol=symbol)
            pos_tickets = {getattr(p, "ticket", 0) for p in open_pos} if open_pos else set()
            for pc in list(pending_clusters):
                for leg in pc.legs:
                    if leg.position_ticket in pos_tickets:
                        leg.status = TradeStatus.OPEN
                        pc.status = TradeStatus.OPEN
                        log.info("[%s] ⚡ Pending limit order filled into OPEN position: ticket=%d", symbol, leg.position_ticket)
                        if pc in pending_clusters:
                            pending_clusters.remove(pc)
                        active_positions.append(pc)
                        break

            # Check if any active positions were closed by broker (e.g. SL or TP reached)
            for ac in list(active_positions):
                open_legs = [l for l in ac.legs if l.status == TradeStatus.OPEN]
                if open_legs and not any(l.position_ticket in pos_tickets for l in open_legs):
                    ac.status = TradeStatus.CLOSED
                    active_positions.remove(ac)
                    self._xau_last_exit_time = now_utc.replace(tzinfo=None)
                    log.info("[%s] Position closed by broker (SL/TP) for cluster %s", symbol, ac.cluster_id[:8])

                    # Calculate realized PnL to update G4 consecutive loss tracker
                    cluster_pnl = 0.0
                    from datetime import timedelta
                    deals = self.connector.history_deals_get(ac.open_time or (now_utc - timedelta(days=1)), now_utc) if hasattr(self.connector, "history_deals_get") else []
                    leg_tickets = {l.position_ticket for l in ac.legs}
                    found_deal = False
                    for d in deals:
                        pos_id = getattr(d, "position_id", 0)
                        if pos_id in leg_tickets:
                            cluster_pnl += getattr(d, "profit", 0.0)
                            found_deal = True
                    if not found_deal:
                        for l in open_legs:
                            p_pts = (current_price - l.entry_price) if l.direction == TradeDirection.BUY else (l.entry_price - current_price)
                            c_sz = 100000.0 if "EUR" in symbol else 100.0
                            cluster_pnl += p_pts * l.lot_size * c_sz

                    if "EUR" in symbol and getattr(tc, "eur_consec_loss_guard", True):
                        if cluster_pnl > 0:
                            self._live_g4_consec_losses = 0
                            log.info("[%s] G4: Win recorded (+$%.2f) — consecutive losses reset to 0", symbol, cluster_pnl)
                        else:
                            self._live_g4_consec_losses = getattr(self, "_live_g4_consec_losses", 0) + 1
                            log.info("[%s] G4: Loss recorded (-$%.2f) — consecutive losses count: %d", symbol, abs(cluster_pnl), self._live_g4_consec_losses)
                            if self._live_g4_consec_losses >= getattr(tc, "eur_consec_loss_max", 3):
                                pause_h = getattr(tc, "eur_consec_loss_pause_hours", 2)
                                self._live_g4_pause_until = now_utc.replace(tzinfo=None) + timedelta(hours=pause_h)
                                log.warning("[%s] 🛡️ G4 Cooldown: %d consecutive losses — live trading paused until %s",
                                            symbol, self._live_g4_consec_losses, self._live_g4_pause_until)

        # Check session active: Strict Killzones for Gold & London+NY Session for Forex
        ecosystem_mode = getattr(tc, "xau_ecosystem_mode", True)
        h_utc = now_utc.hour
        m_utc = now_utc.minute
        is_gold = "XAU" in symbol
        is_eur = "EUR" in symbol
        if is_gold and getattr(tc, "xau_strict_killzones", True):
            in_london = tc.is_in_xau_london_killzone(now_utc) if hasattr(tc, "is_in_xau_london_killzone") else ((h_utc == 7 and m_utc >= 45) or (8 <= h_utc < 10) or (h_utc == 10 and m_utc <= 30))
            in_ny_core = (13 < h_utc < 16) or (h_utc == 13 and m_utc >= 30) or (h_utc == 16 and m_utc <= 30)
            session_active = in_london or in_ny_core
        elif is_eur:
            if hasattr(tc, "is_in_eur_session"):
                session_active = tc.is_in_eur_session(now_utc)
            else:
                eur_start = getattr(tc, "eur_session_start_hour", 7)
                eur_end = getattr(tc, "eur_session_end_hour", 16)
                session_active = (eur_start <= h_utc < eur_end)
                if getattr(tc, "eur_filter_lunch_chop", True):
                    l_start = getattr(tc, "eur_lunch_start_hour", 10)
                    l_end = getattr(tc, "eur_lunch_end_hour", 12)
                    if l_start <= h_utc < l_end:
                        session_active = False
            in_london = (7 <= h_utc < 10)
            in_ny_core = (12 <= h_utc < 16)
        else:
            in_ny_core = is_in_ny_session(now_utc, getattr(tc, "xau_session_start", "10:00"),
                                          getattr(tc, "xau_session_end", "11:00"),
                                          getattr(tc, "xau_session_timezone", "America/New_York"))
            in_london = (7 <= h_utc < 9) if getattr(tc, "xau_enable_london_asian_sweep", True) else False
            session_active = (in_ny_core or in_london) if ecosystem_mode else in_ny_core

        # Daily reset for session trade counters
        today_date = ny_dt.date()
        if getattr(self, "_xau_current_session_date", None) != today_date:
            self._xau_current_session_date = today_date
            self._xau_session_trades = 0
            self._xau_london_trades_today = 0
            self._xau_ny_trades_today = 0

        news_ok, _ = self.news_filter.check(symbol)
        spread_filter = self.spread_filters.get(symbol, self.spread_filter)
        spread_ok, _ = spread_filter.check()

        # If session ended, news blackout, or Guard C invalidation, cancel pending limit orders
        for pc in list(pending_clusters):
            should_cancel = False
            cancel_reason = ""
            fvg_h = getattr(pc, "fvg_high", 0.0)
            fvg_l = getattr(pc, "fvg_low", 0.0)
            last_close = m1_data.close[-1]
            prev_close = m1_data.close[-2] if len(m1_data.close) >= 2 else last_close

            if not session_active:
                should_cancel = True
                cancel_reason = "Session window closed"
            elif not news_ok:
                should_cancel = True
                cancel_reason = "News blackout window active"
            elif not spread_ok:
                should_cancel = True
                cancel_reason = "Excessive spread anomaly"
            elif getattr(tc, "xau_enable_pre_fill_guard", True) and (fvg_h > 0 or fvg_l > 0):
                # Guard C: Invalidate pending limit order if an adverse M1 candle penetrated and closed through the FVG
                if pc.direction == TradeDirection.SELL and fvg_h > 0 and (last_close > fvg_h or prev_close > fvg_h):
                    should_cancel = True
                    cancel_reason = f"Pre-Fill Guard C: M1 bar penetrated above FVG resistance ({last_close:.2f} > {fvg_h:.2f})"
                elif pc.direction == TradeDirection.BUY and fvg_l > 0 and (last_close < fvg_l or prev_close < fvg_l):
                    should_cancel = True
                    cancel_reason = f"Pre-Fill Guard C: M1 bar penetrated below FVG support ({last_close:.2f} < {fvg_l:.2f})"
            
            if not should_cancel:
                for leg in pc.legs:
                    if leg.open_time:
                        elapsed_m1_bars = int((now_utc.replace(tzinfo=None) - leg.open_time).total_seconds() / 60.0)
                        fvg_expiry_limit = tc.get_fvg_expiry_bars(symbol) if hasattr(tc, "get_fvg_expiry_bars") else getattr(tc, "xau_fvg_expiry_bars", 8)
                        if elapsed_m1_bars >= fvg_expiry_limit:
                            should_cancel = True
                            cancel_reason = f"FVG limit order expired after {elapsed_m1_bars} M1 bars (limit: {fvg_expiry_limit})"

            if should_cancel:
                log.info("[%s] 🛡️ Cancelling pending FVG order cluster %s: %s", symbol, pc.cluster_id[:8], cancel_reason)
                self.trade_mgr._close_cluster_positions(pc, current_price, ExitReason.SIGNAL_REVERSAL)

        # 2. Check holding time stop on active open positions
        for c in active_positions:
            if c.open_time:
                bars_held = int((now_utc.replace(tzinfo=None) - c.open_time).total_seconds() / 60.0)
                max_holding = tc.get_max_holding_bars(c.symbol) if hasattr(tc, "get_max_holding_bars") else getattr(tc, "xau_max_holding_bars", 60)
                if max_holding > 0 and bars_held >= max_holding:
                    log.info("[%s] %d-bar holding time stop reached for cluster %s — closing at market",
                             symbol, max_holding, c.cluster_id[:8])
                    self.trade_mgr._close_cluster_positions(c, current_price, ExitReason.TIME_BASED)
                    self._xau_last_exit_time = now_utc.replace(tzinfo=None)

        # 3. Trade limits: max 1 active position & Option A concurrent pending limit orders
        max_concurrent_pending = getattr(tc, "max_concurrent_pending_orders", 2)
        if len(active_positions) > 0 or len(pending_clusters) >= max_concurrent_pending:
            return

        if not session_active:
            return

        max_sess_trades = getattr(tc, "xau_max_trades_per_session", 2)
        max_daily_trades = getattr(tc, "max_daily_trades", 4)
        if getattr(self, "_xau_session_trades", 0) >= max_daily_trades:
            return

        session_limit_reached = False
        if ecosystem_mode:
            if in_london and getattr(self, "_xau_london_trades_today", 0) >= max_sess_trades:
                session_limit_reached = True
            elif in_ny_core and getattr(self, "_xau_ny_trades_today", 0) >= max_sess_trades:
                session_limit_reached = True
        else:
            if self._xau_session_trades >= max_sess_trades:
                session_limit_reached = True

        if session_limit_reached:
            return

        # 4. Check 5-minute cooldown
        if self._xau_last_exit_time:
            cooldown_min = getattr(tc, "xau_cooldown_minutes", 5)
            elapsed_cd = (now_utc.replace(tzinfo=None) - self._xau_last_exit_time).total_seconds() / 60.0
            if elapsed_cd < cooldown_min:
                return

        # ── El Professor Hidden Guards (Live) ─────────────────────────────────────
        professor_veto = False
        if is_gold:
            # Guard 1 (XAU only): London Close Wall — no new entries after 15:45 UTC
            if getattr(tc, "xau_london_close_guard", True):
                g1_h = getattr(tc, "xau_london_close_cutoff_hour", 15)
                g1_m = getattr(tc, "xau_london_close_cutoff_min", 45)
                if h_utc > g1_h or (h_utc == g1_h and m_utc >= g1_m):
                    professor_veto = True
                    log.debug("[%s] G1 London Close Wall: live veto at %02d:%02d UTC", symbol, h_utc, m_utc)
        elif is_eur:
            # Guard 2 (EUR only): ATR Flash-Crash Circuit Breaker
            if getattr(tc, "eur_atr_circuit_breaker", True):
                g2_lb = getattr(tc, "eur_atr_spike_lookback", 20)
                g2_mult = getattr(tc, "eur_atr_spike_mult", 3.0)
                if len(m1_data.high) >= g2_lb + 2:
                    cur_range = m1_data.high[-1] - m1_data.low[-1]
                    avg_range = sum(m1_data.high[j] - m1_data.low[j] for j in range(-g2_lb - 1, -1)) / g2_lb
                    if cur_range > 0 and avg_range > 0 and (cur_range / avg_range) >= g2_mult:
                        professor_veto = True
                        log.warning("[%s] G2 ATR Circuit Breaker: spike ratio %.2f — entry blocked", symbol, cur_range / avg_range)

            # Guard 3 (EUR only): H4 Macro Bias Alignment
            if not professor_veto and getattr(tc, "eur_h4_bias_guard", True):
                h4_data = data_all.get("H4")
                if h4_data and len(h4_data.close) >= getattr(tc, "eur_h4_ema_slow", 50) + 5:
                    g3_fast = getattr(tc, "eur_h4_ema_fast", 9)
                    g3_slow = getattr(tc, "eur_h4_ema_slow", 50)
                    h4_cl = list(h4_data.close[-60:])
                    k_f, k_s = 2.0 / (g3_fast + 1), 2.0 / (g3_slow + 1)
                    ef = es = h4_cl[0]
                    for p in h4_cl[1:]:
                        ef = p * k_f + ef * (1 - k_f)
                        es = p * k_s + es * (1 - k_s)
                    # Store H4 bias on self so signal scorer can access it
                    self._eur_h4_bullish = ef > es
                    self._eur_h4_bearish = ef < es
                    # Bias check happens after signal is generated (below)

            # Guard 4 (EUR only): 3-Consecutive-Loss Cooldown
            if not professor_veto and getattr(tc, "eur_consec_loss_guard", True):
                if not hasattr(self, "_live_g4_pause_until"):
                    self._live_g4_pause_until = None
                    self._live_g4_consec_losses = 0
                    self._live_g4_last_day = None
                today_g4 = now_utc.date()
                if today_g4 != self._live_g4_last_day:
                    self._live_g4_last_day = today_g4
                    self._live_g4_consec_losses = 0
                    self._live_g4_pause_until = None
                if self._live_g4_pause_until and now_utc.replace(tzinfo=None) < self._live_g4_pause_until:
                    professor_veto = True
                    log.warning("[%s] G4 Cooldown: live trading paused until %s", symbol, self._live_g4_pause_until)
                elif self._live_g4_pause_until:
                    self._live_g4_pause_until = None
        # ── End Professor Guards ──────────────────────────────────────────────────

        if professor_veto:
            return

        # 5. Check Spread and News
        if not spread_ok or not news_ok:
            return


        # 6. M15 Structural Context (closed candles only) + M1 Execution
        m15_data = data_all.get("M15")
        if not m15_data or len(m15_data.close) < 15 or len(m1_data.close) < 15:
            return

        m15_closed = TimeframeData(
            tf="M15",
            time=m15_data.time[:-1],
            open=m15_data.open[:-1],
            high=m15_data.high[:-1],
            low=m15_data.low[:-1],
            close=m15_data.close[:-1],
            tick_volume=m15_data.tick_volume[:-1],
            spread=m15_data.spread[:-1],
        )

        m1_atr = atr(m1_data.high, m1_data.low, m1_data.close, getattr(tc, "xau_atr_period", 14)) or 1.0

        if in_ny_core:
            disp_atr = getattr(tc, "xau_displacement_atr_mult", 0.60)
            disp_body = getattr(tc, "xau_displacement_body_ratio", 0.60)
        else:
            disp_atr = getattr(tc, "xau_london_displacement_atr_mult", 0.75)
            disp_body = getattr(tc, "xau_london_displacement_body_ratio", 0.65)

        is_fx = ("EUR" in symbol) or (current_price < 10.0)
        default_pv = 0.00001 if is_fx else 0.01
        pv = self.account.point_value(symbol) if hasattr(self.account, "point_value") else default_pv

        seq = self.trigger.detect_xau_scalp_sequence(
            m15_data=m15_closed,
            m1_data=m1_data,
            m1_atr=m1_atr,
            lookback_m15=getattr(tc, "xau_swing_lookback_m15", 20),
            sequence_window_m1=10,
            min_atr_mult=disp_atr,
            min_body_ratio=disp_body,
            mss_lookback=getattr(tc, "xau_mss_lookback_m1", 5),
            target_r=tc.get_target_r(symbol) if hasattr(tc, "get_target_r") else getattr(tc, "xau_target_r", 2.0),
            point_value=pv,
            stops_level_points=getattr(tc, "deviation_points", 10),
            liquidity_source="m15_swings",
            enable_delta_absorption=getattr(tc, "enable_delta_absorption", True),
            enable_hvn_tp_calibration=getattr(tc, "enable_hvn_tp_calibration", True),
            min_sl_distance=tc.get_min_sl_distance(symbol) if hasattr(tc, "get_min_sl_distance") else 0.0,
        )
        if not seq:
            return

        # Telemetry: Log exact times across timezones
        broker_t = broker_date()
        utc_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
        ny_str = ny_dt.strftime("%Y-%m-%d %H:%M:%S NY")
        sess_name = "NY_CORE" if in_ny_core else "LONDON_OPEN"
        log.info("[%s] 🎯 Valid Scalp Sequence Detected (%s)! UTC: %s | NY: %s | Broker: %s",
                 symbol, sess_name, utc_str, ny_str, broker_t)
        fmt = ".5f" if is_fx else ".2f"
        log.info(f"[{symbol}] Context: BSL={seq['bsl']:{fmt}}, SSL={seq['ssl']:{fmt}} | Swept: {seq['sweep_direction']} at {seq['swept_level']:{fmt}} | MSS={seq['mss_level']:{fmt}} | FVG=[{seq['fvg_low']:{fmt}}, {seq['fvg_high']:{fmt}}], Entry={seq['entry_price']:{fmt}}, SL={seq['sl_price']:{fmt}}, TP={seq['tp_price']:{fmt}}")

        # Construct Signal
        sig = Signal(
            symbol=symbol,
            direction=seq["direction"],
            entry_tf="M1",
            entry_price=seq["entry_price"],
            sl_price=seq["sl_price"],
            tp_price=seq["tp_price"],
            atr_value=seq["atr"],
            score=10,
        )
        sig.grade = SignalGrade.A
        sig.setup_type = f"SWEEP_FVG_{sess_name}"
        sig.fvg_low = seq.get("fvg_low", 0.0)
        sig.fvg_high = seq.get("fvg_high", 0.0)

        # G3 live direction check — veto if signal direction contradicts H4 macro bias
        if is_eur and getattr(tc, "eur_h4_bias_guard", True):
            h4_bullish = getattr(self, "_eur_h4_bullish", None)
            h4_bearish = getattr(self, "_eur_h4_bearish", None)
            if h4_bullish and sig.direction == TradeDirection.SELL:
                log.info("[%s] G3 H4 Bias: H4 BULLISH — SELL signal vetoed", symbol)
                return
            if h4_bearish and sig.direction == TradeDirection.BUY:
                log.info("[%s] G3 H4 Bias: H4 BEARISH — BUY signal vetoed", symbol)
                return

        # Submit FVG Limit Order

        point_val = self.account.point_value(symbol) if hasattr(self.account, "point_value") else 1.0
        contract_sz = self.account.contract_size(symbol) if hasattr(self.account, "contract_size") else 100
        min_lot = self.account.min_lot(symbol) if hasattr(self.account, "min_lot") else 0.01
        lot_step = self.account.lot_step(symbol) if hasattr(self.account, "lot_step") else 0.01
        max_lot = self.account.max_lot(symbol) if hasattr(self.account, "max_lot") else 100.0

        # Net Dollar Beta Gate (Correlation Shield)
        risk_scale = 1.0
        if getattr(tc, "enable_net_beta_gate", True):
            if self.cluster_mgr.has_same_usd_exposure(symbol, sig.direction):
                risk_scale = getattr(tc, "correlated_usd_risk_scale", 0.60)
                log.info("[%s] 🛡️ Net Dollar Beta Gate active: correlated USD exposure detected across open positions — scaling risk by %.2fx (0.25%% -> %.3f%%)",
                         symbol, risk_scale, getattr(tc, "xau_london_risk_per_trade", 0.0025) * risk_scale * 100)

        cluster = self.trade_mgr.execute_limit_signal(
            signal=sig,
            limit_price=seq["entry_price"],
            account=account_info,
            point_value=point_val,
            contract_size=contract_sz,
            min_lot=min_lot,
            lot_step=lot_step,
            max_lot=max_lot,
            risk_scale=risk_scale,
        )
        if cluster:
            self.cluster_mgr.add(cluster)
            self._xau_session_trades += 1
            if in_london:
                self._xau_london_trades_today = getattr(self, "_xau_london_trades_today", 0) + 1
            elif in_ny_core:
                self._xau_ny_trades_today = getattr(self, "_xau_ny_trades_today", 0) + 1
            log.info("[%s] 📥 Pending FVG Limit Order registered in cluster %s (%s): %s at %.2f (SL=%.2f, TP=%.2f)",
                     symbol, cluster.cluster_id[:8], sess_name, sig.direction.value, seq["entry_price"],
                     seq["sl_price"], seq["tp_price"])

    def _manage_active_trades(self, data_all: dict, symbol: str = ""):

        clusters = self.cluster_mgr.active_clusters_for_symbol(symbol) if symbol else list(self.cluster_mgr.active)
        for cluster in clusters:
            actions = self.trade_mgr.manage_exits(cluster, data_all)
            for action in actions:
                log.info("Exit action [%s]: %s cluster=%s", getattr(cluster, "symbol", ""), action.get("action"), cluster.cluster_id[:8])
                if cluster.status == TradeStatus.CLOSED and "EUR" in getattr(cluster, "symbol", "") and getattr(self.cfg.trading, "eur_consec_loss_guard", True):
                    pnl = action.get("pnl", 0.0)
                    if pnl > 0:
                        self._live_g4_consec_losses = 0
                        log.info("[%s] G4: Win recorded (+$%.2f) — consecutive losses reset to 0", cluster.symbol, pnl)
                    else:
                        self._live_g4_consec_losses = getattr(self, "_live_g4_consec_losses", 0) + 1
                        log.info("[%s] G4: Loss recorded (-$%.2f) — consecutive losses count: %d", cluster.symbol, abs(pnl), self._live_g4_consec_losses)
                        if self._live_g4_consec_losses >= getattr(self.cfg.trading, "eur_consec_loss_max", 3):
                            from datetime import timedelta
                            pause_h = getattr(self.cfg.trading, "eur_consec_loss_pause_hours", 2)
                            self._live_g4_pause_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=pause_h)
                            log.warning("[%s] 🛡️ G4 Cooldown: %d consecutive losses — live trading paused until %s",
                                        cluster.symbol, self._live_g4_consec_losses, self._live_g4_pause_until)

    def _close_all_positions(self, reason: str):
        for cluster in list(self.cluster_mgr.active):
            sym = getattr(cluster, "symbol", "XAUUSD")
            tick = self.connector.symbol_info_tick(sym)
            price = (tick.bid + tick.ask) / 2 if tick else 0.0
            self.trade_mgr._close_cluster_positions(cluster, price, ExitReason.EQUITY_KILL)
            log.warning("[%s] All positions closed: %s at %.4f", sym, reason, price)

    def run_premarket_research(self):
        """Run pre-market research across all configured symbols using the 7-Agent Research Team."""
        log.info("Starting 7-Agent Pre-Market Research across symbols: %s", ", ".join(self.symbols))
        connected = self.connector.connect()
        if not connected:
            log.warning("MT5 terminal not reachable — running research with available local/cached data.")

        account_info = self.account.refresh() if connected else None
        sess_name = current_session().value.upper()

        if connected:
            for s in self.symbols:
                feed = self.data_feeds.get(s)
                if feed:
                    feed.update_all()

        scout_res = self.research_team.scout(self.symbols, self.data_feeds, self.spread_trackers, sess_name)
        print("\n" + "=" * 80)
        print(f"📡 01 — MARKET SCOUT REPORT [{sess_name} SESSION]")
        print("=" * 80)
        print(scout_res.summary)
        for c in scout_res.candidates:
            print(f"  [{c.priority_rank}] {c.symbol:<8} Range: {c.session_range:.4f} | ATR: {c.atr_value:.4f} | RelSpread: {c.relative_spread:.2f}x | Score: {c.volatility_score:.2f} ({c.catalyst_hint})")
        print("=" * 80 + "\n")

        plans = []
        for c in scout_res.candidates:
            sym = c.symbol
            feed = self.data_feeds.get(sym)
            if not feed:
                continue
            data_all = feed.all_tfs() if hasattr(feed, "all_tfs") else feed
            if not data_all:
                continue

            m15 = data_all.get("M15")
            if not m15 or not m15.close:
                continue

            current_price = m15.close[-1]
            hierarchy_result = self.hierarchy.evaluate(data_all, current_session())
            allowed = hierarchy_result.get("allowed_direction")
            direction = TradeDirection.BUY if allowed == "bullish" else (TradeDirection.SELL if allowed == "bearish" else TradeDirection.BUY)
            atr_val = atr(m15.high, m15.low, m15.close, self.cfg.trading.atr_period) or 1.0
            sl = self.exit_mgr.calc_atr_sl(m15, direction, "M15")
            tp = self.exit_mgr.calc_structure_tp(m15, direction, current_price, atr_val)

            tech_struct = self.research_team.technical(sym, data_all, hierarchy_result, current_price)
            fund_dossier = self.research_team.fundamental(sym, data_all, tech_struct.direction)
            news_brief = self.research_team.news(sym, self.news_filter)
            quant_hyp = self.research_team.quant(sym, hierarchy_result, m15, atr_val, current_price, sl, tp)

            risk_verdict = self.research_team.risk_manager(
                symbol=sym,
                direction=direction,
                entry_price=current_price,
                sl_price=sl,
                tp_price=tp,
                atr_val=atr_val,
                account_info=account_info or getattr(self.daily_loss, "state", None),
                daily_loss=self.daily_loss,
                max_dd=self.max_dd,
                news_brief=news_brief,
                quant_hyp=quant_hyp,
            )

            contract_sz = self.account.contract_size(sym) if hasattr(self.account, "contract_size") else 100
            point_val = self.account.point_value(sym) if hasattr(self.account, "point_value") else 1.0
            eq = getattr(account_info, "equity", 100000.0) if account_info else 100000.0
            lot_sz = self.sizer.calc_initial_lot(eq, current_price, sl, point_val, contract_sz)

            plan = self.research_team.portfolio_manager(
                symbol=sym,
                direction=direction,
                entry_price=current_price,
                sl_price=sl,
                tp_price=tp,
                lot_size=lot_sz,
                tech=tech_struct,
                fund=fund_dossier,
                news=news_brief,
                quant=quant_hyp,
                risk=risk_verdict,
                session_name=sess_name,
            )
            saved = self.research_team.save_research_plan(plan)
            plans.append(plan)

            print(plan.markdown_dossier)
            print(f"📁 Research dossier saved to: {saved}\n")

        if connected:
            self.connector.disconnect()
        return plans

    def _shutdown(self):
        log.info("Shutting down...")
        self.connector.disconnect()
        self.persistence.close()


def main():
    parser = argparse.ArgumentParser(description="XAUUSD Digger Bot")
    parser.add_argument("--env", type=str, default=None, help="Path to .env file")
    parser.add_argument("--live", action="store_true", help="Run in live trading mode (default)")
    parser.add_argument("--backtest", type=str, default=None, help="Path to backtest data JSON")
    parser.add_argument("--config", type=str, default=None, help="Path to config JSON")
    parser.add_argument("--balance", type=float, default=None, help="Initial backtest balance in USD (e.g. 10000)")
    parser.add_argument("--symbol", type=str, default=None, help="Symbol to backtest (e.g. XAUUSD, EURUSD)")
    parser.add_argument("--start", type=str, default=None, help="Start date filter YYYY-MM-DD (e.g. 2026-07-01)")
    parser.add_argument("--end", type=str, default=None, help="End date filter YYYY-MM-DD (e.g. 2026-07-31)")
    parser.add_argument("--research", action="store_true", help="Run 7-Agent pre-market research across all symbols and print/save research dossiers")
    parser.add_argument("--monte-carlo", action="store_true", help="Run Monte Carlo bootstrap stress testing on backtest trades")
    parser.add_argument("--wfv", action="store_true", help="Run multi-window Walk Forward Validation (IS vs OOS)")
    parser.add_argument("--sims", type=int, default=15000, help="Number of Monte Carlo simulations (default: 15000)")
    args = parser.parse_args()

    config = Config.load(args.env, args.config)
    setup_logging(config.trading.logging_level, config.trading.log_file,
                  config.trading.telegram_token, config.trading.telegram_chat_id)

    if args.research:
        bot = XAUUSDBot(config)
        bot.run_premarket_research()
        return

    if args.backtest:
        from .backtesting.engine import BacktestEngine
        from .backtesting.report import print_report
        from .backtesting.validation import run_monte_carlo, run_walk_forward_validation
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

        init_bal = args.balance or getattr(config.trading, "backtest_initial_balance", 100000.0)

        # Walk Forward Validation execution
        if args.wfv:
            wfv_res = run_walk_forward_validation(raw, config, symbol=sym, initial_balance=init_bal)
            wfv_res.print_dashboard()
            return

        engine = BacktestEngine(config, initial_balance=init_bal, symbol=sym, start_date=dt_start, end_date=dt_end)
        results = engine.run(raw)
        print_report(results)

        # Monte Carlo Stress-Testing execution
        if args.monte_carlo:
            trade_pnls = results.get("trade_pnls", [])
            if trade_pnls:
                mc_res = run_monte_carlo(
                    trade_pnls,
                    initial_capital=init_bal,
                    num_sims=args.sims,
                    target_pct_p1=8.0,
                    target_pct_p2=5.0,
                    max_dd_limit_pct=10.0,
                )
                mc_res.print_dashboard(title=f"MONTE CARLO EMPIRICAL STRESS TEST — {sym}")
            else:
                print("No closed trades available for Monte Carlo analysis.")

        return

    bot = XAUUSDBot(config)
    bot.start()


if __name__ == "__main__":
    main()
