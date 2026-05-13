"""State persistence for crash recovery.

Provides save/load operations for positions and orders so the bot can
resume from the last known state after a restart or crash.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.persistence.models import OrderRecord, PositionRecord

logger = structlog.get_logger(__name__)


async def save_position(
    session: AsyncSession,
    symbol: str,
    asset_class: str,
    strategy_name: str,
    quantity: Decimal,
    avg_entry_price: Decimal,
    current_price: Decimal | None = None,
    unrealized_pnl: Decimal | None = None,
    realized_pnl: Decimal = Decimal("0"),
    opened_at: datetime | None = None,
) -> PositionRecord:
    """Save or update a position in the database.

    If a position with the same symbol and strategy already exists,
    it will be updated. Otherwise, a new record is created.

    # NOTE: This upsert is not protected against concurrent calls for the same
    # symbol+strategy. In practice, only one coroutine writes positions at a time
    # (the reconciliation or fill handler), but if this changes, add a DB-level
    # unique constraint and use INSERT ... ON CONFLICT.

    Returns:
        The saved PositionRecord.
    """
    now = datetime.now(UTC)
    opened = opened_at or now

    # Check for existing position
    stmt = select(PositionRecord).where(
        PositionRecord.symbol == symbol,
        PositionRecord.strategy_name == strategy_name,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.quantity = quantity
        existing.avg_entry_price = avg_entry_price
        existing.current_price = current_price
        existing.unrealized_pnl = unrealized_pnl
        existing.realized_pnl = realized_pnl
        existing.updated_at = now
        await session.flush()
        logger.debug("position_updated", symbol=symbol, strategy=strategy_name)
        return existing
    else:
        record = PositionRecord(
            symbol=symbol,
            asset_class=asset_class,
            strategy_name=strategy_name,
            quantity=quantity,
            avg_entry_price=avg_entry_price,
            current_price=current_price,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            opened_at=opened,
            updated_at=now,
        )
        session.add(record)
        await session.flush()
        logger.debug("position_saved", symbol=symbol, strategy=strategy_name)
        return record


async def load_positions(session: AsyncSession) -> list[PositionRecord]:
    """Load all persisted positions from the database.

    Returns:
        List of all PositionRecord objects.
    """
    stmt = select(PositionRecord).order_by(PositionRecord.opened_at)
    result = await session.execute(stmt)
    positions = list(result.scalars().all())
    logger.info("positions_loaded", count=len(positions))
    return positions


async def remove_position(
    session: AsyncSession,
    symbol: str,
    strategy_name: str,
) -> bool:
    """Remove a closed position from the database.

    Returns:
        True if a position was removed, False if not found.
    """
    stmt = select(PositionRecord).where(
        PositionRecord.symbol == symbol,
        PositionRecord.strategy_name == strategy_name,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        await session.delete(existing)
        await session.flush()
        logger.debug("position_removed", symbol=symbol, strategy=strategy_name)
        return True
    return False


async def save_order(
    session: AsyncSession,
    ibkr_order_id: int | None,
    strategy_name: str,
    symbol: str,
    direction: str,
    order_type: str,
    quantity: Decimal,
    status: str,
    submitted_at: datetime,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    filled_quantity: Decimal = Decimal("0"),
    avg_fill_price: Decimal | None = None,
    filled_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    rejection_reason: str | None = None,
) -> OrderRecord:
    """Save an order record to the database.

    Creates a new order record for audit trail purposes.

    Returns:
        The saved OrderRecord.
    """
    record = OrderRecord(
        ibkr_order_id=ibkr_order_id,
        strategy_name=strategy_name,
        symbol=symbol,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        status=status,
        filled_quantity=filled_quantity,
        avg_fill_price=avg_fill_price,
        submitted_at=submitted_at,
        filled_at=filled_at,
        cancelled_at=cancelled_at,
        rejection_reason=rejection_reason,
    )
    session.add(record)
    await session.flush()
    logger.debug("order_saved", symbol=symbol, strategy=strategy_name, status=status)
    return record


async def update_order_status(
    session: AsyncSession,
    order_id: int,
    status: str,
    filled_quantity: Decimal | None = None,
    avg_fill_price: Decimal | None = None,
    filled_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    rejection_reason: str | None = None,
) -> bool:
    """Update an existing order's status and fill information.

    Returns:
        True if the order was found and updated, False otherwise.
    """
    stmt = select(OrderRecord).where(OrderRecord.id == order_id)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is None:
        return False

    existing.status = status
    if filled_quantity is not None:
        existing.filled_quantity = filled_quantity
    if avg_fill_price is not None:
        existing.avg_fill_price = avg_fill_price
    if filled_at is not None:
        existing.filled_at = filled_at
    if cancelled_at is not None:
        existing.cancelled_at = cancelled_at
    if rejection_reason is not None:
        existing.rejection_reason = rejection_reason

    await session.flush()
    logger.debug("order_status_updated", order_id=order_id, status=status)
    return True


async def load_open_orders(session: AsyncSession) -> list[OrderRecord]:
    """Load all orders that are not in a terminal state.

    Terminal states: filled, cancelled, rejected.

    Returns:
        List of open OrderRecord objects.
    """
    terminal_statuses = {"filled", "cancelled", "rejected"}
    stmt = (
        select(OrderRecord)
        .where(OrderRecord.status.notin_(terminal_statuses))
        .order_by(OrderRecord.submitted_at)
    )
    result = await session.execute(stmt)
    orders = list(result.scalars().all())
    logger.info("open_orders_loaded", count=len(orders))
    return orders
