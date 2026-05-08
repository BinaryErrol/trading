"""Reconciliation helper for comparing persisted state with IBKR account.

On startup, compares the bot's persisted positions and orders with the
actual IBKR account state to detect and resolve discrepancies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.persistence.models import PositionRecord
from src.persistence.state import load_open_orders, load_positions, remove_position, save_position

logger = structlog.get_logger(__name__)


class IBKRConnectionProtocol(Protocol):
    """Protocol for IBKR connection used during reconciliation."""

    @property
    def ib(self) -> Any: ...

    @property
    def is_connected(self) -> bool: ...


@dataclass
class ReconciliationResult:
    """Result of comparing persisted state with IBKR account.

    Attributes:
        matched: Positions that match between DB and IBKR.
        added: Positions found in IBKR but not in DB (new positions).
        removed: Positions in DB but not in IBKR (closed externally).
        quantity_mismatches: Positions where quantity differs.
        stale_orders: Orders in DB that are no longer active in IBKR.
    """

    matched: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    quantity_mismatches: list[dict[str, Any]] = field(default_factory=list)
    stale_orders: list[int] = field(default_factory=list)

    @property
    def has_discrepancies(self) -> bool:
        """Return True if any discrepancies were found."""
        return bool(self.added or self.removed or self.quantity_mismatches or self.stale_orders)

    @property
    def summary(self) -> str:
        """Return a human-readable summary of reconciliation results."""
        parts = [f"matched={len(self.matched)}"]
        if self.added:
            parts.append(f"added={len(self.added)}")
        if self.removed:
            parts.append(f"removed={len(self.removed)}")
        if self.quantity_mismatches:
            parts.append(f"qty_mismatches={len(self.quantity_mismatches)}")
        if self.stale_orders:
            parts.append(f"stale_orders={len(self.stale_orders)}")
        return ", ".join(parts)


async def reconcile_positions(
    session: AsyncSession,
    ibkr_positions: list[dict[str, Any]],
    auto_fix: bool = True,
) -> ReconciliationResult:
    """Compare persisted positions with IBKR account positions.

    Args:
        session: Database session for reading/writing position state.
        ibkr_positions: List of position dicts from IBKR with keys:
            symbol, asset_class, quantity, avg_cost.
        auto_fix: If True, automatically update DB to match IBKR state.

    Returns:
        ReconciliationResult with details of any discrepancies.
    """
    result = ReconciliationResult()

    # Load persisted positions
    db_positions = await load_positions(session)
    db_by_symbol: dict[str, PositionRecord] = {p.symbol: p for p in db_positions}

    # Build set of IBKR symbols
    ibkr_by_symbol: dict[str, dict[str, Any]] = {p["symbol"]: p for p in ibkr_positions}

    # Check each IBKR position against DB
    for symbol, ibkr_pos in ibkr_by_symbol.items():
        ibkr_qty = Decimal(str(ibkr_pos["quantity"]))

        if symbol in db_by_symbol:
            db_pos = db_by_symbol[symbol]
            if db_pos.quantity == ibkr_qty:
                result.matched.append(symbol)
            else:
                result.quantity_mismatches.append({
                    "symbol": symbol,
                    "db_quantity": db_pos.quantity,
                    "ibkr_quantity": ibkr_qty,
                })
                if auto_fix:
                    db_pos.quantity = ibkr_qty
                    db_pos.avg_entry_price = Decimal(str(ibkr_pos["avg_cost"]))
                    await session.flush()
        else:
            # Position in IBKR but not in DB
            result.added.append(symbol)
            if auto_fix:
                await save_position(
                    session=session,
                    symbol=symbol,
                    asset_class=ibkr_pos.get("asset_class", "STK"),
                    strategy_name="unknown",
                    quantity=ibkr_qty,
                    avg_entry_price=Decimal(str(ibkr_pos["avg_cost"])),
                )

    # Check for positions in DB but not in IBKR (closed externally)
    for symbol in db_by_symbol:
        if symbol not in ibkr_by_symbol:
            result.removed.append(symbol)
            if auto_fix:
                await remove_position(
                    session=session,
                    symbol=symbol,
                    strategy_name=db_by_symbol[symbol].strategy_name,
                )

    logger.info(
        "reconciliation_complete",
        summary=result.summary,
        has_discrepancies=result.has_discrepancies,
    )

    return result


async def reconcile_orders(
    session: AsyncSession,
    ibkr_open_order_ids: set[int],
) -> ReconciliationResult:
    """Compare persisted open orders with IBKR active orders.

    Identifies orders that are tracked in the DB as open but are no longer
    active in IBKR (e.g., filled or cancelled externally).

    Args:
        session: Database session.
        ibkr_open_order_ids: Set of order IDs currently active in IBKR.

    Returns:
        ReconciliationResult with stale_orders populated.
    """
    result = ReconciliationResult()

    db_open_orders = await load_open_orders(session)

    for order in db_open_orders:
        if order.ibkr_order_id is not None and order.ibkr_order_id not in ibkr_open_order_ids:
            result.stale_orders.append(order.id)
            # Mark as cancelled since IBKR no longer has it
            order.status = "cancelled"
            await session.flush()

    if result.stale_orders:
        logger.warning(
            "stale_orders_found",
            count=len(result.stale_orders),
            order_ids=result.stale_orders,
        )

    return result
