## Kubera Autonomous Market AI Dashboard

This app exposes a production-oriented multi-agent market dashboard pipeline for equities (for example `ADANIENT`).

### Architecture

- **Technical Analyst Agent**: derives trend/pattern context from live NSE candles and indicator set (SMA, RSI, MACD, support/resistance).
- **Sentiment Analyst Agent**: scores latest market news sentiment (`-100` to `+100`) and detects potential alpha events.
- **Risk Manager Agent**: computes volatility, VaR(95), stop-loss/take-profit, and risk-reward quality.
- **Chief Synthesizer Agent**: combines all agent outputs and computes final signal + mathematically grounded win probability from continuously learned real outcomes.

### API

#### `GET /api/dashboard/{symbol}`

Returns a full dashboard snapshot:

- market candles and latest related news
- individual agent outputs
- final signal + win probability + justification
- explainability brain log (agent-by-agent timeline)

Query params:

- `news_limit` (optional, default `25`, range `5-100`)

#### `POST /api/dashboard/predictions/resolve`

Persists live outcome feedback to continuously improve probability calibration.

Request body:

- `prediction_id` (from `GET /api/dashboard/{symbol}`)
- `exit_price` (real realized price)

### Environment

Frontend:

- `VITE_BACKEND_URL` (example: `http://localhost:8000`)

Backend:

- Optional for status persistence:
  - `MONGO_URL`
  - `DB_NAME`
- Optional CORS:
  - `CORS_ORIGINS` (comma-separated)

### Run

Backend:

```bash
cd app/backend
pip install -r requirements.txt
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

Frontend:

```bash
cd app/frontend
npm ci
npm run dev
```
