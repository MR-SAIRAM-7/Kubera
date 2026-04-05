from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any, Deque, Dict, List, Optional, Tuple


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


def technical_agent(symbol: str, candles: List[Dict[str, float]]) -> Dict[str, Any]:
    closes = [row["close"] for row in candles]
    highs = [row["high"] for row in candles]
    lows = [row["low"] for row in candles]

    sma_20 = mean(closes[-20:]) if len(closes) >= 20 else mean(closes)
    sma_50 = mean(closes[-50:]) if len(closes) >= 50 else mean(closes)
    rsi_14 = _rsi(closes=closes, period=14)
    ema_12 = _ema(closes[-80:], 12)
    ema_26 = _ema(closes[-80:], 26)
    macd = ema_12 - ema_26
    signal_line = _ema([macd] * 9, 9)
    support = min(lows[-50:]) if len(lows) >= 50 else min(lows)
    resistance = max(highs[-50:]) if len(highs) >= 50 else max(highs)
    last_close = closes[-1]
    near_support = abs(last_close - support) / max(last_close, 1) <= 0.01
    near_resistance = abs(resistance - last_close) / max(last_close, 1) <= 0.01
    momentum = closes[-1] - closes[-6] if len(closes) > 6 else 0.0

    bullish_votes = int(sma_20 > sma_50) + int(macd > signal_line) + int(rsi_14 > 52) + int(momentum > 0) + int(near_support)
    bearish_votes = int(sma_20 < sma_50) + int(macd < signal_line) + int(rsi_14 < 45) + int(momentum < 0) + int(near_resistance)
    if bullish_votes > bearish_votes:
        bias = "bullish"
    elif bearish_votes > bullish_votes:
        bias = "bearish"
    else:
        bias = "neutral"

    confidence = round(min(0.96, max(0.1, 0.5 + (bullish_votes - bearish_votes) * 0.08)), 3)
    pattern = "Demand retest" if near_support else ("Supply rejection" if near_resistance else "Trend continuation")

    return {
        "agent": "technical",
        "symbol": symbol,
        "bias": bias,
        "confidence": confidence,
        "detected_pattern": pattern,
        "indicators": {
            "sma_20": round(sma_20, 2),
            "sma_50": round(sma_50, 2),
            "rsi_14": round(rsi_14, 2),
            "macd": round(macd, 4),
            "signal_line": round(signal_line, 4),
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "last_close": round(last_close, 2),
        },
    }


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
}


def sentiment_agent(symbol: str, news_payload: Dict[str, Any]) -> Dict[str, Any]:
    items = news_payload.get("items", []) if isinstance(news_payload, dict) else []
    if not isinstance(items, list):
        items = []

    score = 0
    alpha_signals: List[str] = []
    for item in items[:80]:
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

    return {
        "agent": "sentiment",
        "symbol": symbol,
        "sentiment_score": normalized,
        "bias": bias,
        "confidence": confidence,
        "samples_considered": len(items),
        "alpha_signals": alpha_signals[:5],
    }


def risk_agent(symbol: str, candles: List[Dict[str, float]], technical: Dict[str, Any]) -> Dict[str, Any]:
    closes = [row["close"] for row in candles]
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    price = closes[-1]
    var_95 = abs(price * volatility * 1.65)
    true_ranges = [abs(row["high"] - row["low"]) for row in candles[-20:]] or [price * 0.01]
    atr = mean(true_ranges)

    bias = technical.get("bias", "neutral")
    if bias == "bullish":
        stop_loss = price - max(atr * 1.2, var_95 * 0.9)
        take_profit = price + max(atr * 2.1, var_95 * 1.7)
    elif bias == "bearish":
        stop_loss = price + max(atr * 1.2, var_95 * 0.9)
        take_profit = price - max(atr * 2.1, var_95 * 1.7)
    else:
        stop_loss = price - atr
        take_profit = price + atr * 1.4

    rr = abs(take_profit - price) / max(abs(price - stop_loss), 0.0001)
    risk_state = "acceptable" if rr >= 1.5 and volatility < 0.03 else "elevated"

    return {
        "agent": "risk",
        "symbol": symbol,
        "volatility": round(volatility, 5),
        "value_at_risk_95": round(var_95, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "risk_reward_ratio": round(rr, 2),
        "risk_state": risk_state,
        "confidence": 0.58 if risk_state == "acceptable" else 0.42,
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


class ScenarioStore:
    def __init__(self, max_samples_per_scenario: int = 500):
        self.max_samples_per_scenario = max_samples_per_scenario
        self.scenarios: Dict[str, Deque[bool]] = {}
        self.pending_predictions: Dict[str, Dict[str, Any]] = {}
        self._counter = 0

    def register_prediction(self, scenario_key: str, symbol: str, entry_price: float) -> str:
        self._counter += 1
        prediction_id = f"p_{self._counter}"
        self.pending_predictions[prediction_id] = {
            "scenario_key": scenario_key,
            "symbol": symbol,
            "entry_price": entry_price,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return prediction_id

    def resolve_prediction(self, prediction_id: str, exit_price: float) -> Optional[Dict[str, Any]]:
        prediction = self.pending_predictions.pop(prediction_id, None)
        if prediction is None:
            return None

        scenario_key = prediction["scenario_key"]
        entry_price = prediction["entry_price"]
        outcome_up = bool(exit_price > entry_price)
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

    def get_probability(self, scenario_key: str) -> Tuple[int, int, float]:
        history = self.scenarios.get(scenario_key)
        if not history:
            return 0, 0, 50.0
        wins = sum(1 for outcome in history if outcome)
        total = len(history)
        probability = (wins / total) * 100 if total else 50.0
        return wins, total, round(probability, 2)


def synthesizer_agent(
    symbol: str,
    technical: Dict[str, Any],
    sentiment: Dict[str, Any],
    risk: Dict[str, Any],
    scenario_store: ScenarioStore,
) -> Dict[str, Any]:
    scenario_key = _scenario_key(technical=technical, sentiment=sentiment, risk=risk)
    wins, total, empirical_probability = scenario_store.get_probability(scenario_key)

    technical_signal = 1 if technical.get("bias") == "bullish" else (-1 if technical.get("bias") == "bearish" else 0)
    sentiment_signal = sentiment.get("sentiment_score", 0) / 100
    risk_multiplier = 1.0 if risk.get("risk_state") == "acceptable" else 0.75

    model_probability = 50 + technical_signal * 16 + sentiment_signal * 18
    final_probability = (empirical_probability * 0.7 + model_probability * 0.3) if total > 0 else model_probability
    final_probability = max(5.0, min(95.0, final_probability * risk_multiplier))

    if final_probability >= 60 and risk.get("risk_reward_ratio", 0) >= 1.5:
        signal = "BUY"
    elif final_probability <= 40 and risk.get("risk_reward_ratio", 0) >= 1.5:
        signal = "SELL"
    else:
        signal = "HOLD"

    justification = (
        f"{signal} with {round(final_probability, 2)}% probability. "
        f"Scenario={scenario_key}. "
        f"Empirical outcomes: {wins}/{total} wins. "
        f"Technical={technical.get('bias')} ({technical.get('detected_pattern')}), "
        f"Sentiment={sentiment.get('sentiment_score')}, "
        f"RR={risk.get('risk_reward_ratio')}."
    )
    return {
        "agent": "synthesizer",
        "symbol": symbol,
        "signal": signal,
        "win_probability": round(final_probability, 2),
        "historical_wins": wins,
        "historical_matches": total,
        "scenario_key": scenario_key,
        "justification": justification,
    }


def build_brain_log(
    technical: Dict[str, Any],
    sentiment: Dict[str, Any],
    risk: Dict[str, Any],
    synthesis: Dict[str, Any],
) -> List[Dict[str, str]]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "timestamp": now,
            "agent": "Technical Analyst",
            "message": f"{technical.get('bias')} | {technical.get('detected_pattern')} | RSI={technical.get('indicators', {}).get('rsi_14')}",
        },
        {
            "timestamp": now,
            "agent": "Sentiment Analyst",
            "message": f"Score={sentiment.get('sentiment_score')} from {sentiment.get('samples_considered')} items.",
        },
        {
            "timestamp": now,
            "agent": "Risk Manager",
            "message": f"VaR={risk.get('value_at_risk_95')} RR={risk.get('risk_reward_ratio')} SL={risk.get('stop_loss')} TP={risk.get('take_profit')}",
        },
        {
            "timestamp": now,
            "agent": "Chief Synthesizer",
            "message": f"{synthesis.get('signal')} at {synthesis.get('win_probability')}% ({synthesis.get('historical_wins')}/{synthesis.get('historical_matches')})",
        },
    ]


def build_dashboard_snapshot(
    symbol: str,
    quote_payload: Dict[str, Any],
    news_payload: Dict[str, Any],
    scenario_store: ScenarioStore,
    nse_raw_fetcher: Optional[Any] = None,
    lookback_points: int = 240,
) -> Dict[str, Any]:
    candles = _fetch_live_candles(
        symbol=symbol,
        nse_raw_fetcher=nse_raw_fetcher,
        lookback_points=lookback_points,
    )
    technical = technical_agent(symbol=symbol, candles=candles)
    sentiment = sentiment_agent(symbol=symbol, news_payload=news_payload)
    risk = risk_agent(symbol=symbol, candles=candles, technical=technical)
    synthesis = synthesizer_agent(
        symbol=symbol,
        technical=technical,
        sentiment=sentiment,
        risk=risk,
        scenario_store=scenario_store,
    )

    market = quote_payload.get("stock", {}) if isinstance(quote_payload, dict) else {}
    current_price = _to_float(market.get("last_price"), fallback=candles[-1]["close"] if candles else 0.0)
    previous_close = _to_float(market.get("previous_close"), fallback=current_price)
    prediction_id = scenario_store.register_prediction(
        scenario_key=synthesis.get("scenario_key", "unknown"),
        symbol=symbol.upper(),
        entry_price=current_price,
    )

    return {
        "symbol": symbol.upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "prediction_id": prediction_id,
        "market": {
            "current_price": round(current_price, 2),
            "previous_close": round(previous_close, 2),
            "candles": candles,
            "news": news_payload.get("items", [])[:30] if isinstance(news_payload, dict) else [],
        },
        "agents": {
            "technical": technical,
            "sentiment": sentiment,
            "risk": risk,
            "synthesizer": synthesis,
        },
        "brain_log": build_brain_log(technical=technical, sentiment=sentiment, risk=risk, synthesis=synthesis),
    }
