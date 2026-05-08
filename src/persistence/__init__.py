"""Database persistence layer for the IBKR Trading Bot.

Provides async SQLAlchemy ORM models, session management, state persistence
for crash recovery, and reconciliation helpers.
"""

from src.persistence.database import (
    AsyncSessionFactory,
    close_db,
    get_engine,
    get_session,
    init_db,
)
from src.persistence.models import (
    AlertLogRecord,
    BacktestResultRecord,
    DailySnapshotRecord,
    OrderRecord,
    PositionRecord,
    TradeRecord,
)
from src.persistence.reconciliation import (
    ReconciliationResult,
    reconcile_orders,
    reconcile_positions,
)
from src.persistence.state import (
    load_open_orders,
    load_positions,
    remove_position,
    save_order,
    save_position,
    update_order_status,
)

__all__ = [
    "AlertLogRecord",
    "AsyncSessionFactory",
    "BacktestResultRecord",
    "DailySnapshotRecord",
    "OrderRecord",
    "PositionRecord",
    "ReconciliationResult",
    "TradeRecord",
    "close_db",
    "get_engine",
    "get_session",
    "init_db",
    "load_open_orders",
    "load_positions",
    "reconcile_orders",
    "reconcile_positions",
    "remove_position",
    "save_order",
    "save_position",
    "update_order_status",
]
