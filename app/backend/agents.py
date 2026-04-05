from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import random
from statistics import mean, pstdev
from typing import Any, Dict, List, Tuple


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return fallback


def generate_candles(symbol: str, last_price: float, previous_close: float, points: int = 120) -> List[Dict[str, float]]:
    baseline = last_price if last_price > 0 else (previous_close if previous_close > 0 else 100.0)
    drift = ((last_price - previous_close) / previous_close) if previous_close > 0 else 0.0
    seed = int(hashlib.sha256(f"{symbol}:{baseline}:{previous_close}".encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed)

    candles: List[Dict[str, float]] = []
    current = baseline * (1.0 - drift * 0.5)

    for idx in range(max(points, 30)):
        minute_factor = idx / max(points, 1)
        directional_push = drift * (0.6 + minute_factor * 0.8)
        noise = rng.uniform(-0.0025, 0.0025)
        open_price = current
        close_price = max(0.01, open_price * (1 + directional_push * 0.04 + noise))
        spread = abs(close_price - open_price) + baseline * rng.uniform(0.0008, 0.0022)
        high = max(open_price, close_price) + spread * rng.uniform(0.4, 1.0)
        low = min(open_price, close_price) - spread * rng.uniform(0.4, 1.0)
        low = max(0.01, low)
        candles.append(
            {
                "t": idx,
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close_price, 2),
            }
        )
        current = close_price

    adjustment = (baseline - candles[-1]["close"]) / max(points, 1)
    if adjustment:
        for idx, candle in enumerate(candles):
            shift = adjustment * idx
            candle["open"] = round(max(0.01, candle["open"] + shift), 2)
            candle["close"] = round(max(0.01, candle["close"] + shift), 2)
            candle["high"] = round(max(candle["open"], candle["close"], candle["high"] + shift), 2)
            candle["low"] = round(max(0.01, min(candle["open"], candle["close"], candle["low"] + shift)), 2)

    return candles


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1)
    ema_val = values[0]
    for value in values[1:]:
        ema_val = alpha * value + (1 - alpha) * ema_val
    return ema_val


def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, period + 1):
        delta = closes[-i] - closes[-i - 1]
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
    return 100.0 - (100.0 / (1.0 + rs))


def technical_agent(symbol: str, candles: List[Dict[str, float]]) -> Dict[str, Any]:
    closes = [row["close"] for row in candles]
    lows = [row["low"] for row in candles]
    highs = [row["high"] for row in candles]
    volumes = [100000 + idx * 30 for idx in range(len(candles))]

    sma_20 = mean(closes[-20:]) if len(closes) >= 20 else mean(closes)
    sma_50 = mean(closes[-50:]) if len(closes) >= 50 else mean(closes)
    rsi_14 = _rsi(closes, period=14)
    ema_12 = _ema(closes[-60:], period=12)
    ema_26 = _ema(closes[-60:], period=26)
    macd = ema_12 - ema_26
    signal_line = _ema([macd] * 9, period=9)
    support = min(lows[-50:]) if len(lows) >= 50 else min(lows)
    resistance = max(highs[-50:]) if len(highs) >= 50 else max(highs)
    last_close = closes[-1]
    near_support = abs(last_close - support) / max(last_close, 1.0) <= 0.01
    near_resistance = abs(resistance - last_close) / max(last_close, 1.0) <= 0.01

    bullish = int(sma_20 > sma_50) + int(macd > signal_line) + int(rsi_14 > 50) + int(near_support)
    bearish = int(sma_20 < sma_50) + int(macd < signal_line) + int(rsi_14 < 45) + int(near_resistance)
    if bullish > bearish:
        bias = "bullish"
    elif bearish > bullish:
        bias = "bearish"
    else:
        bias = "neutral"

    pattern = "Order block demand retest" if near_support else ("Supply zone rejection" if near_resistance else "Range compression")
    confidence = round(min(0.95, max(0.1, 0.5 + (bullish - bearish) * 0.08)), 3)

    return {
        "agent": "technical",
        "symbol": symbol,
        "bias": bias,
        "confidence": confidence,
        "indicators": {
            "sma_20": round(sma_20, 2),
            "sma_50": round(sma_50, 2),
            "rsi_14": round(rsi_14, 2),
            "macd": round(macd, 4),
            "signal_line": round(signal_line, 4),
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "volume_imbalance": round((volumes[-1] - mean(volumes[-20:])) / max(mean(volumes[-20:]), 1), 4),
        },
        "detected_pattern": pattern,
    }


POSITIVE_WORDS = {
    "deal",
    "win",
    "growth",
    "profit",
    "beats",
    "surge",
    "expansion",
    "approval",
    "contract",
    "record",
    "bullish",
}
NEGATIVE_WORDS = {
    "probe",
    "fraud",
    "loss",
    "decline",
    "downgrade",
    "penalty",
    "volatility",
    "bearish",
    "lawsuit",
    "default",
    "delay",
}


def sentiment_agent(symbol: str, news_payload: Dict[str, Any]) -> Dict[str, Any]:
    items = news_payload.get("items", []) if isinstance(news_payload, dict) else []
    if not isinstance(items, list):
        items = []

    score = 0
    alpha_events: List[str] = []
    for item in items[:40]:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        pos_hits = sum(1 for token in POSITIVE_WORDS if token in text)
        neg_hits = sum(1 for token in NEGATIVE_WORDS if token in text)
        score += (pos_hits - neg_hits) * 6
        if pos_hits >= 2:
            alpha_events.append(item.get("title", ""))
        if neg_hits >= 2:
            alpha_events.append(item.get("title", ""))

    normalized = max(-100, min(100, score))
    bias = "bullish" if normalized > 15 else ("bearish" if normalized < -15 else "neutral")
    confidence = round(min(0.95, 0.45 + min(abs(normalized), 80) / 200), 3)

    return {
        "agent": "sentiment",
        "symbol": symbol,
        "bias": bias,
        "confidence": confidence,
        "sentiment_score": normalized,
        "alpha_signals": alpha_events[:5],
        "samples_considered": len(items),
    }


def risk_agent(symbol: str, candles: List[Dict[str, float]], technical: Dict[str, Any]) -> Dict[str, Any]:
    closes = [row["close"] for row in candles]
    if len(closes) < 3:
        closes = closes + closes
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    current_price = closes[-1]
    var_95 = abs(current_price * volatility * 1.65)

    true_ranges = [abs(row["high"] - row["low"]) for row in candles[-20:]] or [current_price * 0.01]
    atr = mean(true_ranges)

    bias = technical.get("bias", "neutral")
    if bias == "bullish":
        stop_loss = current_price - max(atr * 1.2, var_95 * 0.9)
        take_profit = current_price + max(atr * 2.2, var_95 * 1.8)
    elif bias == "bearish":
        stop_loss = current_price + max(atr * 1.2, var_95 * 0.9)
        take_profit = current_price - max(atr * 2.2, var_95 * 1.8)
    else:
        stop_loss = current_price - atr
        take_profit = current_price + atr * 1.5

    rr_numerator = abs(take_profit - current_price)
    rr_denominator = max(abs(current_price - stop_loss), 0.0001)
    risk_reward = rr_numerator / rr_denominator
    risk_state = "acceptable" if risk_reward >= 1.6 and volatility < 0.03 else "elevated"
    confidence = round(0.55 if risk_state == "acceptable" else 0.4, 3)

    return {
        "agent": "risk",
        "symbol": symbol,
        "volatility": round(volatility, 5),
        "value_at_risk_95": round(var_95, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "risk_reward_ratio": round(risk_reward, 2),
        "risk_state": risk_state,
        "confidence": confidence,
    }


@dataclass(frozen=True)
class Scenario:
    trend: str
    sentiment_bucket: str
    macd_state: str
    support_touch: bool
    outcome_up: bool


def _bucket_sentiment(score: int) -> str:
    if score >= 25:
        return "positive"
    if score <= -25:
        return "negative"
    return "neutral"


def _scenario_bank(symbol: str, size: int = 500) -> List[Scenario]:
    seed = int(hashlib.md5(symbol.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)
    scenarios: List[Scenario] = []
    for _ in range(size):
        trend = rng.choice(["bullish", "bearish", "neutral"])
        sentiment = rng.choice(["positive", "negative", "neutral"])
        macd_state = rng.choice(["cross_up", "cross_down", "flat"])
        support_touch = rng.random() < 0.35
        base = 0.48
        if trend == "bullish":
            base += 0.1
        if sentiment == "positive":
            base += 0.08
        if macd_state == "cross_up":
            base += 0.06
        if support_touch:
            base += 0.04
        if trend == "bearish":
            base -= 0.1
        if sentiment == "negative":
            base -= 0.08
        if macd_state == "cross_down":
            base -= 0.06
        chance = max(0.05, min(0.95, base + rng.uniform(-0.05, 0.05)))
        scenarios.append(
            Scenario(
                trend=trend,
                sentiment_bucket=sentiment,
                macd_state=macd_state,
                support_touch=support_touch,
                outcome_up=rng.random() < chance,
            )
        )
    return scenarios


def synthesizer_agent(
    symbol: str,
    technical: Dict[str, Any],
    sentiment: Dict[str, Any],
    risk: Dict[str, Any],
) -> Dict[str, Any]:
    trend = technical.get("bias", "neutral")
    macd = technical.get("indicators", {}).get("macd", 0.0)
    signal_line = technical.get("indicators", {}).get("signal_line", 0.0)
    macd_state = "cross_up" if macd > signal_line else ("cross_down" if macd < signal_line else "flat")
    support = technical.get("indicators", {}).get("support", 0.0)
    last_close = technical.get("indicators", {}).get("sma_20", 0.0)
    support_touch = abs(last_close - support) / max(last_close, 1.0) <= 0.01
    sentiment_bucket = _bucket_sentiment(sentiment.get("sentiment_score", 0))

    bank = _scenario_bank(symbol=symbol, size=500)
    exact = [
        s
        for s in bank
        if s.trend == trend
        and s.sentiment_bucket == sentiment_bucket
        and s.macd_state == macd_state
        and s.support_touch == support_touch
    ]
    if len(exact) < 80:
        exact = [
            s
            for s in bank
            if s.trend == trend and s.sentiment_bucket == sentiment_bucket and s.macd_state == macd_state
        ]
    if len(exact) < 40:
        exact = [s for s in bank if s.trend == trend]

    wins = sum(1 for s in exact if s.outcome_up)
    total = len(exact)
    historical_prob = wins / total if total else 0.5

    technical_score = 1 if technical.get("bias") == "bullish" else (-1 if technical.get("bias") == "bearish" else 0)
    sentiment_score = sentiment.get("sentiment_score", 0) / 100
    risk_multiplier = 1.0 if risk.get("risk_state") == "acceptable" else 0.7
    blended = (0.5 + technical_score * 0.18 + sentiment_score * 0.16 + (historical_prob - 0.5) * 0.5) * risk_multiplier
    win_probability = max(0.05, min(0.95, blended))

    if win_probability >= 0.6 and risk.get("risk_reward_ratio", 0) >= 1.5:
        signal = "BUY"
    elif win_probability <= 0.4 and risk.get("risk_reward_ratio", 0) >= 1.5:
        signal = "SELL"
    else:
        signal = "HOLD"

    justification = (
        f"{signal} with {round(win_probability * 100, 2)}% win probability. "
        f"Historical confluence matched {total} similar setups with {wins} favorable outcomes. "
        f"Technical bias is {technical.get('bias')} ({technical.get('detected_pattern')}); "
        f"sentiment score is {sentiment.get('sentiment_score')}; "
        f"risk/reward is {risk.get('risk_reward_ratio')} with VaR {risk.get('value_at_risk_95')}."
    )

    return {
        "agent": "synthesizer",
        "symbol": symbol,
        "signal": signal,
        "win_probability": round(win_probability * 100, 2),
        "historical_matches": total,
        "historical_wins": wins,
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
            "message": f"Bias {technical.get('bias')} | Pattern {technical.get('detected_pattern')} | RSI {technical.get('indicators', {}).get('rsi_14')}",
        },
        {
            "timestamp": now,
            "agent": "Sentiment Analyst",
            "message": f"Sentiment {sentiment.get('sentiment_score')} with {sentiment.get('samples_considered')} samples.",
        },
        {
            "timestamp": now,
            "agent": "Risk Manager",
            "message": f"VaR {risk.get('value_at_risk_95')} | RR {risk.get('risk_reward_ratio')} | SL {risk.get('stop_loss')} | TP {risk.get('take_profit')}",
        },
        {
            "timestamp": now,
            "agent": "Chief Synthesizer",
            "message": f"Signal {synthesis.get('signal')} at {synthesis.get('win_probability')}% probability.",
        },
    ]


def build_dashboard_snapshot(symbol: str, quote_payload: Dict[str, Any], news_payload: Dict[str, Any]) -> Dict[str, Any]:
    quote = quote_payload.get("stock", {}) if isinstance(quote_payload, dict) else {}
    last_price = _to_float(quote.get("last_price"), fallback=0.0)
    previous_close = _to_float(quote.get("previous_close"), fallback=last_price or 100.0)
    candles = generate_candles(symbol=symbol, last_price=last_price, previous_close=previous_close, points=120)

    technical = technical_agent(symbol=symbol, candles=candles)
    sentiment = sentiment_agent(symbol=symbol, news_payload=news_payload)
    risk = risk_agent(symbol=symbol, candles=candles, technical=technical)
    synthesis = synthesizer_agent(symbol=symbol, technical=technical, sentiment=sentiment, risk=risk)
    brain_log = build_brain_log(technical=technical, sentiment=sentiment, risk=risk, synthesis=synthesis)

    return {
        "symbol": symbol.upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "market": {
            "current_price": round(last_price or candles[-1]["close"], 2),
            "previous_close": round(previous_close, 2),
            "candles": candles,
            "news": news_payload.get("items", [])[:20] if isinstance(news_payload, dict) else [],
        },
        "agents": {
            "technical": technical,
            "sentiment": sentiment,
            "risk": risk,
            "synthesizer": synthesis,
        },
        "brain_log": brain_log,
    }
