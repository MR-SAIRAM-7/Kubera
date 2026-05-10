-- Kubera NSE/BSE market AI schema. Raw data, features, agent outputs, decisions,
-- audit events, and execution state are intentionally separated for traceability.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    symbol TEXT NOT NULL,
    isin TEXT,
    company_name TEXT,
    sector TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    avg_traded_value NUMERIC NOT NULL DEFAULT 0,
    data_quality_score NUMERIC NOT NULL DEFAULT 1,
    source TEXT NOT NULL,
    captured_at_utc TIMESTAMPTZ NOT NULL,
    source_timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    ingestion_latency_ms INTEGER NOT NULL DEFAULT 0,
    freshness_confidence NUMERIC NOT NULL DEFAULT 1,
    UNIQUE (exchange, symbol)
);

CREATE TABLE IF NOT EXISTS raw_ohlcv (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ts_utc TIMESTAMPTZ NOT NULL,
    source_timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    ingestion_latency_ms INTEGER NOT NULL DEFAULT 0,
    freshness_confidence NUMERIC NOT NULL DEFAULT 1,
    stale BOOLEAN NOT NULL DEFAULT FALSE,
    incomplete BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (exchange, symbol, ts_utc)
);
SELECT create_hypertable('raw_ohlcv', 'ts_utc', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS corporate_actions (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action_type TEXT NOT NULL,
    ex_date_utc TIMESTAMPTZ NOT NULL,
    factor NUMERIC,
    cash_amount NUMERIC,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    source TEXT NOT NULL,
    captured_at_utc TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (exchange, symbol, action_type, ex_date_utc)
);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    feature_snapshot_id UUID PRIMARY KEY,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ts_utc TIMESTAMPTZ NOT NULL,
    feature_family TEXT NOT NULL,
    features JSONB NOT NULL,
    source_snapshot JSONB NOT NULL,
    data_quality JSONB NOT NULL DEFAULT '{}'::jsonb
);
SELECT create_hypertable('feature_snapshots', 'ts_utc', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS agent_outputs (
    agent_output_id UUID PRIMARY KEY,
    feature_snapshot_id UUID REFERENCES feature_snapshots(feature_snapshot_id),
    agent_name TEXT NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ts_utc TIMESTAMPTZ NOT NULL,
    score NUMERIC,
    confidence NUMERIC,
    output JSONB NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'rules-v1'
);
SELECT create_hypertable('agent_outputs', 'ts_utc', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS historical_setups (
    setup_id UUID PRIMARY KEY,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    sector TEXT,
    timeframe TEXT NOT NULL,
    regime_label TEXT NOT NULL,
    vector REAL[] NOT NULL,
    features JSONB NOT NULL,
    outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id UUID PRIMARY KEY,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ts_utc TIMESTAMPTZ NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL', 'HOLD', 'WATCHLIST')),
    score NUMERIC NOT NULL,
    probability NUMERIC NOT NULL,
    confidence NUMERIC NOT NULL,
    entry_zone JSONB NOT NULL,
    stop_loss NUMERIC,
    target NUMERIC,
    horizon TEXT NOT NULL,
    rationale JSONB NOT NULL,
    risk_veto BOOLEAN NOT NULL DEFAULT FALSE,
    risk_veto_reason TEXT,
    input_snapshot JSONB NOT NULL,
    strategy_version TEXT NOT NULL,
    model_version TEXT NOT NULL
);
SELECT create_hypertable('decisions', 'ts_utc', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS execution_audit (
    audit_id UUID PRIMARY KEY,
    decision_id UUID REFERENCES decisions(decision_id),
    requested_at_utc TIMESTAMPTZ NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('advisory', 'paper', 'live')),
    approved_by TEXT,
    kill_switch_enabled BOOLEAN NOT NULL,
    request JSONB NOT NULL,
    response JSONB NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL
);
