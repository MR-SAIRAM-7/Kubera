from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from math import sqrt
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple

UTC = timezone.utc
ASIA_KOLKATA = "Asia/Kolkata"
SYSTEM_MODE_ADVISORY = "advisory"
SYSTEM_MODE_PAPER = "paper"
SYSTEM_MODE_LIVE = "live"
SUPPORTED_MODES = {SYSTEM_MODE_ADVISORY, SYSTEM_MODE_PAPER, SYSTEM_MODE_LIVE}


class SignalDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WATCHLIST = "WATCHLIST"


@dataclass(frozen=True)
class SourceMetadata:
    source: str
    captured_at_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_timezone: str = ASIA_KOLKATA
    ingestion_latency_ms: int = 0
    freshness_confidence: float = 1.0
    stale: bool = False
    incomplete: bool = False


@dataclass(frozen=True)
class TradableInstrument:
    symbol: str
    exchange: str
    name: str = ""
    sector: str = "UNKNOWN"
    status: str = "ACTIVE"
    avg_traded_value: float = 0.0
    data_quality_score: float = 1.0
    metadata: SourceMetadata = field(default_factory=lambda: SourceMetadata(source="runtime"))


@dataclass(frozen=True)
class Position:
    symbol: str
    side: str
    quantity: int
    entry_price: float
    sector: str = "UNKNOWN"
    beta: float = 1.0


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    quantity: int
    order_type: str
    price: Optional[float]
    approved_by: Optional[str]
    strategy_version: str
    model_version: str
    input_snapshot: Dict[str, Any]


class EventBus(Protocol):
    def publish(self, topic: str, payload: Dict[str, Any]) -> None: ...


class InMemoryAuditEventBus:
    def __init__(self, max_events: int = 1000):
        self.max_events = max_events
        self.events: List[Dict[str, Any]] = []

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        event = {
            "topic": topic,
            "payload": payload,
            "published_at_utc": datetime.now(UTC).isoformat(),
        }
        self.events.append(event)
        if len(self.events) > self.max_events:
            self.events = self.events[-self.max_events :]

    def latest(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.events[-max(1, limit) :]


class UniverseAgent:
    """Build a clean NSE/BSE tradable universe without reading signals from other agents."""

    def __init__(self, min_avg_traded_value: float = 5_000_000, min_data_quality_score: float = 0.7):
        self.min_avg_traded_value = min_avg_traded_value
        self.min_data_quality_score = min_data_quality_score

    def scan(self, instruments: Iterable[TradableInstrument]) -> Dict[str, Any]:
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for instrument in instruments:
            reasons: List[str] = []
            if instrument.status.upper() != "ACTIVE":
                reasons.append("SUSPENDED_OR_INACTIVE")
            if instrument.avg_traded_value < self.min_avg_traded_value:
                reasons.append("ILLIQUID")
            if instrument.data_quality_score < self.min_data_quality_score:
                reasons.append("DATA_QUALITY_LOW")
            row = {
                "symbol": instrument.symbol.upper(),
                "exchange": instrument.exchange.upper(),
                "name": instrument.name,
                "sector": instrument.sector,
                "avg_traded_value": instrument.avg_traded_value,
                "data_quality_score": instrument.data_quality_score,
                "metadata": instrument.metadata.__dict__,
            }
            if reasons:
                row["reasons"] = reasons
                rejected.append(row)
            else:
                accepted.append(row)
        return {
            "agent": "universe",
            "accepted": accepted,
            "rejected": rejected,
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "rules": {
                "min_avg_traded_value": self.min_avg_traded_value,
                "min_data_quality_score": self.min_data_quality_score,
            },
        }


class FundamentalAgent:
    def analyze(self, symbol: str, filings: List[Dict[str, Any]]) -> Dict[str, Any]:
        latest = next((row for row in filings if row.get("audited") or row.get("filed")), filings[0] if filings else {})
        revenue_growth = _bounded_float(latest.get("revenue_growth"), -100, 100)
        profit_growth = _bounded_float(latest.get("profit_growth"), -100, 100)
        roe = _bounded_float(latest.get("roe"), -100, 100)
        roce = _bounded_float(latest.get("roce"), -100, 100)
        debt_equity = max(0.0, _to_float(latest.get("debt_equity"), 1.0))
        pledge_ratio = max(0.0, _to_float(latest.get("pledge_ratio"), 0.0))
        quality_score = max(0, min(100, 50 + roe * 0.5 + roce * 0.3 - debt_equity * 10 - pledge_ratio * 0.4))
        growth_score = max(0, min(100, 50 + revenue_growth * 0.35 + profit_growth * 0.35))
        leverage_score = max(0, min(100, 100 - debt_equity * 25))
        governance_score = max(0, min(100, 100 - pledge_ratio))
        overall = round(0.3 * quality_score + 0.25 * growth_score + 0.2 * leverage_score + 0.25 * governance_score, 2)
        return {
            "agent": "fundamental",
            "symbol": symbol.upper(),
            "score": overall,
            "value_score": round((quality_score + leverage_score) / 2, 2),
            "quality_score": round(quality_score, 2),
            "growth_score": round(growth_score, 2),
            "leverage_score": round(leverage_score, 2),
            "governance_score": round(governance_score, 2),
            "latest_filed_period": latest.get("period", "UNAVAILABLE"),
            "uses_only_latest_filed_data": bool(latest),
            "source_snapshot": latest,
        }


class RegimeAgent:
    def classify(self, symbol: str, candles: List[Dict[str, float]]) -> Dict[str, Any]:
        closes = [row["close"] for row in candles if row.get("close", 0) > 0]
        if len(closes) < 30:
            return {
                "agent": "regime",
                "symbol": symbol.upper(),
                "label": "insufficient-data",
                "confidence": 0.2,
                "features": {},
                "passes_trading_filter": False,
            }
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]
        slope_20 = (closes[-1] - closes[-20]) / max(closes[-20], 0.01)
        slope_50 = (closes[-1] - closes[-50]) / max(closes[-50], 0.01) if len(closes) >= 50 else slope_20
        volatility = pstdev(returns[-50:]) if len(returns) > 2 else 0.0
        vol_percentile_proxy = min(1.0, volatility / 0.035)
        momentum_persistence = sum(1 for r in returns[-20:] if r > 0) / max(1, min(20, len(returns)))
        if vol_percentile_proxy > 0.85:
            label = "high-volatility"
        elif abs(slope_20) < 0.015 and abs(slope_50) < 0.025:
            label = "range-bound"
        elif slope_20 > 0 and slope_50 > 0:
            label = "bull-trend"
        elif slope_20 < 0 and slope_50 < 0:
            label = "bear-trend"
        else:
            label = "transition-event"
        confidence = round(max(0.3, min(0.95, abs(slope_20) * 8 + abs(momentum_persistence - 0.5) + 0.4)), 3)
        return {
            "agent": "regime",
            "symbol": symbol.upper(),
            "label": label,
            "confidence": confidence,
            "passes_trading_filter": label not in {"high-volatility", "insufficient-data"},
            "features": {
                "index_slope_20_proxy": round(slope_20, 4),
                "index_slope_50_proxy": round(slope_50, 4),
                "volatility_percentile_proxy": round(vol_percentile_proxy, 4),
                "momentum_persistence": round(momentum_persistence, 4),
                "correlation_stress_proxy": round(min(1.0, volatility / 0.05), 4),
            },
        }


class HistoricalProbabilityEngine:
    def compute(
        self,
        matches: List[Dict[str, Any]],
        liquidity_score: float,
        regime_match: bool,
        news_shock: bool,
    ) -> Dict[str, Any]:
        total = len(matches)
        wins = sum(1 for row in matches if row.get("success"))
        base_probability = ((wins + 1) / (total + 2)) if total else 0.5
        sample_confidence = min(1.0, sqrt(total / 100)) if total else 0.15
        adjustment = 0.0
        if liquidity_score < 0.5:
            adjustment -= 0.05
        if not regime_match:
            adjustment -= 0.07
        if news_shock:
            adjustment -= 0.06
        calibrated = max(0.05, min(0.95, base_probability + adjustment))
        confidence = max(0.05, min(0.95, sample_confidence * (0.85 if news_shock else 1.0) * (1.0 if regime_match else 0.8)))
        return {
            "base_probability": round(base_probability * 100, 2),
            "calibrated_probability": round(calibrated * 100, 2),
            "confidence": round(confidence, 3),
            "sample_size": total,
            "wins": wins,
            "adjustments": {
                "liquidity_score": liquidity_score,
                "regime_match": regime_match,
                "news_shock": news_shock,
                "probability_adjustment_pct": round(adjustment * 100, 2),
            },
            "analogues": [
                {
                    "similarity": round(float(row.get("similarity", 0.0)), 4),
                    "success": bool(row.get("success")),
                    "metadata": row.get("metadata", {}),
                }
                for row in matches[:10]
            ],
        }


class PortfolioAgent:
    def __init__(self, max_single_stock_pct: float = 0.1, max_sector_pct: float = 0.3, max_heat_pct: float = 0.06):
        self.max_single_stock_pct = max_single_stock_pct
        self.max_sector_pct = max_sector_pct
        self.max_heat_pct = max_heat_pct

    def plan(
        self,
        cash: float,
        positions: List[Position],
        candidate: Dict[str, Any],
        risk: Dict[str, Any],
        sector: str = "UNKNOWN",
    ) -> Dict[str, Any]:
        equity = cash + sum(p.quantity * p.entry_price for p in positions)
        proposed_notional = equity * float(risk.get("position_size_pct", 0.0)) / 100
        symbol_exposure = sum(p.quantity * p.entry_price for p in positions if p.symbol == candidate.get("symbol"))
        sector_exposure = sum(p.quantity * p.entry_price for p in positions if p.sector == sector)
        total_heat = sum(abs(p.quantity * p.entry_price) for p in positions) / max(equity, 1)
        vetoes: List[str] = []
        if (symbol_exposure + proposed_notional) / max(equity, 1) > self.max_single_stock_pct:
            vetoes.append("MAX_SINGLE_STOCK_EXPOSURE")
        if (sector_exposure + proposed_notional) / max(equity, 1) > self.max_sector_pct:
            vetoes.append("MAX_SECTOR_EXPOSURE")
        if total_heat + proposed_notional / max(equity, 1) > self.max_heat_pct:
            vetoes.append("MAX_PORTFOLIO_HEAT")
        return {
            "agent": "portfolio",
            "allocation_notional": round(max(0.0, min(cash, proposed_notional)), 2),
            "portfolio_heat_pct": round(total_heat * 100, 2),
            "single_stock_exposure_pct": round((symbol_exposure + proposed_notional) / max(equity, 1) * 100, 2),
            "sector_exposure_pct": round((sector_exposure + proposed_notional) / max(equity, 1) * 100, 2),
            "veto": bool(vetoes),
            "veto_reasons": vetoes,
        }


class ExecutionAgent:
    def __init__(self, event_bus: EventBus, mode: str = SYSTEM_MODE_ADVISORY, kill_switch_enabled: bool = True):
        self.event_bus = event_bus
        self.mode = mode if mode in SUPPORTED_MODES else SYSTEM_MODE_ADVISORY
        self.kill_switch_enabled = kill_switch_enabled

    def set_mode(self, mode: str) -> None:
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"Unsupported execution mode: {mode}")
        self.mode = mode

    def set_kill_switch(self, enabled: bool) -> None:
        self.kill_switch_enabled = enabled
        self.event_bus.publish("execution.kill_switch", {"enabled": enabled})

    def submit(self, order: OrderRequest, risk: Dict[str, Any], portfolio: Dict[str, Any]) -> Dict[str, Any]:
        audit = {
            "order": order.__dict__,
            "risk_snapshot": risk,
            "portfolio_snapshot": portfolio,
            "requested_at_utc": datetime.now(UTC).isoformat(),
            "mode": self.mode,
            "kill_switch_enabled": self.kill_switch_enabled,
        }
        if self.kill_switch_enabled:
            status, reason = "BLOCKED", "KILL_SWITCH_ENABLED"
        elif self.mode != SYSTEM_MODE_LIVE:
            status, reason = "SIMULATED", f"{self.mode.upper()}_MODE_NO_LIVE_ORDER"
        elif not order.approved_by:
            status, reason = "BLOCKED", "HUMAN_APPROVAL_REQUIRED"
        elif risk.get("veto") or portfolio.get("veto"):
            status, reason = "BLOCKED", "RISK_OR_PORTFOLIO_VETO"
        else:
            status, reason = "READY_FOR_BROKER", "APPROVED_AUDITED_REQUEST"
        audit.update({"status": status, "reason": reason})
        self.event_bus.publish("execution.order_request", audit)
        return audit


def run_walk_forward_backtest(candles: List[Dict[str, float]], initial_cash: float = 100_000.0) -> Dict[str, Any]:
    closes = [row["close"] for row in candles if row.get("close", 0) > 0]
    if len(closes) < 80:
        return {
            "status": "insufficient-data",
            "metrics": {},
            "equity_curve": [],
            "message": "At least 80 candles are required for walk-forward validation.",
        }
    cash = initial_cash
    equity_curve: List[Dict[str, Any]] = []
    trades: List[float] = []
    peak = initial_cash
    max_drawdown = 0.0
    for idx in range(60, len(closes) - 5, 5):
        train = closes[idx - 60 : idx]
        test_exit = closes[idx + 5]
        signal = 1 if mean(train[-20:]) > mean(train[-50:]) else -1
        ret = signal * ((test_exit - closes[idx]) / max(closes[idx], 0.01))
        pnl = cash * 0.02 * ret
        cash += pnl
        trades.append(pnl)
        peak = max(peak, cash)
        max_drawdown = min(max_drawdown, (cash - peak) / max(peak, 1))
        equity_curve.append({"step": idx, "equity": round(cash, 2)})
    wins = [pnl for pnl in trades if pnl > 0]
    losses = [abs(pnl) for pnl in trades if pnl <= 0]
    returns = [pnl / initial_cash for pnl in trades]
    total_return = (cash - initial_cash) / initial_cash
    years = max(1 / 252, len(closes) / 252)
    cagr = (cash / initial_cash) ** (1 / years) - 1 if cash > 0 else -1
    sharpe = (mean(returns) / pstdev(returns) * sqrt(252)) if len(returns) > 2 and pstdev(returns) else 0.0
    downside = [r for r in returns if r < 0]
    sortino = (mean(returns) / pstdev(downside) * sqrt(252)) if len(downside) > 2 and pstdev(downside) else 0.0
    return {
        "status": "ok",
        "metrics": {
            "cagr_pct": round(cagr * 100, 2),
            "total_return_pct": round(total_return * 100, 2),
            "win_rate_pct": round(len(wins) / max(1, len(trades)) * 100, 2),
            "profit_factor": round(sum(wins) / max(0.01, sum(losses)), 2),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "turnover": len(trades),
            "benchmark": "SMA20/SMA50 walk-forward baseline",
        },
        "equity_curve": equity_curve,
    }


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return fallback


def _bounded_float(value: Any, low: float, high: float) -> float:
    return max(low, min(high, _to_float(value, 0.0)))
