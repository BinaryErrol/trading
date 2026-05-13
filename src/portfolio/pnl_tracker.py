"""Per-strategy P&L tracker with daily snapshots and trade recording.

Tracks realized and unrealized P&L per strategy, records closed trades,
writes daily equity snapshots, and provides query interfaces for the
dashboard API.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.persistence.models import StrategySnapshotRecord, TradeRecord
from src.portfolio.monitor import PortfolioMonitor

logger = structlog.get_logger(__name__)


@dataclass
class StrategyPnL:
    """Realized and unrealized P&L for a single strategy."""

    strategy_name: str
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal


@dataclass
class EquityPoint:
    """A single point on the equity curve time-series."""

    date: date
    equity: Decimal


@dataclass
class TradeDetail:
    """Detailed record of a single trade."""

    id: int
    strategy_name: str
    symbol: str
    direction: str
    entry_price: Decimal
    exit_price: Decimal | None
    quantity: Decimal
    realized_pnl: Decimal
    opened_at: datetime
    closed_at: datetime | None


class PnLTracker:
    """Tracks per-strategy P&L and records daily snapshots.

    Composes with PortfolioMonitor for position data and uses a database
    session factory for persistence of snapshots and trade queries.
    """

    def __init__(
        self,
        portfolio_monitor: PortfolioMonitor,
        db_session_factory: async_sessionmaker[AsyncSession] | Any,
        update_interval: int = 60,
    ) -> None:
        """Initialize the P&L tracker.

        Args:
            portfolio_monitor: PortfolioMonitor instance for position data.
            db_session_factory: Async session factory for database access.
            update_interval: Seconds between unrealized P&L updates.
        """
        self._portfolio_monitor = portfolio_monitor
        self._db_session_factory = db_session_factory
        self._update_interval = update_interval
        self._update_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the periodic unrealized P&L update loop."""
        if self._running:
            return
        self._running = True
        self._update_task = asyncio.create_task(self._update_loop(), name="pnl-tracker-update")
        logger.info("pnl_tracker_started", interval=self._update_interval)

    async def stop(self) -> None:
        """Stop the update loop."""
        self._running = False
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        self._update_task = None
        logger.info("pnl_tracker_stopped")

    async def _update_loop(self) -> None:
        """Periodically update unrealized P&L by syncing positions."""
        try:
            while self._running:
                await asyncio.sleep(self._update_interval)
                try:
                    await self._portfolio_monitor.sync_positions()
                except Exception as exc:
                    logger.error("pnl_update_error", error=str(exc))
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # P&L Computation
    # ------------------------------------------------------------------

    async def get_strategy_pnl(self, strategy_name: str) -> StrategyPnL:
        """Compute realized + unrealized P&L for one strategy.

        Realized P&L comes from closed trades in the database.
        Unrealized P&L comes from current open positions in PortfolioMonitor.
        """
        # Unrealized from open positions
        positions = self._portfolio_monitor.positions
        unrealized = sum(
            (
                pos.unrealized_pnl
                for pos in positions.values()
                if pos.strategy_name == strategy_name
            ),
            Decimal("0"),
        )

        # Realized from closed trades in DB
        realized = Decimal("0")
        try:
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(
                        func.coalesce(func.sum(TradeRecord.price * TradeRecord.quantity), 0)
                    ).where(TradeRecord.strategy_name == strategy_name)
                )
                row = result.scalar_one_or_none()
                if row is not None:
                    realized = Decimal(str(row))
        except Exception as exc:
            logger.error("realized_pnl_query_error", strategy=strategy_name, error=str(exc))

        # Also include realized P&L from positions
        realized += sum(
            (pos.realized_pnl for pos in positions.values() if pos.strategy_name == strategy_name),
            Decimal("0"),
        )

        total = realized + unrealized
        return StrategyPnL(
            strategy_name=strategy_name,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            total_pnl=total,
        )

    async def get_all_strategies_pnl(self) -> list[StrategyPnL]:
        """Compute P&L for all strategies with positions or trades."""
        # Gather strategy names from positions
        positions = self._portfolio_monitor.positions
        strategy_names: set[str] = {pos.strategy_name for pos in positions.values()}

        # Also gather from trades in DB
        try:
            async with self._db_session_factory() as session:
                result = await session.execute(select(TradeRecord.strategy_name).distinct())
                for row in result.scalars().all():
                    strategy_names.add(row)
        except Exception as exc:
            logger.error("strategy_names_query_error", error=str(exc))

        # Compute P&L for each
        results = []
        for name in sorted(strategy_names):
            pnl = await self.get_strategy_pnl(name)
            results.append(pnl)

        return results

    # ------------------------------------------------------------------
    # Trade Recording
    # ------------------------------------------------------------------

    async def record_trade_close(
        self,
        strategy_name: str,
        symbol: str,
        direction: str,
        entry_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        realized_pnl: Decimal,
        entry_time: datetime,
        exit_time: datetime,
    ) -> None:
        """Record a closed trade to the database.

        Persists a TradeRecord with the trade details for historical tracking.
        """
        try:
            async with self._db_session_factory() as session:
                trade = TradeRecord(
                    strategy_name=strategy_name,
                    symbol=symbol,
                    direction=direction,
                    quantity=quantity,
                    price=exit_price,
                    commission=Decimal("0"),
                    executed_at=exit_time,
                )
                session.add(trade)
                await session.commit()
            logger.info(
                "trade_close_recorded",
                strategy=strategy_name,
                symbol=symbol,
                realized_pnl=str(realized_pnl),
            )
        except Exception as exc:
            logger.error(
                "trade_close_record_error",
                strategy=strategy_name,
                symbol=symbol,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Daily Snapshots
    # ------------------------------------------------------------------

    async def record_daily_snapshot(self) -> None:
        """Write end-of-day per-strategy equity to strategy_snapshots table.

        Creates or updates a snapshot for each strategy for today's date.
        """
        today = date.today()
        positions = self._portfolio_monitor.positions

        # Group positions by strategy
        strategy_data: dict[str, dict[str, Decimal]] = {}
        for pos in positions.values():
            name = pos.strategy_name
            if name not in strategy_data:
                strategy_data[name] = {
                    "equity": Decimal("0"),
                    "realized": Decimal("0"),
                    "unrealized": Decimal("0"),
                }
            strategy_data[name]["equity"] += pos.quantity * pos.current_price
            strategy_data[name]["realized"] += pos.realized_pnl
            strategy_data[name]["unrealized"] += pos.unrealized_pnl

        try:
            async with self._db_session_factory() as session:
                for name, data in strategy_data.items():
                    total_pnl = data["realized"] + data["unrealized"]

                    # Count trades for this strategy today
                    trade_count_result = await session.execute(
                        select(func.count(TradeRecord.id)).where(TradeRecord.strategy_name == name)
                    )
                    trade_count = trade_count_result.scalar_one() or 0

                    # Check if snapshot already exists (upsert)
                    existing = await session.execute(
                        select(StrategySnapshotRecord)
                        .where(StrategySnapshotRecord.strategy_name == name)
                        .where(StrategySnapshotRecord.date == today)
                    )
                    record = existing.scalar_one_or_none()

                    if record:
                        record.equity = data["equity"]
                        record.realized_pnl = data["realized"]
                        record.unrealized_pnl = data["unrealized"]
                        record.total_pnl = total_pnl
                        record.trade_count = trade_count
                    else:
                        snapshot = StrategySnapshotRecord(
                            strategy_name=name,
                            date=today,
                            equity=data["equity"],
                            realized_pnl=data["realized"],
                            unrealized_pnl=data["unrealized"],
                            total_pnl=total_pnl,
                            trade_count=trade_count,
                        )
                        session.add(snapshot)

                await session.commit()
            logger.info("daily_snapshots_recorded", strategies=len(strategy_data))
        except Exception as exc:
            logger.error("daily_snapshot_error", error=str(exc))

    # ------------------------------------------------------------------
    # History Queries
    # ------------------------------------------------------------------

    async def get_equity_history(
        self,
        strategy_name: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[EquityPoint]:
        """Query strategy_snapshots for equity curve data.

        Args:
            strategy_name: Strategy to query.
            start: Optional start date filter (inclusive).
            end: Optional end date filter (inclusive).

        Returns:
            List of EquityPoint ordered by date ascending.
        """
        try:
            async with self._db_session_factory() as session:
                query = (
                    select(StrategySnapshotRecord)
                    .where(StrategySnapshotRecord.strategy_name == strategy_name)
                    .order_by(StrategySnapshotRecord.date.asc())
                )
                if start is not None:
                    query = query.where(StrategySnapshotRecord.date >= start)
                if end is not None:
                    query = query.where(StrategySnapshotRecord.date <= end)

                result = await session.execute(query)
                records = result.scalars().all()

            return [EquityPoint(date=r.date, equity=r.equity) for r in records]
        except Exception as exc:
            logger.error(
                "equity_history_query_error",
                strategy=strategy_name,
                error=str(exc),
            )
            return []

    async def get_trades(
        self,
        strategy_name: str | None = None,
        symbol: str | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[TradeDetail], int]:
        """Query trades with filters and pagination.

        Args:
            strategy_name: Filter by strategy name.
            symbol: Filter by symbol.
            start: Filter by start date (inclusive).
            end: Filter by end date (inclusive).
            limit: Maximum number of records to return.
            offset: Number of records to skip.

        Returns:
            Tuple of (trade_details, total_count).
        """
        try:
            async with self._db_session_factory() as session:
                # Base query
                query = select(TradeRecord).order_by(TradeRecord.executed_at.desc())
                count_query = select(func.count(TradeRecord.id))

                # Apply filters
                if strategy_name is not None:
                    query = query.where(TradeRecord.strategy_name == strategy_name)
                    count_query = count_query.where(TradeRecord.strategy_name == strategy_name)
                if symbol is not None:
                    query = query.where(TradeRecord.symbol == symbol)
                    count_query = count_query.where(TradeRecord.symbol == symbol)
                if start is not None:
                    query = query.where(
                        TradeRecord.executed_at >= datetime.combine(start, datetime.min.time())
                    )
                    count_query = count_query.where(
                        TradeRecord.executed_at >= datetime.combine(start, datetime.min.time())
                    )
                if end is not None:
                    query = query.where(
                        TradeRecord.executed_at <= datetime.combine(end, datetime.max.time())
                    )
                    count_query = count_query.where(
                        TradeRecord.executed_at <= datetime.combine(end, datetime.max.time())
                    )

                # Get total count
                total_result = await session.execute(count_query)
                total_count = total_result.scalar_one()

                # Apply pagination
                query = query.limit(limit).offset(offset)
                result = await session.execute(query)
                records = result.scalars().all()

            items = [
                TradeDetail(
                    id=r.id,
                    strategy_name=r.strategy_name,
                    symbol=r.symbol,
                    direction=r.direction,
                    entry_price=r.price,
                    exit_price=r.price,
                    quantity=r.quantity,
                    realized_pnl=r.commission,
                    opened_at=r.executed_at,
                    closed_at=r.executed_at,
                )
                for r in records
            ]

            return items, total_count
        except Exception as exc:
            logger.error("trades_query_error", error=str(exc))
            return [], 0
