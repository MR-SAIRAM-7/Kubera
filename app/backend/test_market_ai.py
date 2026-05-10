from __future__ import annotations

from app.backend.market_ai import ExecutionAgent, InMemoryAuditEventBus, OrderRequest, PortfolioAgent, RegimeAgent, UniverseAgent, TradableInstrument


def test_universe_filters_illiquid_and_inactive_symbols():
    agent = UniverseAgent(min_avg_traded_value=1000, min_data_quality_score=0.7)
    result = agent.scan(
        [
            TradableInstrument(symbol="NSE:GOOD", exchange="NSE", avg_traded_value=5000, data_quality_score=0.95),
            TradableInstrument(symbol="BSE:BAD", exchange="BSE", avg_traded_value=50, data_quality_score=0.95),
            TradableInstrument(symbol="NSE:HALT", exchange="NSE", status="SUSPENDED", avg_traded_value=5000),
        ]
    )
    assert result["accepted_count"] == 1
    assert result["rejected_count"] == 2
    assert result["rejected"][0]["reasons"] == ["ILLIQUID"]


def test_execution_blocks_live_order_without_approval_and_logs_audit_event():
    bus = InMemoryAuditEventBus()
    agent = ExecutionAgent(event_bus=bus, mode="live", kill_switch_enabled=False)
    response = agent.submit(
        order=OrderRequest(
            symbol="NSE:RELIANCE",
            side="BUY",
            quantity=1,
            order_type="MARKET",
            price=None,
            approved_by=None,
            strategy_version="test",
            model_version="test",
            input_snapshot={},
        ),
        risk={"veto": False},
        portfolio={"veto": False},
    )
    assert response["status"] == "BLOCKED"
    assert response["reason"] == "HUMAN_APPROVAL_REQUIRED"
    assert bus.latest(1)[0]["topic"] == "execution.order_request"


def test_regime_agent_blocks_insufficient_data():
    regime = RegimeAgent().classify("NSE:SHORT", [{"close": 100.0}])
    assert regime["label"] == "insufficient-data"
    assert regime["passes_trading_filter"] is False


def test_portfolio_guardrail_blocks_concentration():
    portfolio = PortfolioAgent(max_single_stock_pct=0.01)
    result = portfolio.plan(
        cash=100000,
        positions=[],
        candidate={"symbol": "NSE:RELIANCE"},
        risk={"position_size_pct": 2.0},
        sector="Energy",
    )
    assert result["veto"] is True
    assert "MAX_SINGLE_STOCK_EXPOSURE" in result["veto_reasons"]
