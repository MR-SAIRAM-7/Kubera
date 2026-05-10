from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from math import sqrt
from statistics import mean, pstdev
from typing import Any, Deque, Dict, List, Optional, Tuple, TypedDict
import importlib

from langgraph.graph import END, StateGraph

_market_ai = importlib.import_module(f"{__package__}.market_ai" if __package__ else "market_ai")
FundamentalAgent = _market_ai.FundamentalAgent
HistoricalProbabilityEngine = _market_ai.HistoricalProbabilityEngine
PortfolioAgent = _market_ai.PortfolioAgent
RegimeAgent = _market_ai.RegimeAgent
run_walk_forward_backtest = _market_ai.run_walk_forward_backtest

# Institutional guardrail: enforce minimum 1:2 RR before any trade can pass risk veto.
RISK_ACCEPTABLE_MIN_RR = 2.0
RISK_ACCEPTABLE_MAX_VOLATILITY = 0.03
LOW_VOLATILITY_THRESHOLD = 0.02
POSITION_SIZE_HIGH = 2.0
POSITION_SIZE_MEDIUM = 1.0
POSITION_SIZE_LOW = 0.5
POSITION_SIZE_MEDIUM_MIN_RR = 1.2
ATR_SL_MULTIPLIER = 1.2
ATR_TP_MULTIPLIER = 2.4
VAR_SL_MULTIPLIER = 0.9
VAR_TP_MULTIPLIER = 1.8

POSITIVE_WORDS = {
    "deal",
    "growth",
    "profit",
    "beats",
    "surge",
    "contract",
    "approval",
    "bullish",
    "record",
    "expansion",
    "upgrade",
    "partnership",
}
NEGATIVE_WORDS = {
    "probe",
    "decline",
    "loss",
    "fraud",
    "downgrade",
    "penalty",
    "volatility",
    "bearish",
    "delay",
    "lawsuit",
    "default",
    "ban",
}


class MarketAgentState(TypedDict, total=False):
    symbol: str
    quote_payload: Dict[str, Any]
    news_payload: Dict[str, Any]
    candles: List[Dict[str, float]]
    technical: Dict[str, Any]
    fundamental: Dict[str, Any]
    sentiment: Dict[str, Any]
    regime: Dict[str, Any]
    risk: Dict[str, Any]
    portfolio: Dict[str, Any]
    synthesis: Dict[str, Any]
    degraded_mode: bool
    degrade_reasons: List[str]


class ScenarioStore:
    def __init__(self, max_samples_per_scenario: int = 500):
        self.max_samples_per_scenario = max_samples_per_scenario
        self.scenarios: Dict[str, Deque[bool]] = {}
        self.pending_predictions: Dict[str, Dict[str, Any]] = {}
        self._counter = 0

    def register_prediction(
        self,
        scenario_key: str,
        symbol: str,
        entry_price: float,
        signal: str,
        stop_loss: float,
        take_profit: float,
    ) -> str:
        self._counter += 1
        prediction_id = f"p_{self._counter}"
        self.pending_predictions[prediction_id] = {
            "scenario_key": scenario_key,
            "symbol": symbol,
            "entry_price": entry_price,
            "signal": signal,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return prediction_id

    def resolve_prediction(self, prediction_id: str, exit_price: float) -> Optional[Dict[str, Any]]:
        prediction = self.pending_predictions.pop(prediction_id, None)
        if prediction is None:
            return None

        scenario_key = prediction["scenario_key"]
        signal = prediction.get("signal", "HOLD")
        stop_loss = float(prediction.get("stop_loss", 0))
        take_profit = float(prediction.get("take_profit", 0))

        if signal == "BUY":
            outcome_up = bool(exit_price >= take_profit) or bool(exit_price > prediction["entry_price"] and exit_price > stop_loss)
        elif signal == "SELL":
            outcome_up = bool(exit_price <= take_profit) or bool(exit_price < prediction["entry_price"] and exit_price < stop_loss)
        else:
            outcome_up = False

        if scenario_key not in self.scenarios:
            self.scenarios[scenario_key] = deque(maxlen=self.max_samples_per_scenario)
        self.scenarios[scenario_key].append(outcome_up)

        history = self.scenarios[scenario_key]
        wins = sum(1 for outcome in history if outcome)
        return {
            "prediction_id": prediction_id,
            "scenario_key": scenario_key,
            "samples": len(history),
            "wins": wins,
            "win_probability": round((wins / len(history)) * 100, 2) if history else 50.0,
        }


class VectorBacktestStore:
    """In-memory vector store with top-k cosine similarity search."""

    def __init__(self, max_items_per_symbol: int = 2500):
        self.max_items_per_symbol = max_items_per_symbol
        self._vectors: Dict[str, Deque[Dict[str, Any]]] = defaultdict(lambda: deque(maxlen=self.max_items_per_symbol))

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sqrt(sum(x * x for x in a))
        norm_b = sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def add_setup(self, symbol: str, vector: List[float], metadata: Dict[str, Any], success: bool) -> None:
        self._vectors[symbol.upper()].append(
            {
                "vector": vector,
                "metadata": metadata,
                "success": success,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def query_similar_setups(self, symbol: str, vector: List[float], top_k: int = 500) -> List[Dict[str, Any]]:
        items = self._vectors.get(symbol.upper(), deque())
        ranked = sorted(
            (
                {
                    **row,
                    "similarity": self._cosine_similarity(vector, row["vector"]),
                }
                for row in items
            ),
            key=lambda row: row["similarity"],
            reverse=True,
        )
        return ranked[: max(1, top_k)]


class PaperTradingEngine:
    def __init__(self):
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.closed_positions: List[Dict[str, Any]] = []

    def execute_signal(
        self,
        symbol: str,
        signal: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        probability: float,
    ) -> Optional[Dict[str, Any]]:
        symbol = symbol.upper()
        if signal not in {"BUY", "SELL"}:
            return None

        position = {
            "symbol": symbol,
            "side": signal,
            "entry_price": round(entry_price, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "probability": round(probability, 2),
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "status": "OPEN",
            "unrealized_pnl": 0.0,
        }
        self.positions[symbol] = position
        return position

    def mark_price(self, symbol: str, current_price: float) -> Optional[Dict[str, Any]]:
        symbol = symbol.upper()
        position = self.positions.get(symbol)
        if not position:
            return None

        side = position["side"]
        entry = float(position["entry_price"])
        sl = float(position["stop_loss"])
        tp = float(position["take_profit"])

        if side == "BUY":
            pnl = current_price - entry
            hit_sl = current_price <= sl
            hit_tp = current_price >= tp
        else:
            pnl = entry - current_price
            hit_sl = current_price >= sl
            hit_tp = current_price <= tp

        if hit_sl or hit_tp:
            position["status"] = "CLOSED"
            position["closed_at"] = datetime.now(timezone.utc).isoformat()
            position["exit_price"] = round(current_price, 2)
            position["realized_pnl"] = round(pnl, 2)
            position["unrealized_pnl"] = 0.0
            position["closure_reason"] = "TP" if hit_tp else "SL"
            self.closed_positions.append(position.copy())
            self.positions.pop(symbol, None)
        else:
            position["unrealized_pnl"] = round(pnl, 2)

        return position

    def get_summary(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        if symbol:
            sym = symbol.upper()
            open_pos = [self.positions[sym]] if sym in self.positions else []
            closed = [row for row in self.closed_positions if row.get("symbol") == sym]
        else:
            open_pos = list(self.positions.values())
            closed = list(self.closed_positions)

        realized = sum(float(row.get("realized_pnl", 0.0)) for row in closed)
        unrealized = sum(float(row.get("unrealized_pnl", 0.0)) for row in open_pos)
        wins = sum(1 for row in closed if float(row.get("realized_pnl", 0.0)) > 0)
        win_rate = round((wins / len(closed)) * 100, 2) if closed else 0.0
        return {
            "open_positions": open_pos,
            "closed_positions": closed[-50:],
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_trades": len(closed),
            "win_rate": win_rate,
        }


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return fallback


def _from_series_rows(rows: List[Dict[str, Any]], points: int = 240) -> List[Dict[str, float]]:
    candles: List[Dict[str, float]] = []
    selected = rows[-points:] if rows else []
    for idx, row in enumerate(selected):
        open_p = _to_float(row.get("CH_OPENING_PRICE", row.get("open", 0)))
        high_p = _to_float(row.get("CH_TRADE_HIGH_PRICE", row.get("high", 0)))
        low_p = _to_float(row.get("CH_TRADE_LOW_PRICE", row.get("low", 0)))
        close_p = _to_float(row.get("CH_CLOSING_PRICE", row.get("close", 0)))
        if close_p <= 0:
            continue
        if open_p <= 0:
            open_p = close_p
        if high_p <= 0:
            high_p = max(open_p, close_p)
        if low_p <= 0:
            low_p = min(open_p, close_p)
        candles.append(
            {
                "t": idx,
                "open": round(open_p, 2),
                "high": round(max(high_p, open_p, close_p), 2),
                "low": round(max(0.01, min(low_p, open_p, close_p)), 2),
                "close": round(close_p, 2),
            }
        )
    return candles


def _fetch_live_candles(
    symbol: str,
    nse_raw_fetcher: Optional[Any] = None,
    lookback_points: int = 240,
) -> List[Dict[str, float]]:
    if nse_raw_fetcher is None:
        raise ValueError("Live market data source is required")

    for path in ("/api/chart-databyindex", "/api/chart-databysymbol", "/api/historical/cm/equity"):
        try:
            payload = nse_raw_fetcher(path, symbol)
            if not payload:
                continue
            if isinstance(payload, dict):
                rows = (
                    payload.get("grapthData")
                    or payload.get("graphData")
                    or payload.get("data")
                    or payload.get("rows")
                    or payload.get("candles")
                    or []
                )
            elif isinstance(payload, list):
                rows = payload
            else:
                rows = []
            if not rows:
                continue
            normalized = _from_series_rows(rows=rows, points=lookback_points)
            if len(normalized) >= 30:
                return normalized
        except Exception:
            continue

    raise ValueError(f"Live intraday/historical candles are unavailable for {symbol}")


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1)
    value = values[0]
    for entry in values[1:]:
        value = alpha * entry + (1 - alpha) * value
    return value


def _macd_series(closes: List[float]) -> Tuple[List[float], float]:
    if not closes:
        return [], 0.0

    alpha_12 = 2.0 / (12 + 1)
    alpha_26 = 2.0 / (26 + 1)
    ema12 = closes[0]
    ema26 = closes[0]
    macd_values: List[float] = []
    for close in closes:
        ema12 = alpha_12 * close + (1 - alpha_12) * ema12
        ema26 = alpha_26 * close + (1 - alpha_26) * ema26
        macd_values.append(ema12 - ema26)
    return macd_values, macd_values[-1]


def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains: List[float] = []
    losses: List[float] = []
    for idx in range(1, period + 1):
        delta = closes[-idx] - closes[-idx - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _bollinger(closes: List[float], period: int = 20, std_dev: float = 2.0) -> Dict[str, float]:
    window = closes[-period:] if len(closes) >= period else closes
    mid = mean(window)
    sd = pstdev(window) if len(window) > 1 else 0
    return {
        "middle": round(mid, 2),
        "upper": round(mid + std_dev * sd, 2),
        "lower": round(mid - std_dev * sd, 2),
    }


def _atr(candles: List[Dict[str, float]], period: int = 14) -> float:
    if not candles:
        return 0.0
    selected = candles[-(period + 1) :]
    tr_values: List[float] = []
    prev_close = selected[0]["close"]
    for row in selected[1:]:
        tr = max(
            row["high"] - row["low"],
            abs(row["high"] - prev_close),
            abs(row["low"] - prev_close),
        )
        tr_values.append(tr)
        prev_close = row["close"]
    return mean(tr_values) if tr_values else 0.0


def technical_agent(symbol: str, candles: List[Dict[str, float]]) -> Dict[str, Any]:
    if len(candles) < 5:
        return {
            "agent": "technical",
            "symbol": symbol,
            "bias": "neutral",
            "confidence": 0.1,
            "detected_pattern": "Insufficient candles",
            "order_blocks": [],
            "indicators": {},
        }

    closes = [row["close"] for row in candles]
    highs = [row["high"] for row in candles]
    lows = [row["low"] for row in candles]

    sma_20 = mean(closes[-20:]) if len(closes) >= 20 else mean(closes)
    sma_50 = mean(closes[-50:]) if len(closes) >= 50 else mean(closes)
    ema_200 = _ema(closes[-240:] if len(closes) >= 240 else closes, 200)
    rsi_14 = _rsi(closes=closes, period=14)
    macd_values, macd = _macd_series(closes[-120:])
    signal_line = _ema(macd_values[-9:] if len(macd_values) >= 9 else macd_values, 9)
    bollinger = _bollinger(closes)

    support = min(lows[-50:]) if len(lows) >= 50 else min(lows)
    resistance = max(highs[-50:]) if len(highs) >= 50 else max(highs)
    last_close = closes[-1]

    near_support = abs(last_close - support) / max(last_close, 1) <= 0.01
    near_resistance = abs(resistance - last_close) / max(last_close, 1) <= 0.01
    bullish_divergence = closes[-1] > closes[-5] and rsi_14 > 50
    bearish_divergence = closes[-1] < closes[-5] and rsi_14 < 50
    atr_14 = _atr(candles, period=14)
    avg_volume_proxy = mean([row.get("volume", 0.0) for row in candles[-20:]]) if candles else 0.0

    bullish_votes = int(sma_20 > sma_50) + int(macd > signal_line) + int(rsi_14 > 52) + int(near_support) + int(bullish_divergence)
    bearish_votes = int(sma_20 < sma_50) + int(macd < signal_line) + int(rsi_14 < 45) + int(near_resistance) + int(bearish_divergence)

    if bullish_votes > bearish_votes:
        bias = "bullish"
    elif bearish_votes > bullish_votes:
        bias = "bearish"
    else:
        bias = "neutral"

    confidence = round(min(0.96, max(0.1, 0.5 + (bullish_votes - bearish_votes) * 0.08)), 3)

    order_blocks = [
        {
            "type": "demand" if near_support else "supply",
            "start_t": max(0, candles[-1]["t"] - 20),
            "end_t": candles[-1]["t"],
            "low": round(support * 0.997, 2) if near_support else round(resistance * 0.995, 2),
            "high": round(support * 1.003, 2) if near_support else round(resistance * 1.005, 2),
        }
    ]

    return {
        "agent": "technical",
        "symbol": symbol,
        "bias": bias,
        "confidence": confidence,
        "detected_pattern": "Bullish divergence" if bullish_divergence else ("Bearish divergence" if bearish_divergence else "Trend continuation"),
        "order_blocks": order_blocks,
        "indicators": {
            "sma_20": round(sma_20, 2),
            "sma_50": round(sma_50, 2),
            "ema_200": round(ema_200, 2),
            "rsi_14": round(rsi_14, 2),
            "macd": round(macd, 4),
            "signal_line": round(signal_line, 4),
            "bollinger": bollinger,
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "last_close": round(last_close, 2),
            "atr_14": round(atr_14, 2),
            "volume_spike_proxy": round(avg_volume_proxy, 2),
        },
    }


def sentiment_agent(symbol: str, news_payload: Dict[str, Any]) -> Dict[str, Any]:
    items = news_payload.get("items", []) if isinstance(news_payload, dict) else []
    if not isinstance(items, list):
        items = []

    score = 0
    alpha_signals: List[str] = []
    for item in items[:100]:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        pos_hits = sum(1 for token in POSITIVE_WORDS if token in text)
        neg_hits = sum(1 for token in NEGATIVE_WORDS if token in text)
        item_score = (pos_hits - neg_hits) * 5
        score += item_score
        if abs(item_score) >= 10:
            alpha_signals.append(item.get("title", ""))

    normalized = max(-100, min(100, score))
    bias = "bullish" if normalized > 15 else ("bearish" if normalized < -15 else "neutral")
    confidence = round(min(0.95, 0.45 + min(abs(normalized), 90) / 210), 3)

    source_errors = news_payload.get("source_errors", []) if isinstance(news_payload, dict) else []
    degraded = bool(source_errors and len(source_errors) >= 2)

    return {
        "agent": "sentiment",
        "symbol": symbol,
        "sentiment_score": normalized,
        "bias": bias,
        "confidence": confidence,
        "samples_considered": len(items),
        "alpha_signals": alpha_signals[:6],
        "degraded": degraded,
        "source_errors": source_errors,
    }


def risk_agent(symbol: str, candles: List[Dict[str, float]], technical: Dict[str, Any], regime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if len(candles) < 2:
        return {
            "agent": "risk",
            "symbol": symbol,
            "risk_state": "blocked",
            "veto": True,
            "veto_reasons": ["INSUFFICIENT_PRICE_HISTORY"],
            "risk_warning": "Risk filter veto active due to insufficient price history.",
            "confidence": 0.1,
        }
    closes = [row["close"] for row in candles]
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    price = closes[-1]
    var_95 = abs(price * volatility * 1.65)
    atr = _atr(candles, period=14)

    bias = technical.get("bias", "neutral")
    if bias == "bullish":
        stop_loss = price - max(atr * ATR_SL_MULTIPLIER, var_95 * VAR_SL_MULTIPLIER)
        take_profit = price + max(atr * ATR_TP_MULTIPLIER, var_95 * VAR_TP_MULTIPLIER)
    elif bias == "bearish":
        stop_loss = price + max(atr * ATR_SL_MULTIPLIER, var_95 * VAR_SL_MULTIPLIER)
        take_profit = price - max(atr * ATR_TP_MULTIPLIER, var_95 * VAR_TP_MULTIPLIER)
    else:
        stop_loss = price - atr
        take_profit = price + atr * 1.5

    rr = abs(take_profit - price) / max(abs(price - stop_loss), 0.0001)
    risk_state = "acceptable" if rr >= RISK_ACCEPTABLE_MIN_RR and volatility < RISK_ACCEPTABLE_MAX_VOLATILITY else "elevated"
    regime = regime or {}
    veto_reasons: List[str] = []
    if rr < RISK_ACCEPTABLE_MIN_RR:
        veto_reasons.append("RR_BELOW_MINIMUM")
    if volatility >= RISK_ACCEPTABLE_MAX_VOLATILITY:
        veto_reasons.append("VOLATILITY_EXTREME")
    if regime and not regime.get("passes_trading_filter", True):
        veto_reasons.append(f"REGIME_BLOCK:{regime.get('label')}")
    veto = bool(veto_reasons)
    position_size_pct = (
        POSITION_SIZE_HIGH
        if risk_state == "acceptable" and volatility < LOW_VOLATILITY_THRESHOLD
        else (POSITION_SIZE_MEDIUM if rr >= POSITION_SIZE_MEDIUM_MIN_RR else POSITION_SIZE_LOW)
    )

    return {
        "agent": "risk",
        "symbol": symbol,
        "volatility": round(volatility, 5),
        "value_at_risk_95": round(var_95, 2),
        "atr_14": round(atr, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "risk_reward_ratio": round(rr, 2),
        "risk_state": risk_state,
        "position_size_pct": round(position_size_pct, 2),
        "veto": veto,
        "veto_reasons": veto_reasons,
        "confidence": 0.62 if risk_state == "acceptable" and not veto else 0.4,
        "risk_warning": f"Risk filter veto active: {', '.join(veto_reasons)}" if veto else "Risk profile acceptable for strategy constraints.",
    }


def _scenario_key(technical: Dict[str, Any], sentiment: Dict[str, Any], risk: Dict[str, Any]) -> str:
    trend = technical.get("bias", "neutral")
    sentiment_score = sentiment.get("sentiment_score", 0)
    sentiment_bucket = "positive" if sentiment_score >= 25 else ("negative" if sentiment_score <= -25 else "neutral")
    macd = technical.get("indicators", {}).get("macd", 0.0)
    signal = technical.get("indicators", {}).get("signal_line", 0.0)
    macd_state = "cross_up" if macd > signal else ("cross_down" if macd < signal else "flat")
    risk_state = risk.get("risk_state", "elevated")
    return f"{trend}|{sentiment_bucket}|{macd_state}|{risk_state}"


def _setup_vector(technical: Dict[str, Any], sentiment: Dict[str, Any], risk: Dict[str, Any]) -> List[float]:
    indicators = technical.get("indicators", {})
    trend = technical.get("bias", "neutral")
    trend_up = 1.0 if trend == "bullish" else 0.0
    trend_down = 1.0 if trend == "bearish" else 0.0
    return [
        indicators.get("rsi_14", 50.0) / 100.0,
        indicators.get("macd", 0.0),
        indicators.get("signal_line", 0.0),
        sentiment.get("sentiment_score", 0.0) / 100.0,
        risk.get("volatility", 0.0),
        min(5.0, max(0.0, risk.get("risk_reward_ratio", 0.0))) / 5.0,
        trend_up,
        trend_down,
    ]


def synthesizer_agent(
    symbol: str,
    technical: Dict[str, Any],
    sentiment: Dict[str, Any],
    risk: Dict[str, Any],
    regime: Dict[str, Any],
    fundamental: Dict[str, Any],
    portfolio: Dict[str, Any],
    vector_store: VectorBacktestStore,
    degraded_mode: bool,
) -> Dict[str, Any]:
    scenario_key = _scenario_key(technical=technical, sentiment=sentiment, risk=risk)
    setup_vector = _setup_vector(technical=technical, sentiment=sentiment, risk=risk)

    matches = vector_store.query_similar_setups(symbol=symbol, vector=setup_vector, top_k=500)
    probability_engine = HistoricalProbabilityEngine()
    probability_snapshot = probability_engine.compute(
        matches=matches,
        liquidity_score=1.0 if risk.get("position_size_pct", 0) >= 1 else 0.45,
        regime_match=regime.get("passes_trading_filter", False),
        news_shock=bool(degraded_mode or abs(float(sentiment.get("sentiment_score", 0))) >= 70),
    )
    total = probability_snapshot["sample_size"]
    wins = probability_snapshot["wins"]

    probability = probability_snapshot["calibrated_probability"]

    technical_signal = 1 if technical.get("bias") == "bullish" else (-1 if technical.get("bias") == "bearish" else 0)
    sentiment_signal = sentiment.get("sentiment_score", 0) / 100
    blended = 50 + technical_signal * 12 + sentiment_signal * 18
    fundamental_component = (float(fundamental.get("score", 50.0)) - 50.0) * 0.18
    regime_component = 6 if regime.get("label") == "bull-trend" else (-6 if regime.get("label") in {"bear-trend", "high-volatility"} else 0)
    blended = blended + fundamental_component + regime_component
    final_probability = 0.7 * probability + 0.3 * blended if total > 0 else blended

    if degraded_mode:
        final_probability *= 0.9

    final_probability = max(5.0, min(95.0, final_probability))

    rr = risk.get("risk_reward_ratio", 0)
    risk_veto = risk.get("veto", False)
    portfolio_veto = portfolio.get("veto", False)
    regime_pass = regime.get("passes_trading_filter", False)

    if risk_veto or portfolio_veto or not regime_pass:
        signal = "HOLD"
    elif final_probability >= 60 and rr >= 2.0:
        signal = "BUY"
    elif final_probability <= 40 and rr >= 2.0:
        signal = "SELL"
    else:
        signal = "HOLD"

    confidence_band = "high" if final_probability >= 68 else ("medium" if final_probability >= 53 else "low")
    consensus_score = round(abs(technical_signal + sentiment_signal) / 2, 2)
    entry_price = technical.get("indicators", {}).get("last_close", 0.0)
    entry_zone = {"low": round(entry_price * 0.995, 2), "high": round(entry_price * 1.005, 2)}

    justification = (
        f"{signal} with {round(final_probability, 2)}% probability from Bayesian top-{total} setup matching "
        f"({wins}/{total} successes). Technical={technical.get('bias')}, sentiment={sentiment.get('sentiment_score')}, "
        f"RR={rr}, regime={regime.get('label')}, fundamental={fundamental.get('score')}, degraded_mode={degraded_mode}."
    )

    return {
        "agent": "synthesizer",
        "symbol": symbol,
        "signal": signal,
        "win_probability": round(final_probability, 2),
        "probability_engine": probability_snapshot,
        "entry_zone": entry_zone,
        "stop_loss": risk.get("stop_loss"),
        "target": risk.get("take_profit"),
        "time_horizon": "swing-5-to-20-sessions",
        "consensus_score": consensus_score,
        "confidence_band": confidence_band,
        "historical_wins": wins,
        "historical_matches": total,
        "scenario_key": scenario_key,
        "justification": justification,
        "degraded_mode": degraded_mode,
        "decision_trace": {
            "technical": {"bias": technical.get("bias"), "confidence": technical.get("confidence")},
            "sentiment": {"score": sentiment.get("sentiment_score"), "confidence": sentiment.get("confidence")},
            "fundamental": {"score": fundamental.get("score"), "period": fundamental.get("latest_filed_period")},
            "regime": {"label": regime.get("label"), "confidence": regime.get("confidence")},
            "risk": {"veto": risk.get("veto"), "reasons": risk.get("veto_reasons", [])},
            "portfolio": {"veto": portfolio.get("veto"), "reasons": portfolio.get("veto_reasons", [])},
        },
        "confidence_modifier": 0.9 if degraded_mode else 1.0,
    }


def build_brain_log(
    technical: Dict[str, Any],
    sentiment: Dict[str, Any],
    risk: Dict[str, Any],
    synthesis: Dict[str, Any],
    regime: Optional[Dict[str, Any]] = None,
    fundamental: Optional[Dict[str, Any]] = None,
    degrade_reasons: Optional[List[str]] = None,
    portfolio: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    now = datetime.now(timezone.utc).isoformat()
    regime = regime or {}
    fundamental = fundamental or {}
    portfolio = portfolio or {}
    degrade_reasons = degrade_reasons or []
    logs = [
        {
            "timestamp": now,
            "agent": "Technical Analyst",
            "message": (
                f"{technical.get('bias')} | RSI={technical.get('indicators', {}).get('rsi_14')} "
                f"MACD={technical.get('indicators', {}).get('macd')}"
            ),
        },
        {
            "timestamp": now,
            "agent": "Sentiment Analyst",
            "message": f"Score={sentiment.get('sentiment_score')} from {sentiment.get('samples_considered')} items.",
        },
        {
            "timestamp": now,
            "agent": "Fundamental Analyst",
            "message": f"Score={fundamental.get('score')} period={fundamental.get('latest_filed_period')}",
        },
        {
            "timestamp": now,
            "agent": "Regime Detector",
            "message": f"Regime={regime.get('label')} confidence={regime.get('confidence')}",
        },
        {
            "timestamp": now,
            "agent": "Risk Manager",
            "message": (
                f"VaR={risk.get('value_at_risk_95')} RR={risk.get('risk_reward_ratio')} "
                f"SL={risk.get('stop_loss')} TP={risk.get('take_profit')} veto={risk.get('veto')}"
            ),
        },
        {
            "timestamp": now,
            "agent": "Portfolio Controller",
            "message": f"Allocation={portfolio.get('allocation_notional')} veto={portfolio.get('veto')}",
        },
        {
            "timestamp": now,
            "agent": "Chief Synthesizer",
            "message": f"{synthesis.get('signal')} @ {synthesis.get('win_probability')}% ({synthesis.get('historical_wins')}/{synthesis.get('historical_matches')})",
        },
    ]
    for reason in degrade_reasons:
        logs.append({"timestamp": now, "agent": "Chief Synthesizer", "message": f"Degraded mode: {reason}"})
    return logs


def build_langgraph_orchestrator(
    symbol: str,
    quote_payload: Dict[str, Any],
    news_payload: Dict[str, Any],
    candles: List[Dict[str, float]],
    vector_store: VectorBacktestStore,
) -> Dict[str, Any]:
    graph = StateGraph(MarketAgentState)

    def technical_node(state: MarketAgentState) -> MarketAgentState:
        technical = technical_agent(symbol=state["symbol"], candles=state["candles"])
        return {"technical": technical}

    def fundamental_node(state: MarketAgentState) -> MarketAgentState:
        market = state.get("quote_payload", {})
        filings = []
        if isinstance(market, dict):
            filings = market.get("filings", []) or market.get("financials", []) or []
        fundamental = FundamentalAgent().analyze(symbol=state["symbol"], filings=filings)
        return {"fundamental": fundamental}

    def sentiment_node(state: MarketAgentState) -> MarketAgentState:
        sentiment = sentiment_agent(symbol=state["symbol"], news_payload=state["news_payload"])
        degraded = bool(sentiment.get("degraded"))
        reasons: List[str] = []
        if degraded:
            reasons.append("News sources rate-limited/unavailable; sentiment confidence reduced.")
        return {"sentiment": sentiment, "degraded_mode": degraded, "degrade_reasons": reasons}

    def regime_node(state: MarketAgentState) -> MarketAgentState:
        regime = RegimeAgent().classify(symbol=state["symbol"], candles=state["candles"])
        return {"regime": regime}

    def risk_node(state: MarketAgentState) -> MarketAgentState:
        risk = risk_agent(symbol=state["symbol"], candles=state["candles"], technical=state["technical"], regime=state["regime"])
        return {"risk": risk}

    def portfolio_node(state: MarketAgentState) -> MarketAgentState:
        portfolio = PortfolioAgent().plan(
            cash=1_000_000.0,
            positions=[],
            candidate={"symbol": state["symbol"]},
            risk=state["risk"],
            sector=state["fundamental"].get("sector", "UNKNOWN"),
        )
        return {"portfolio": portfolio}

    def synthesizer_node(state: MarketAgentState) -> MarketAgentState:
        synthesis = synthesizer_agent(
            symbol=state["symbol"],
            technical=state["technical"],
            sentiment=state["sentiment"],
            risk=state["risk"],
            regime=state["regime"],
            fundamental=state["fundamental"],
            portfolio=state["portfolio"],
            vector_store=vector_store,
            degraded_mode=bool(state.get("degraded_mode", False)),
        )
        return {"synthesis": synthesis}

    graph.add_node("technical_agent", technical_node)
    graph.add_node("fundamental_agent", fundamental_node)
    graph.add_node("sentiment_agent", sentiment_node)
    graph.add_node("regime_agent", regime_node)
    graph.add_node("risk_agent", risk_node)
    graph.add_node("portfolio_agent", portfolio_node)
    graph.add_node("synthesizer_agent", synthesizer_node)

    graph.set_entry_point("technical_agent")
    graph.add_edge("technical_agent", "fundamental_agent")
    graph.add_edge("fundamental_agent", "sentiment_agent")
    graph.add_edge("sentiment_agent", "regime_agent")
    graph.add_edge("regime_agent", "risk_agent")
    graph.add_edge("risk_agent", "portfolio_agent")
    graph.add_edge("portfolio_agent", "synthesizer_agent")
    graph.add_edge("synthesizer_agent", END)

    app = graph.compile()
    state = app.invoke(
        {
            "symbol": symbol,
            "quote_payload": quote_payload,
            "news_payload": news_payload,
            "candles": candles,
            "degraded_mode": False,
            "degrade_reasons": [],
        }
    )
    return state


def build_dashboard_snapshot(
    symbol: str,
    quote_payload: Dict[str, Any],
    news_payload: Dict[str, Any],
    scenario_store: ScenarioStore,
    vector_store: VectorBacktestStore,
    paper_trader: PaperTradingEngine,
    nse_raw_fetcher: Optional[Any] = None,
    lookback_points: int = 240,
) -> Dict[str, Any]:
    candles = _fetch_live_candles(symbol=symbol, nse_raw_fetcher=nse_raw_fetcher, lookback_points=lookback_points)

    state = build_langgraph_orchestrator(
        symbol=symbol,
        quote_payload=quote_payload,
        news_payload=news_payload,
        candles=candles,
        vector_store=vector_store,
    )

    technical = state["technical"]
    fundamental = state["fundamental"]
    sentiment = state["sentiment"]
    regime = state["regime"]
    risk = state["risk"]
    portfolio = state["portfolio"]
    synthesis = state["synthesis"]
    degrade_reasons = state.get("degrade_reasons", [])

    market = quote_payload.get("stock", {}) if isinstance(quote_payload, dict) else {}
    current_price = _to_float(market.get("last_price"), fallback=candles[-1]["close"] if candles else 0.0)
    previous_close = _to_float(market.get("previous_close"), fallback=current_price)

    prediction_id = scenario_store.register_prediction(
        scenario_key=synthesis.get("scenario_key", "unknown"),
        symbol=symbol.upper(),
        entry_price=current_price,
        signal=synthesis.get("signal", "HOLD"),
        stop_loss=float(risk.get("stop_loss", 0.0)),
        take_profit=float(risk.get("take_profit", 0.0)),
    )

    if synthesis.get("signal") in {"BUY", "SELL"}:
        paper_trader.execute_signal(
            symbol=symbol,
            signal=synthesis["signal"],
            entry_price=current_price,
            stop_loss=float(risk.get("stop_loss", current_price)),
            take_profit=float(risk.get("take_profit", current_price)),
            probability=float(synthesis.get("win_probability", 50.0)),
        )

    paper_position = paper_trader.mark_price(symbol=symbol, current_price=current_price)

    markers: List[Dict[str, Any]] = []
    if synthesis.get("signal") in {"BUY", "SELL"}:
        markers.append(
            {
                "time": candles[-1]["t"],
                "price": current_price,
                "signal": synthesis.get("signal"),
            }
        )

    for trade in paper_trader.closed_positions:
        if trade.get("symbol") != symbol.upper() or trade.get("status") != "CLOSED":
            continue
        if trade.get("vector_recorded"):
            continue
        vector_store.add_setup(
            symbol=symbol,
            vector=_setup_vector(technical=technical, sentiment=sentiment, risk=risk),
            metadata={
                "scenario_key": synthesis.get("scenario_key"),
                "signal": trade.get("side"),
                "closure_reason": trade.get("closure_reason"),
            },
            success=bool(float(trade.get("realized_pnl", 0.0)) > 0),
        )
        trade["vector_recorded"] = True

    return {
        "symbol": symbol.upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "prediction_id": prediction_id,
        "market": {
            "current_price": round(current_price, 2),
            "previous_close": round(previous_close, 2),
            "candles": candles,
            "overlays": {
                "order_blocks": technical.get("order_blocks", []),
                "signal_markers": markers,
            },
            "news": news_payload.get("items", [])[:30] if isinstance(news_payload, dict) else [],
        },
        "agents": {
            "technical": technical,
            "sentiment": sentiment,
            "fundamental": fundamental,
            "regime": regime,
            "risk": risk,
            "portfolio": portfolio,
            "synthesizer": synthesis,
        },
        "paper_trading": {
            "latest_position": paper_position,
            "summary": paper_trader.get_summary(symbol=symbol),
        },
        "backtest": run_walk_forward_backtest(candles),
        "compliance": {
            "mode": "advisory",
            "live_trading_enabled": False,
            "human_approval_required": True,
            "kill_switch_enabled": True,
            "disclaimer": "Decision support only. This platform does not guarantee profits and is not investment advice.",
        },
        "brain_log": build_brain_log(
            technical=technical,
            sentiment=sentiment,
            risk=risk,
            synthesis=synthesis,
            regime=regime,
            fundamental=fundamental,
            portfolio=portfolio,
            degrade_reasons=degrade_reasons,
        ),
    }
