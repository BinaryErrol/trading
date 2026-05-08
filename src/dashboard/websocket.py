"""WebSocket endpoint for real-time dashboard streaming.

Provides a `/ws/live` endpoint that streams real-time updates including
positions, P&L, signals, and orders to connected dashboard clients.
"""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.portfolio.monitor import PortfolioMonitor

logger = structlog.get_logger(__name__)

router = APIRouter()


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


class ConnectionManager:
    """Manages active WebSocket connections for broadcasting updates."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    @property
    def active_connections(self) -> list[WebSocket]:
        """Return list of active WebSocket connections."""
        return list(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("websocket_connected", total_connections=len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active list."""
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info("websocket_disconnected", total_connections=len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected clients.

        Disconnected clients are automatically removed.
        """
        disconnected: list[WebSocket] = []
        encoded = json.dumps(message, cls=DecimalEncoder)

        for connection in self._connections:
            try:
                await connection.send_text(encoded)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)


# Module-level connection manager
ws_manager = ConnectionManager()

# Module-level reference to portfolio monitor (set at startup)
_ws_portfolio_monitor: PortfolioMonitor | None = None


def set_ws_portfolio_monitor(monitor: PortfolioMonitor) -> None:
    """Set the portfolio monitor for WebSocket streaming."""
    global _ws_portfolio_monitor
    _ws_portfolio_monitor = monitor


def _verify_ws_auth(token: str | None) -> bool:
    """Verify WebSocket authentication token.

    Returns True if auth passes (no token configured or token matches).
    """
    configured_token = os.environ.get("DASHBOARD_AUTH_TOKEN")
    if configured_token is None:
        return True  # No auth configured
    return token == configured_token


def _get_portfolio_snapshot(monitor: PortfolioMonitor) -> dict[str, Any]:
    """Build a portfolio snapshot for WebSocket broadcast."""
    total_value = monitor.get_total_value()
    peak_equity = monitor.get_peak_equity()
    unrealized_pnl = monitor.get_unrealized_pnl()

    if peak_equity > 0:
        drawdown_pct = float((peak_equity - total_value) / peak_equity * 100)
    else:
        drawdown_pct = 0.0

    positions = [
        {
            "symbol": pos.symbol,
            "strategy_name": pos.strategy_name,
            "quantity": float(pos.quantity),
            "current_price": float(pos.current_price),
            "unrealized_pnl": float(pos.unrealized_pnl),
        }
        for pos in monitor.positions.values()
    ]

    return {
        "type": "portfolio_update",
        "data": {
            "total_value": float(total_value),
            "unrealized_pnl": float(unrealized_pnl),
            "peak_equity": float(peak_equity),
            "drawdown_pct": drawdown_pct,
            "positions": positions,
            "position_count": len(positions),
        },
    }


@router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    """WebSocket endpoint streaming real-time portfolio updates.

    Clients can optionally authenticate by sending a token query parameter:
        ws://host/ws/live?token=<auth_token>

    Once connected, the server streams portfolio snapshots at regular intervals
    (every 2 seconds) including positions, P&L, and risk metrics.

    Clients can also send JSON messages to request specific data:
        {"action": "get_positions"} - Get current positions
        {"action": "get_portfolio"} - Get portfolio summary
    """
    # Check auth token from query params
    token = websocket.query_params.get("token")
    if not _verify_ws_auth(token):
        await websocket.close(code=4003, reason="Invalid authentication token")
        return

    await ws_manager.connect(websocket)

    try:
        # Start streaming task
        streaming_task = asyncio.create_task(_stream_updates(websocket))

        # Listen for client messages
        while True:
            try:
                data = await websocket.receive_text()
                await _handle_client_message(websocket, data)
            except WebSocketDisconnect:
                break

    except Exception as e:
        logger.error("websocket_error", error=str(e))
    finally:
        streaming_task.cancel()
        ws_manager.disconnect(websocket)


async def _stream_updates(websocket: WebSocket) -> None:
    """Stream periodic portfolio updates to a connected client."""
    try:
        while True:
            if _ws_portfolio_monitor is not None:
                snapshot = _get_portfolio_snapshot(_ws_portfolio_monitor)
                try:
                    await websocket.send_text(
                        json.dumps(snapshot, cls=DecimalEncoder)
                    )
                except Exception:
                    break
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        pass


async def _handle_client_message(websocket: WebSocket, data: str) -> None:
    """Handle incoming messages from WebSocket clients."""
    try:
        message = json.loads(data)
        action = message.get("action")

        if action == "get_positions" and _ws_portfolio_monitor is not None:
            positions = [
                {
                    "symbol": pos.symbol,
                    "strategy_name": pos.strategy_name,
                    "quantity": float(pos.quantity),
                    "current_price": float(pos.current_price),
                    "unrealized_pnl": float(pos.unrealized_pnl),
                }
                for pos in _ws_portfolio_monitor.positions.values()
            ]
            await websocket.send_text(
                json.dumps({"type": "positions", "data": positions}, cls=DecimalEncoder)
            )

        elif action == "get_portfolio" and _ws_portfolio_monitor is not None:
            snapshot = _get_portfolio_snapshot(_ws_portfolio_monitor)
            await websocket.send_text(json.dumps(snapshot, cls=DecimalEncoder))

        else:
            await websocket.send_text(
                json.dumps({"type": "error", "message": f"Unknown action: {action}"})
            )

    except json.JSONDecodeError:
        await websocket.send_text(
            json.dumps({"type": "error", "message": "Invalid JSON"})
        )
