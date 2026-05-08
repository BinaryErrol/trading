"""Dashboard API module for the IBKR Trading Bot.

Provides FastAPI REST endpoints and WebSocket streaming for real-time
portfolio monitoring, strategy status, and order tracking.
"""

from src.dashboard.api import create_app

__all__ = ["create_app"]
