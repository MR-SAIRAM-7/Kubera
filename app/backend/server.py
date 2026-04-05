from fastapi import FastAPI, APIRouter, HTTPException, Query, Request
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List
import uuid
from datetime import datetime, timezone

try:
    from .NSE.nseScraper import NSEScraper
    from .BSE.bseScraper import BSEScraper
    from .social.getNews import NewsScraper
    from .agents import build_dashboard_snapshot, ScenarioStore
except ImportError:
    # Fallback for running as a script from backend working directory.
    from NSE.nseScraper import NSEScraper
    from BSE.bseScraper import BSEScraper
    from social.getNews import NewsScraper
    from agents import build_dashboard_snapshot, ScenarioStore


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Configure logging early so startup warnings are visible.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# MongoDB connection (optional for scraper endpoints).
mongo_url = os.environ.get('MONGO_URL')
db_name = os.environ.get('DB_NAME')
client = None
db = None

if mongo_url and db_name:
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
else:
    logger.warning(
        "MongoDB is not configured. Set MONGO_URL and DB_NAME to enable /api/status routes."
    )

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")
scenario_store = ScenarioStore(max_samples_per_scenario=500)


# Define Models
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Ignore MongoDB's _id field
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str

# Add your routes to the router instead of directly to app
@api_router.get("/")
async def root():
    return {"message": "Hello World"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB is not configured")

    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    
    # Convert to dict and serialize datetime to ISO string for MongoDB
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    
    _ = await db.status_checks.insert_one(doc)
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB is not configured")

    # Exclude MongoDB's _id field from the query results
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    
    # Convert ISO string timestamps back to datetime objects
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    
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
async def get_nse_raw_api_data(request: Request, api_path: str = Query(..., description="NSE API path e.g. /api/allIndices")):
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


@api_router.get("/dashboard/{symbol}")
async def get_autonomous_dashboard(symbol: str, news_limit: int = Query(default=25, ge=5, le=100)):
    try:
        nse_scraper = NSEScraper()
        news_scraper = NewsScraper()

        market_data = nse_scraper.get_stock_and_market_overview(symbol=symbol, filings_limit=10)
        news_data = news_scraper.get_latest_related_news(stock_query=symbol, limit=news_limit)
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
            symbol=symbol,
            quote_payload=market_data,
            news_payload=news_data,
            scenario_store=scenario_store,
            nse_raw_fetcher=nse_live_candle_fetcher,
            lookback_points=240,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Dashboard synthesis failed for %s", symbol)
        raise HTTPException(
            status_code=502,
            detail="Dashboard synthesis failed. Live data source is unavailable for the requested symbol.",
        )


class PredictionOutcome(BaseModel):
    prediction_id: str
    exit_price: float


@api_router.post("/dashboard/predictions/resolve")
async def resolve_dashboard_prediction(payload: PredictionOutcome):
    result = scenario_store.resolve_prediction(
        prediction_id=payload.prediction_id,
        exit_price=payload.exit_price,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Prediction id not found")
    return result

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    if client is not None:
        client.close()
