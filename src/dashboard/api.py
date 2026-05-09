"""FastAPI application for the IBKR Trading Bot monitoring dashboard.

Provides REST endpoints for portfolio data, strategy status, risk metrics,
order history, and CSV export. Includes token-based authentication middleware
and a health check endpoint for Docker.
"""

from __future__ import annotations

import hmac
import os
from datetime import date
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.dashboard.websocket import router as ws_router
from src.portfolio.monitor import PortfolioMonitor

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PortfolioSummary(BaseModel):
    """Portfolio overview response."""

    total_value: float
    unrealized_pnl: float
    peak_equity: float
    drawdown_pct: float


class PositionResponse(BaseModel):
    """Single position response."""

    symbol: str
    asset_class: str
    strategy_name: str
    quantity: float
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float


class StrategyStatusResponse(BaseModel):
    """Strategy status response."""

    name: str
    total_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    total_trades: int


class RiskMetricsResponse(BaseModel):
    """Risk metrics response."""

    portfolio_value: float
    peak_equity: float
    drawdown_pct: float
    unrealized_pnl: float
    position_count: int


class OrderResponse(BaseModel):
    """Order record response."""

    id: int
    ibkr_order_id: int | None
    strategy_name: str
    symbol: str
    direction: str
    order_type: str
    quantity: float
    limit_price: float | None
    stop_price: float | None
    status: str
    filled_quantity: float
    avg_fill_price: float | None
    submitted_at: str
    filled_at: str | None
    cancelled_at: str | None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


class ExportResponse(BaseModel):
    """CSV export response."""

    filepath: str
    trade_count: int


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

# Module-level references set by the application at startup
_portfolio_monitor: PortfolioMonitor | None = None
_db_session_factory: Any = None


def set_portfolio_monitor(monitor: PortfolioMonitor) -> None:
    """Set the portfolio monitor instance for dependency injection."""
    global _portfolio_monitor
    _portfolio_monitor = monitor


def set_db_session_factory(factory: Any) -> None:
    """Set the database session factory for dependency injection."""
    global _db_session_factory
    _db_session_factory = factory


def get_portfolio_monitor() -> PortfolioMonitor:
    """FastAPI dependency that provides the PortfolioMonitor instance."""
    if _portfolio_monitor is None:
        raise HTTPException(status_code=503, detail="Portfolio monitor not initialized")
    return _portfolio_monitor


def get_db_session_factory() -> Any:
    """FastAPI dependency that provides the database session factory."""
    if _db_session_factory is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return _db_session_factory


# ---------------------------------------------------------------------------
# Authentication middleware
# ---------------------------------------------------------------------------


def get_auth_token() -> str | None:
    """Get the configured auth token from environment variable."""
    return os.environ.get("DASHBOARD_AUTH_TOKEN")


async def verify_auth(request: Request) -> None:
    """Verify the authentication token if configured.

    Checks the Authorization header for a Bearer token matching
    the DASHBOARD_AUTH_TOKEN environment variable. If no token is
    configured, all requests are allowed.
    """
    token = get_auth_token()
    if token is None:
        return  # No auth configured, allow all

    # Skip auth for health endpoint
    if request.url.path == "/health":
        return

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    # Expect "Bearer <token>"
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")

    if not hmac.compare_digest(parts[1], token):
        raise HTTPException(status_code=403, detail="Invalid authentication token")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(
    portfolio_monitor: PortfolioMonitor | None = None,
    db_session_factory: Any = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        portfolio_monitor: PortfolioMonitor instance for portfolio data.
        db_session_factory: Async session factory for database access.
        cors_origins: Allowed CORS origins for the frontend.

    Returns:
        Configured FastAPI application.
    """
    if portfolio_monitor is not None:
        set_portfolio_monitor(portfolio_monitor)
    if db_session_factory is not None:
        set_db_session_factory(db_session_factory)

    app = FastAPI(
        title="IBKR Trading Bot Dashboard",
        version="0.1.0",
        description="REST API and WebSocket for real-time trading bot monitoring",
    )

    # CORS middleware
    origins = cors_origins or ["http://localhost:3000"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include WebSocket router
    app.include_router(ws_router)

    # -----------------------------------------------------------------------
    # Health check
    # -----------------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    async def health_check() -> HealthResponse:
        """Health check endpoint for Docker and load balancers."""
        return HealthResponse(status="healthy", version="0.1.0")

    # -----------------------------------------------------------------------
    # Portfolio endpoints (auth-protected)
    # -----------------------------------------------------------------------

    @app.get("/api/portfolio", response_model=PortfolioSummary)
    async def get_portfolio(
        _: None = Depends(verify_auth),
        monitor: PortfolioMonitor = Depends(get_portfolio_monitor),
    ) -> PortfolioSummary:
        """Get portfolio summary with total value, P&L, and drawdown."""
        total_value = monitor.get_total_value()
        unrealized_pnl = monitor.get_unrealized_pnl()
        peak_equity = monitor.get_peak_equity()

        if peak_equity > 0:
            drawdown_pct = float((peak_equity - total_value) / peak_equity * 100)
        else:
            drawdown_pct = 0.0

        return PortfolioSummary(
            total_value=float(total_value),
            unrealized_pnl=float(unrealized_pnl),
            peak_equity=float(peak_equity),
            drawdown_pct=drawdown_pct,
        )

    @app.get("/api/positions", response_model=list[PositionResponse])
    async def get_positions(
        _: None = Depends(verify_auth),
        monitor: PortfolioMonitor = Depends(get_portfolio_monitor),
    ) -> list[PositionResponse]:
        """Get all current positions."""
        positions = monitor.positions
        return [
            PositionResponse(
                symbol=pos.symbol,
                asset_class=pos.asset_class,
                strategy_name=pos.strategy_name,
                quantity=float(pos.quantity),
                avg_entry_price=float(pos.avg_entry_price),
                current_price=float(pos.current_price),
                unrealized_pnl=float(pos.unrealized_pnl),
                realized_pnl=float(pos.realized_pnl),
            )
            for pos in positions.values()
        ]

    @app.get("/api/strategies", response_model=list[StrategyStatusResponse])
    async def get_strategies(
        _: None = Depends(verify_auth),
        monitor: PortfolioMonitor = Depends(get_portfolio_monitor),
    ) -> list[StrategyStatusResponse]:
        """Get performance metrics for all strategies with recorded trades."""
        # Get unique strategy names from trades
        strategy_names = set(t.strategy_name for t in monitor._trades)
        results = []
        for name in sorted(strategy_names):
            metrics = monitor.calculate_strategy_metrics(name)
            results.append(
                StrategyStatusResponse(
                    name=name,
                    total_return=float(metrics.total_return),
                    sharpe_ratio=metrics.sharpe_ratio,
                    sortino_ratio=metrics.sortino_ratio,
                    max_drawdown=float(metrics.max_drawdown),
                    win_rate=metrics.win_rate,
                    profit_factor=metrics.profit_factor,
                    total_trades=metrics.total_trades,
                )
            )
        return results

    @app.get("/api/performance/{strategy}", response_model=StrategyStatusResponse)
    async def get_performance(
        strategy: str,
        _: None = Depends(verify_auth),
        monitor: PortfolioMonitor = Depends(get_portfolio_monitor),
    ) -> StrategyStatusResponse:
        """Get performance metrics for a specific strategy."""
        metrics = monitor.calculate_strategy_metrics(strategy)
        if metrics.total_trades == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No trades found for strategy '{strategy}'",
            )
        return StrategyStatusResponse(
            name=strategy,
            total_return=float(metrics.total_return),
            sharpe_ratio=metrics.sharpe_ratio,
            sortino_ratio=metrics.sortino_ratio,
            max_drawdown=float(metrics.max_drawdown),
            win_rate=metrics.win_rate,
            profit_factor=metrics.profit_factor,
            total_trades=metrics.total_trades,
        )

    @app.get("/api/risk", response_model=RiskMetricsResponse)
    async def get_risk_metrics(
        _: None = Depends(verify_auth),
        monitor: PortfolioMonitor = Depends(get_portfolio_monitor),
    ) -> RiskMetricsResponse:
        """Get current risk metrics."""
        total_value = monitor.get_total_value()
        peak_equity = monitor.get_peak_equity()
        unrealized_pnl = monitor.get_unrealized_pnl()

        if peak_equity > 0:
            drawdown_pct = float((peak_equity - total_value) / peak_equity * 100)
        else:
            drawdown_pct = 0.0

        return RiskMetricsResponse(
            portfolio_value=float(total_value),
            peak_equity=float(peak_equity),
            drawdown_pct=drawdown_pct,
            unrealized_pnl=float(unrealized_pnl),
            position_count=len(monitor.positions),
        )

    @app.get("/api/orders", response_model=list[OrderResponse])
    async def get_orders(
        _: None = Depends(verify_auth),
        session_factory: Any = Depends(get_db_session_factory),
    ) -> list[OrderResponse]:
        """Get order history from the database."""
        from sqlalchemy import select

        from src.persistence.models import OrderRecord

        async with session_factory() as session:
            result = await session.execute(
                select(OrderRecord).order_by(OrderRecord.submitted_at.desc()).limit(100)
            )
            orders = result.scalars().all()

        return [
            OrderResponse(
                id=order.id,
                ibkr_order_id=order.ibkr_order_id,
                strategy_name=order.strategy_name,
                symbol=order.symbol,
                direction=order.direction,
                order_type=order.order_type,
                quantity=float(order.quantity),
                limit_price=float(order.limit_price) if order.limit_price else None,
                stop_price=float(order.stop_price) if order.stop_price else None,
                status=order.status,
                filled_quantity=float(order.filled_quantity),
                avg_fill_price=(
                    float(order.avg_fill_price) if order.avg_fill_price else None
                ),
                submitted_at=order.submitted_at.isoformat(),
                filled_at=order.filled_at.isoformat() if order.filled_at else None,
                cancelled_at=order.cancelled_at.isoformat() if order.cancelled_at else None,
            )
            for order in orders
        ]

    @app.get("/api/export/csv", response_model=ExportResponse)
    async def export_csv(
        start: str | None = None,
        end: str | None = None,
        _: None = Depends(verify_auth),
        monitor: PortfolioMonitor = Depends(get_portfolio_monitor),
    ) -> ExportResponse:
        """Trigger CSV export of trade history.

        Query params:
            start: Start date (YYYY-MM-DD). Defaults to 30 days ago.
            end: End date (YYYY-MM-DD). Defaults to today.
        """
        from datetime import timedelta

        today = date.today()

        if start:
            start_date = date.fromisoformat(start)
        else:
            start_date = today - timedelta(days=30)

        if end:
            end_date = date.fromisoformat(end)
        else:
            end_date = today

        filepath = await monitor.export_csv(start_date, end_date)

        # Count trades in the exported range
        trade_count = len([
            t for t in monitor._trades
            if start_date <= t.closed_at.date() <= end_date
        ])

        return ExportResponse(filepath=str(filepath), trade_count=trade_count)

    return app
