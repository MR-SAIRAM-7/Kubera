import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware

try:
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic
except ImportError as exc:
    logger = logging.getLogger(__name__)
    logger.warning("aiokafka is unavailable; Kafka topic auto-provisioning disabled: %s", exc)
    AIOKafkaAdminClient = None
    NewTopic = None

try:
    from .NSE.nseScraper import NSEScraper, NSEScraperError
    from .BSE.bseScraper import BSEScraper
    from .social.getNews import NewsScraper, NewsScraperError
    from .agents import (
        PaperTradingEngine,
        ScenarioStore,
        VectorBacktestStore,
        build_dashboard_snapshot,
    )
except ImportError:
    from NSE.nseScraper import NSEScraper, NSEScraperError
    from BSE.bseScraper import BSEScraper
    from social.getNews import NewsScraper, NewsScraperError
    from agents import (
        PaperTradingEngine,
        ScenarioStore,
        VectorBacktestStore,
        build_dashboard_snapshot,
    )

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

mongo_url = os.environ.get("MONGO_URL")
db_name = os.environ.get("DB_NAME")
client = AsyncIOMotorClient(mongo_url) if mongo_url and db_name else None
db = client[db_name] if client and db_name else None

if db is None:
    logger.warning("MongoDB is not configured. Set MONGO_URL and DB_NAME to enable /api/status routes.")

app = FastAPI()
api_router = APIRouter(prefix="/api")

scenario_store = ScenarioStore(max_samples_per_scenario=500)
vector_store = VectorBacktestStore(max_items_per_symbol=3000)
paper_trader = PaperTradingEngine()
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9:._-]{1,64}$")
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC_PARTITIONS = int(os.environ.get("KAFKA_TOPIC_PARTITIONS", "3"))
KAFKA_TOPIC_REPLICATION_FACTOR = int(os.environ.get("KAFKA_TOPIC_REPLICATION_FACTOR", "1"))
WS_STREAM_INTERVAL = int(os.environ.get("WS_STREAM_INTERVAL_SECONDS", "5"))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_cors_origins(raw: str) -> List[str]:
    origins = []
    for origin in raw.split(","):
        stripped_origin = origin.strip()
        if stripped_origin:
            origins.append(stripped_origin)
    return origins or ["*"]


cors_origins = _parse_cors_origins(os.environ.get("CORS_ORIGINS", "*"))
cors_allow_credentials = _env_bool("CORS_ALLOW_CREDENTIALS", default=False)
if "*" in cors_origins and cors_allow_credentials:
    logger.warning("CORS_ALLOW_CREDENTIALS=true is incompatible with CORS_ORIGINS='*'. Falling back to false.")
    cors_allow_credentials = False


class TTLCache:
    def __init__(self, ttl_seconds: int = 45, max_items: int = 256):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._store: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str, allow_stale: bool = False) -> Optional[Dict[str, Any]]:
        item = self._store.get(key)
        if item is None:
            return None
        now = time.time()
        if item["expires_at"] > now:
            return {"value": item["value"], "stale": False}
        if allow_stale:
            return {"value": item["value"], "stale": True}
        self._store.pop(key, None)
        return None

    def set(self, key: str, value: Dict[str, Any]) -> None:
        if len(self._store) >= self.max_items:
            oldest_key = min(self._store, key=lambda entry: self._store[entry]["created_at"])
            self._store.pop(oldest_key, None)
        now = time.time()
        self._store[key] = {
            "value": value,
            "created_at": now,
            "expires_at": now + self.ttl_seconds,
        }


dashboard_cache = TTLCache(
    ttl_seconds=int(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", "45")),
    max_items=int(os.environ.get("DASHBOARD_CACHE_MAX_ITEMS", "256")),
)


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized or not SYMBOL_PATTERN.match(normalized):
        raise HTTPException(
            status_code=422,
            detail="Invalid symbol format. Use exchange-prefixed symbols like NSE:RELIANCE or plain ticker tokens.",
        )
    return normalized


class WebSocketHub:
    def __init__(self):
        self.connections: Dict[str, Set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, symbol: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.connections.setdefault(symbol, set()).add(websocket)

    async def disconnect(self, symbol: str, websocket: WebSocket) -> None:
        async with self.lock:
            conns = self.connections.get(symbol)
            if not conns:
                return
            conns.discard(websocket)
            if not conns:
                self.connections.pop(symbol, None)

    async def broadcast(self, symbol: str, payload: Dict[str, Any]) -> None:
        async with self.lock:
            conns = list(self.connections.get(symbol, set()))
        for ws in conns:
            try:
                if ws.client_state != WebSocketState.CONNECTED:
                    await self.disconnect(symbol, ws)
                    continue
                await ws.send_json(payload)
            except RuntimeError as exc:
                logger.warning("WebSocket send failed for %s: %s", symbol, exc)
                await self.disconnect(symbol, ws)


class AlertDispatcher:
    def __init__(self):
        self.telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        self.discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        self._last_signal_by_symbol: Dict[str, str] = {}

    @staticmethod
    def _build_text(symbol: str, snapshot: Dict[str, Any]) -> str:
        synth = snapshot.get("agents", {}).get("synthesizer", {})
        risk = snapshot.get("agents", {}).get("risk", {})
        return (
            f"{symbol} | {synth.get('signal')} | Prob={synth.get('win_probability')}% | "
            f"RR={risk.get('risk_reward_ratio')} | SL={risk.get('stop_loss')} | TP={risk.get('take_profit')}"
        )

    async def _post(self, url: str, payload: Dict[str, Any]) -> None:
        def _do_post() -> None:
            requests.post(url, json=payload, timeout=8)

        try:
            await asyncio.to_thread(_do_post)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Alert post failed: %s", exc)

    async def maybe_dispatch(self, symbol: str, snapshot: Dict[str, Any]) -> None:
        signal = snapshot.get("agents", {}).get("synthesizer", {}).get("signal")
        if signal not in {"BUY", "SELL"}:
            return
        previous = self._last_signal_by_symbol.get(symbol)
        if previous == signal:
            return
        self._last_signal_by_symbol[symbol] = signal
        text = self._build_text(symbol=symbol, snapshot=snapshot)

        if self.discord_webhook_url:
            await self._post(self.discord_webhook_url, {"content": text})

        if self.telegram_bot_token and self.telegram_chat_id:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            await self._post(url, {"chat_id": self.telegram_chat_id, "text": text})


class DynamicTickerRegistry:
    def __init__(self):
        self._symbols: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def ensure(self, symbol: str) -> Dict[str, Any]:
        async with self._lock:
            existing = self._symbols.get(symbol)
            if existing:
                return existing
            topic = f"ticks.{symbol.lower().replace(':', '-')}"
            kafka_created = await self._create_kafka_topic(topic=topic)
            data = {
                "symbol": symbol,
                "topic": topic,
                "workflow": f"langgraph-{symbol.lower().replace(':', '-')}",
                "kafka_topic_created": kafka_created,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._symbols[symbol] = data
            return data

    async def list(self) -> List[Dict[str, Any]]:
        async with self._lock:
            return list(self._symbols.values())

    async def _create_kafka_topic(self, topic: str) -> bool:
        if AIOKafkaAdminClient is None or NewTopic is None:
            return False
        admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP)
        try:
            await admin.start()
            await admin.create_topics(
                [
                    NewTopic(
                        name=topic,
                        num_partitions=max(1, KAFKA_TOPIC_PARTITIONS),
                        replication_factor=max(1, KAFKA_TOPIC_REPLICATION_FACTOR),
                    )
                ]
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kafka topic creation failed for %s: %s", topic, exc)
            return False
        finally:
            try:
                await admin.close()
            except Exception:  # noqa: BLE001
                pass


class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusCheckCreate(BaseModel):
    client_name: str


class PredictionOutcome(BaseModel):
    prediction_id: str
    exit_price: float


websocket_hub = WebSocketHub()
alert_dispatcher = AlertDispatcher()
ticker_registry = DynamicTickerRegistry()


async def _build_snapshot(symbol: str, news_limit: int, force_refresh: bool = False) -> Dict[str, Any]:
    normalized_symbol = _normalize_symbol(symbol)
    cache_key = f"{normalized_symbol}:{news_limit}"
    snapshot: Optional[Dict[str, Any]] = None
    live_source_error = False

    if not force_refresh:
        cached = dashboard_cache.get(cache_key)
        if cached is not None:
            payload = dict(cached["value"])
            payload["cache"] = {"hit": True, "stale": False}
            return payload

    def _sync_build() -> Dict[str, Any]:
        nse_scraper = NSEScraper()
        news_scraper = NewsScraper()
        market_data = nse_scraper.get_stock_and_market_overview(symbol=normalized_symbol, filings_limit=10)
        news_data = news_scraper.get_latest_related_news(stock_query=normalized_symbol, limit=news_limit)

        def nse_live_candle_fetcher(path: str, ticker: str):
            upper = ticker.upper().strip()
            if path == "/api/chart-databyindex":
                for candidate in (f"{upper}EQN", upper, f"NIFTY {upper}"):
                    try:
                        data = nse_scraper.get_any_data(path, params={"index": candidate})
                        if data:
                            return data
                    except Exception:
                        continue
                return None
            if path == "/api/chart-databysymbol":
                return nse_scraper.get_any_data(path, params={"symbol": upper})
            if path == "/api/historical/cm/equity":
                return nse_scraper.get_any_data(path, params={"symbol": upper, "series": ["EQ"]})
            return None

        return build_dashboard_snapshot(
            symbol=normalized_symbol,
            quote_payload=market_data,
            news_payload=news_data,
            scenario_store=scenario_store,
            vector_store=vector_store,
            paper_trader=paper_trader,
            nse_raw_fetcher=nse_live_candle_fetcher,
            lookback_points=240,
        )

    try:
        snapshot = await asyncio.to_thread(_sync_build)
    except (NSEScraperError, NewsScraperError, ValueError, RuntimeError):
        live_source_error = True
        logger.error("Dashboard synthesis failed for %s", normalized_symbol)

    if snapshot is not None:
        dashboard_cache.set(cache_key, snapshot)
        payload = dict(snapshot)
        payload["cache"] = {"hit": False, "stale": False}
        return payload

    if live_source_error:
        stale = dashboard_cache.get(cache_key, allow_stale=True)
        if stale is not None:
            payload = dict(stale["value"])
            payload["cache"] = {"hit": True, "stale": True}
            payload["warning"] = "Live source unavailable. Showing cached snapshot."
            return payload

    raise HTTPException(status_code=502, detail="Dashboard synthesis failed. Live data source is unavailable.")


@api_router.get("/")
async def root():
    return {"message": "Kubera backend online"}


@api_router.get("/health/live")
async def liveness():
    return {"status": "ok", "service": "kubera-backend"}


@api_router.get("/health/ready")
async def readiness():
    return {
        "status": "ok",
        "mongo_configured": bool(mongo_url and db_name),
        "cache_items": len(dashboard_cache._store),
        "active_subscriptions": len(await ticker_registry.list()),
    }


@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB is not configured")
    status_obj = StatusCheck(**input.model_dump())
    doc = status_obj.model_dump()
    doc["timestamp"] = doc["timestamp"].isoformat()
    await db.status_checks.insert_one(doc)
    return status_obj


@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB is not configured")
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    for check in status_checks:
        if isinstance(check["timestamp"], str):
            check["timestamp"] = datetime.fromisoformat(check["timestamp"])
    return status_checks


@api_router.get("/market/nse/{symbol}")
async def get_nse_stock_overview(symbol: str, filings_limit: int = Query(default=10, ge=0, le=50)):
    try:
        scraper = NSEScraper()
        return scraper.get_stock_and_market_overview(symbol=symbol, filings_limit=filings_limit)
    except Exception:  # noqa: BLE001
        logger.exception("NSE scraping failed for %s", symbol)
        raise HTTPException(status_code=502, detail="NSE scraping failed")


@api_router.get("/market/nse/raw")
async def get_nse_raw_api_data(request: Request, api_path: str = Query(...)):
    try:
        query_params = dict(request.query_params)
        query_params.pop("api_path", None)
        scraper = NSEScraper()
        return scraper.get_any_data(api_path=api_path, params=query_params or None)
    except Exception:  # noqa: BLE001
        logger.exception("NSE raw API scraping failed for path %s", api_path)
        raise HTTPException(status_code=502, detail="NSE raw API scraping failed")


@api_router.get("/market/bse/{stock_query}")
async def get_bse_stock_overview(stock_query: str):
    try:
        scraper = BSEScraper()
        return scraper.get_stock_and_market_overview(stock_query=stock_query)
    except Exception:  # noqa: BLE001
        logger.exception("BSE scraping failed for %s", stock_query)
        raise HTTPException(status_code=502, detail="BSE scraping failed")


@api_router.get("/news/{stock_query}")
async def get_stock_news(stock_query: str, limit: int = Query(default=20, ge=1, le=100)):
    try:
        scraper = NewsScraper()
        return scraper.get_latest_related_news(stock_query=stock_query, limit=limit)
    except Exception:  # noqa: BLE001
        logger.exception("News scraping failed for %s", stock_query)
        raise HTTPException(status_code=502, detail="News scraping failed")


@api_router.post("/subscriptions/{symbol}")
async def create_dynamic_subscription(symbol: str):
    normalized = _normalize_symbol(symbol)
    return await ticker_registry.ensure(normalized)


@api_router.get("/subscriptions")
async def list_dynamic_subscriptions():
    return {"subscriptions": await ticker_registry.list()}


@api_router.get("/dashboard/{symbol}")
async def get_autonomous_dashboard(
    symbol: str,
    news_limit: int = Query(default=25, ge=5, le=100),
    force_refresh: bool = Query(default=False),
):
    normalized = _normalize_symbol(symbol)
    await ticker_registry.ensure(normalized)
    payload = await _build_snapshot(symbol=normalized, news_limit=news_limit, force_refresh=force_refresh)
    await alert_dispatcher.maybe_dispatch(symbol=normalized, snapshot=payload)
    return payload


@api_router.get("/paper-trading/{symbol}")
async def get_paper_trading_summary(symbol: str):
    normalized = _normalize_symbol(symbol)
    return {"symbol": normalized, "paper_trading": paper_trader.get_summary(symbol=normalized)}


@api_router.post("/dashboard/predictions/resolve")
async def resolve_dashboard_prediction(payload: PredictionOutcome):
    result = scenario_store.resolve_prediction(prediction_id=payload.prediction_id, exit_price=payload.exit_price)
    if result is None:
        raise HTTPException(status_code=404, detail="Prediction id not found")
    return result


@app.websocket("/ws/dashboard/{symbol}")
async def dashboard_websocket(websocket: WebSocket, symbol: str, news_limit: int = 25):
    normalized = _normalize_symbol(symbol)
    await ticker_registry.ensure(normalized)
    await websocket_hub.connect(normalized, websocket)
    try:
        while True:
            payload = await _build_snapshot(symbol=normalized, news_limit=max(5, min(100, int(news_limit))), force_refresh=False)
            await alert_dispatcher.maybe_dispatch(symbol=normalized, snapshot=payload)
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(WS_STREAM_INTERVAL)
    except WebSocketDisconnect:
        await websocket_hub.disconnect(normalized, websocket)
    except Exception as exc:  # noqa: BLE001
        logger.exception("WebSocket stream failed for %s: %s", normalized, exc)
        await websocket_hub.disconnect(normalized, websocket)


app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=cors_allow_credentials,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    if client is not None:
        client.close()
