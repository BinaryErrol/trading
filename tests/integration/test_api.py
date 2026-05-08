"""Integration tests for the Dashboard API.

Tests REST endpoint responses, WebSocket connections, and authentication
middleware using FastAPI's TestClient and httpx async client.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.dashboard.api import (
    create_app,
    set_db_session_factory,
    set_portfolio_monitor,
)
from src.dashboard.websocket import set_ws_portfolio_monitor
from src.portfolio.monitor import PortfolioMonitor, Position, Trade


@pytest.fixture
def portfolio_monitor() -> PortfolioMonitor:
    """Create a PortfolioMonitor with sample data for testing."""
    monitor = PortfolioMonitor(connection=None, initial_equity=Decimal("100000"))

    # Add sample positions
    monitor._positions = {
        "AAPL": Position(
            symbol="AAPL",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("155.00"),
            unrealized_pnl=Decimal("500.00"),
            realized_pnl=Decimal("200.00"),
            opened_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
        ),
        "MSFT": Position(
            symbol="MSFT",
            asset_class="STK",
            strategy_name="mean_reversion",
            quantity=Decimal("50"),
            avg_entry_price=Decimal("380.00"),
            current_price=Decimal("375.00"),
            unrealized_pnl=Decimal("-250.00"),
            realized_pnl=Decimal("100.00"),
            opened_at=datetime(2024, 1, 20, 14, 30, 0, tzinfo=UTC),
        ),
    }

    # Add sample trades for strategy metrics
    monitor._trades = [
        Trade(
            strategy_name="momentum",
            symbol="AAPL",
            pnl=Decimal("500"),
            return_pct=0.05,
            closed_at=datetime(2024, 1, 10, 16, 0, 0, tzinfo=UTC),
        ),
        Trade(
            strategy_name="momentum",
            symbol="AAPL",
            pnl=Decimal("-200"),
            return_pct=-0.02,
            closed_at=datetime(2024, 1, 12, 16, 0, 0, tzinfo=UTC),
        ),
        Trade(
            strategy_name="momentum",
            symbol="MSFT",
            pnl=Decimal("300"),
            return_pct=0.03,
            closed_at=datetime(2024, 1, 14, 16, 0, 0, tzinfo=UTC),
        ),
    ]

    return monitor


@pytest.fixture
def app(portfolio_monitor: PortfolioMonitor) -> TestClient:
    """Create a test client with the dashboard app."""
    # Reset module-level state
    set_portfolio_monitor(portfolio_monitor)
    set_ws_portfolio_monitor(portfolio_monitor)
    set_db_session_factory(None)

    application = create_app(
        portfolio_monitor=portfolio_monitor,
        db_session_factory=None,
        cors_origins=["http://localhost:3000"],
    )
    return TestClient(application)


@pytest.fixture
def auth_app(portfolio_monitor: PortfolioMonitor) -> TestClient:
    """Create a test client with auth token configured."""
    set_portfolio_monitor(portfolio_monitor)
    set_ws_portfolio_monitor(portfolio_monitor)
    set_db_session_factory(None)

    application = create_app(
        portfolio_monitor=portfolio_monitor,
        db_session_factory=None,
    )
    return TestClient(application)


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200(self, app: TestClient) -> None:
        """Health endpoint returns 200 with status and version."""
        response = app.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"

    def test_health_no_auth_required(self, auth_app: TestClient) -> None:
        """Health endpoint does not require authentication even when token is set."""
        with patch.dict(os.environ, {"DASHBOARD_AUTH_TOKEN": "secret-token"}):
            response = auth_app.get("/health")
            assert response.status_code == 200


class TestPortfolioEndpoint:
    """Tests for GET /api/portfolio."""

    def test_get_portfolio_summary(self, app: TestClient) -> None:
        """Portfolio endpoint returns summary with value, P&L, drawdown."""
        response = app.get("/api/portfolio")
        assert response.status_code == 200
        data = response.json()
        assert "total_value" in data
        assert "unrealized_pnl" in data
        assert "peak_equity" in data
        assert "drawdown_pct" in data

    def test_portfolio_values_are_numeric(self, app: TestClient) -> None:
        """All portfolio values are numeric (float)."""
        response = app.get("/api/portfolio")
        data = response.json()
        assert isinstance(data["total_value"], (int, float))
        assert isinstance(data["unrealized_pnl"], (int, float))
        assert isinstance(data["peak_equity"], (int, float))
        assert isinstance(data["drawdown_pct"], (int, float))


class TestPositionsEndpoint:
    """Tests for GET /api/positions."""

    def test_get_positions_returns_list(self, app: TestClient) -> None:
        """Positions endpoint returns a list of positions."""
        response = app.get("/api/positions")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_position_fields(self, app: TestClient) -> None:
        """Each position has all required fields."""
        response = app.get("/api/positions")
        data = response.json()
        position = data[0]
        required_fields = [
            "symbol",
            "asset_class",
            "strategy_name",
            "quantity",
            "avg_entry_price",
            "current_price",
            "unrealized_pnl",
            "realized_pnl",
        ]
        for field in required_fields:
            assert field in position, f"Missing field: {field}"

    def test_position_values(self, app: TestClient) -> None:
        """Position values match the test data."""
        response = app.get("/api/positions")
        data = response.json()
        # Find AAPL position
        aapl = next(p for p in data if p["symbol"] == "AAPL")
        assert aapl["quantity"] == 100.0
        assert aapl["avg_entry_price"] == 150.0
        assert aapl["current_price"] == 155.0
        assert aapl["unrealized_pnl"] == 500.0


class TestStrategiesEndpoint:
    """Tests for GET /api/strategies."""

    def test_get_strategies_returns_list(self, app: TestClient) -> None:
        """Strategies endpoint returns a list of strategy metrics."""
        response = app.get("/api/strategies")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1  # Only "momentum" has trades

    def test_strategy_fields(self, app: TestClient) -> None:
        """Each strategy has all required metric fields."""
        response = app.get("/api/strategies")
        data = response.json()
        strategy = data[0]
        assert strategy["name"] == "momentum"
        assert "total_return" in strategy
        assert "sharpe_ratio" in strategy
        assert "sortino_ratio" in strategy
        assert "max_drawdown" in strategy
        assert "win_rate" in strategy
        assert "profit_factor" in strategy
        assert "total_trades" in strategy
        assert strategy["total_trades"] == 3


class TestPerformanceEndpoint:
    """Tests for GET /api/performance/{strategy}."""

    def test_get_performance_existing_strategy(self, app: TestClient) -> None:
        """Performance endpoint returns metrics for a strategy with trades."""
        response = app.get("/api/performance/momentum")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "momentum"
        assert data["total_trades"] == 3

    def test_get_performance_nonexistent_strategy(self, app: TestClient) -> None:
        """Performance endpoint returns 404 for strategy with no trades."""
        response = app.get("/api/performance/nonexistent")
        assert response.status_code == 404


class TestRiskEndpoint:
    """Tests for GET /api/risk."""

    def test_get_risk_metrics(self, app: TestClient) -> None:
        """Risk endpoint returns current risk metrics."""
        response = app.get("/api/risk")
        assert response.status_code == 200
        data = response.json()
        assert "portfolio_value" in data
        assert "peak_equity" in data
        assert "drawdown_pct" in data
        assert "unrealized_pnl" in data
        assert "position_count" in data
        assert data["position_count"] == 2


class TestOrdersEndpoint:
    """Tests for GET /api/orders."""

    def test_orders_returns_503_without_db(self, app: TestClient) -> None:
        """Orders endpoint returns 503 when database is not initialized."""
        response = app.get("/api/orders")
        assert response.status_code == 503


class TestExportEndpoint:
    """Tests for GET /api/export/csv."""

    def test_export_csv_default_dates(self, app: TestClient) -> None:
        """Export endpoint works with default date range."""
        response = app.get("/api/export/csv")
        assert response.status_code == 200
        data = response.json()
        assert "filepath" in data
        assert "trade_count" in data

    def test_export_csv_custom_dates(self, app: TestClient) -> None:
        """Export endpoint works with custom date range."""
        response = app.get("/api/export/csv?start=2024-01-01&end=2024-01-31")
        assert response.status_code == 200
        data = response.json()
        assert data["trade_count"] == 3  # All trades are in January 2024


class TestAuthentication:
    """Tests for authentication middleware."""

    def test_no_auth_when_token_not_set(self, app: TestClient) -> None:
        """Requests succeed without auth when DASHBOARD_AUTH_TOKEN is not set."""
        # Ensure env var is not set
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DASHBOARD_AUTH_TOKEN", None)
            response = app.get("/api/portfolio")
            assert response.status_code == 200

    def test_auth_required_when_token_set(self, auth_app: TestClient) -> None:
        """Requests fail without auth header when token is configured."""
        with patch.dict(os.environ, {"DASHBOARD_AUTH_TOKEN": "secret-token"}):
            response = auth_app.get("/api/portfolio")
            assert response.status_code == 401

    def test_auth_succeeds_with_valid_token(self, auth_app: TestClient) -> None:
        """Requests succeed with valid Bearer token."""
        with patch.dict(os.environ, {"DASHBOARD_AUTH_TOKEN": "secret-token"}):
            response = auth_app.get(
                "/api/portfolio",
                headers={"Authorization": "Bearer secret-token"},
            )
            assert response.status_code == 200

    def test_auth_fails_with_invalid_token(self, auth_app: TestClient) -> None:
        """Requests fail with invalid Bearer token."""
        with patch.dict(os.environ, {"DASHBOARD_AUTH_TOKEN": "secret-token"}):
            response = auth_app.get(
                "/api/portfolio",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert response.status_code == 403

    def test_auth_fails_with_malformed_header(self, auth_app: TestClient) -> None:
        """Requests fail with malformed Authorization header."""
        with patch.dict(os.environ, {"DASHBOARD_AUTH_TOKEN": "secret-token"}):
            response = auth_app.get(
                "/api/portfolio",
                headers={"Authorization": "Basic secret-token"},
            )
            assert response.status_code == 401


class TestWebSocket:
    """Tests for WebSocket /ws/live endpoint."""

    def test_websocket_connects(self, app: TestClient) -> None:
        """WebSocket connection is accepted."""
        with app.websocket_connect("/ws/live") as ws:
            # Should receive a portfolio update within the streaming interval
            data = ws.receive_text()
            message = json.loads(data)
            assert message["type"] == "portfolio_update"
            assert "data" in message

    def test_websocket_portfolio_update_fields(self, app: TestClient) -> None:
        """WebSocket portfolio update contains expected fields."""
        with app.websocket_connect("/ws/live") as ws:
            data = ws.receive_text()
            message = json.loads(data)
            portfolio_data = message["data"]
            assert "total_value" in portfolio_data
            assert "unrealized_pnl" in portfolio_data
            assert "positions" in portfolio_data
            assert "position_count" in portfolio_data

    def test_websocket_get_positions_action(self, app: TestClient) -> None:
        """WebSocket responds to get_positions action."""
        with app.websocket_connect("/ws/live") as ws:
            # Consume the initial streaming update
            ws.receive_text()
            # Send action request
            ws.send_text(json.dumps({"action": "get_positions"}))
            data = ws.receive_text()
            message = json.loads(data)
            assert message["type"] == "positions"
            assert isinstance(message["data"], list)

    def test_websocket_get_portfolio_action(self, app: TestClient) -> None:
        """WebSocket responds to get_portfolio action."""
        with app.websocket_connect("/ws/live") as ws:
            # Consume the initial streaming update
            ws.receive_text()
            # Send action request
            ws.send_text(json.dumps({"action": "get_portfolio"}))
            data = ws.receive_text()
            message = json.loads(data)
            assert message["type"] == "portfolio_update"

    def test_websocket_invalid_json(self, app: TestClient) -> None:
        """WebSocket handles invalid JSON gracefully."""
        with app.websocket_connect("/ws/live") as ws:
            # Consume the initial streaming update
            ws.receive_text()
            ws.send_text("not valid json")
            data = ws.receive_text()
            message = json.loads(data)
            assert message["type"] == "error"
            assert "Invalid JSON" in message["message"]

    def test_websocket_unknown_action(self, app: TestClient) -> None:
        """WebSocket handles unknown actions gracefully."""
        with app.websocket_connect("/ws/live") as ws:
            # Consume the initial streaming update
            ws.receive_text()
            ws.send_text(json.dumps({"action": "unknown_action"}))
            data = ws.receive_text()
            message = json.loads(data)
            assert message["type"] == "error"

    def test_websocket_auth_rejected_with_invalid_token(
        self, auth_app: TestClient
    ) -> None:
        """WebSocket connection is rejected with invalid auth token."""
        with patch.dict(os.environ, {"DASHBOARD_AUTH_TOKEN": "secret-token"}):
            with pytest.raises(Exception):
                with auth_app.websocket_connect("/ws/live?token=wrong") as ws:
                    ws.receive_text()

    def test_websocket_auth_accepted_with_valid_token(
        self, auth_app: TestClient
    ) -> None:
        """WebSocket connection is accepted with valid auth token."""
        with patch.dict(os.environ, {"DASHBOARD_AUTH_TOKEN": "secret-token"}):
            with auth_app.websocket_connect("/ws/live?token=secret-token") as ws:
                data = ws.receive_text()
                message = json.loads(data)
                assert message["type"] == "portfolio_update"
