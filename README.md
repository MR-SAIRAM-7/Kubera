# Kubera — Autonomous Market AI Dashboard

Kubera is a multi-agent market intelligence dashboard for equities (for example `NSE: ADANIENT`) built with:

- **Backend:** FastAPI (Python)
- **Frontend:** React + Vite + Tailwind + Lightweight Charts
- **Core model:** Multi-agent synthesis (Technical + Sentiment + Risk + Chief Synthesizer)

It is designed to provide **explainable** trade signals, risk controls, and continuously calibrated probabilities.

---

## 1) Product Vision

Kubera acts like a virtual trading desk with specialized agents:

1. **Technical Analyst Agent**
   - Ingests candles and computes trend/pattern context
   - Uses SMA, RSI, MACD, support/resistance and momentum
2. **Sentiment Analyst Agent**
   - Aggregates and ranks market/news feed items
   - Scores sentiment in `[-100, +100]`
3. **Risk Manager Agent**
   - Computes volatility, VaR(95), SL/TP, risk:reward
   - Adds risk warning + suggested position sizing
4. **Chief Synthesizer Agent**
   - Combines all signals into BUY/SELL/HOLD
   - Produces probability + confidence band + justification
   - Uses empirical scenario outcomes for calibration

---

## 2) Repository Structure

```text
Kubera/
├── app/
│   ├── backend/
│   │   ├── server.py                # FastAPI routes and orchestration
│   │   ├── agents.py                # Multi-agent logic and scenario store
│   │   ├── NSE/nseScraper.py        # NSE market data client
│   │   ├── BSE/bseScraper.py        # BSE market data client
│   │   └── social/getNews.py        # News/sentiment feed collector
│   ├── frontend/
│   │   ├── src/App.jsx              # Dashboard UI
│   │   └── package.json
│   └── README.md
└── README.md
```

---

## 3) Production-Ready Features Included

- Multi-agent signal architecture with explainability (`brain_log`)
- Empirical scenario memory with win-rate calibration
- Dynamic risk controls (VaR, SL/TP, RR, position size guidance)
- Resilient market/news scraping with retries and fallbacks
- Backend **health endpoints** for liveness/readiness
- Dashboard response cache with stale fallback during upstream outages
- Symbol validation and safer API input handling
- Real-time UI refresh controls and manual refresh
- Chart overlays for support/resistance + SL/TP guides

---

## 4) Quick Start

### 4.1 Prerequisites

- Python 3.11+
- Node.js 20+
- npm 10+

### 4.2 Backend

```bash
cd app/backend
python -m pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

### 4.3 Frontend

```bash
cd app/frontend
npm ci
npm run dev
```

Open: `http://localhost:5173`

---

## 5) Environment Variables

### Backend

- `MONGO_URL` (optional)
- `DB_NAME` (optional)
- `CORS_ORIGINS` (optional, comma-separated; default `*`)
- `DASHBOARD_CACHE_TTL_SECONDS` (optional, default `45`)
- `DASHBOARD_CACHE_MAX_ITEMS` (optional, default `256`)

### Frontend

- `VITE_BACKEND_URL` (optional, default `http://localhost:8000`)

---

## 6) API Reference

Base URL: `http://localhost:8000/api`

### Health

- `GET /health/live`
- `GET /health/ready`

### Dashboard

- `GET /dashboard/{symbol}`
  - Query:
    - `news_limit` (`5..100`, default `25`)
    - `force_refresh` (`true|false`, default `false`)
  - Returns:
    - market candles/news
    - all agent outputs
    - final signal + win probability + justification
    - brain panel logs
    - cache metadata

- `POST /dashboard/predictions/resolve`
  - Body:
    ```json
    {
      "prediction_id": "p_1",
      "exit_price": 3120.5
    }
    ```

### Data Endpoints

- `GET /market/nse/{symbol}`
- `GET /market/nse/raw?api_path=/api/allIndices`
- `GET /market/bse/{stock_query}`
- `GET /news/{stock_query}?limit=20`

---

## 7) Frontend UX Panels

- **Signal cards:** Signal, Probability, Sentiment, RR, Consensus
- **Price Action chart:** live candles + support/resistance + SL/TP guides
- **Brain Panel:** real-time agent reasoning timeline
- **Backtesting Reflection:** matched historical scenarios + wins
- **Risk Controls:** SL, TP, VaR, position size, risk warning

---

## 8) Operational Notes

- This system is a **decision-support tool**, not financial advice.
- Upstream public data feeds may throttle/block traffic; stale cache fallback is enabled.
- For institutional scale, plan migration to:
  - stream bus (Kafka/Redpanda),
  - time-series DB (TimescaleDB),
  - vector store (Milvus/Pinecone),
  - job orchestration and persistent backtesting services.

---

## 9) Developer Commands

Frontend:

```bash
cd app/frontend
npm run lint
npm run build
```

Backend:

```bash
cd app/backend
python -m compileall .
```

---

## 10) Recommended Next Enhancements

1. Persistent ScenarioStore (Redis/Postgres) for multi-instance deployments
2. WebSocket/SSE push for sub-minute event updates
3. Full audit trail for every signal decision
4. Strategy sandbox + paper-trade ledger
5. Alerting integrations (Telegram/Slack/Email)
6. Portfolio-level risk netting and exposure guardrails

---

## Disclaimer

Kubera is for research and educational use. Markets are risky and uncertain. Always validate independently before live deployment.
