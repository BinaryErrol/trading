"""SQLAlchemy ORM models for the IBKR Trading Bot persistence layer.

Models correspond to the database schema defined in the design document:
- PositionRecord: Open positions with strategy association
- OrderRecord: Full order lifecycle with audit trail
- TradeRecord: Executed fills with commission tracking
- DailySnapshotRecord: End-of-day portfolio snapshots
- BacktestResultRecord: Stored backtest results for comparison
- AlertLogRecord: Notification delivery audit log
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class PositionRecord(Base):
    """Persisted position state for crash recovery.

    Maps to the `positions` table. Tracks open positions with their
    strategy association, entry price, and P&L.
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String(10), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    avg_entry_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<PositionRecord(id={self.id}, symbol={self.symbol!r}, "
            f"strategy={self.strategy_name!r}, qty={self.quantity})>"
        )


class OrderRecord(Base):
    """Full order lifecycle record with audit trail.

    Maps to the `orders` table. Tracks orders from submission through
    fill/cancellation/rejection with timestamps at each stage.
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ibkr_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)  # BUY, SELL
    order_type: Mapped[str] = mapped_column(String(15), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=0)
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationship to trades
    trades: Mapped[list["TradeRecord"]] = relationship(
        "TradeRecord", back_populates="order", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<OrderRecord(id={self.id}, symbol={self.symbol!r}, "
            f"direction={self.direction!r}, status={self.status!r})>"
        )


class TradeRecord(Base):
    """Executed trade (fill) record with commission tracking.

    Maps to the `trades` table. Each trade is linked to an order
    and records the actual execution details.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id"), nullable=True, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationship to order
    order: Mapped["OrderRecord | None"] = relationship(
        "OrderRecord", back_populates="trades", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<TradeRecord(id={self.id}, symbol={self.symbol!r}, "
            f"direction={self.direction!r}, qty={self.quantity}, price={self.price})>"
        )


class DailySnapshotRecord(Base):
    """End-of-day portfolio snapshot for historical tracking.

    Maps to the `daily_snapshots` table. One record per trading day
    with aggregate portfolio metrics and per-strategy breakdown.
    """

    __tablename__ = "daily_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    total_equity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    total_pnl: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    peak_equity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    strategy_metrics: Mapped[dict] = mapped_column(JSON, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<DailySnapshotRecord(id={self.id}, date={self.date}, "
            f"equity={self.total_equity})>"
        )


class BacktestResultRecord(Base):
    """Stored backtest result for comparison across parameter sets.

    Maps to the `backtest_results` table. Stores strategy parameters,
    date range, and performance metrics as JSON.
    """

    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_return: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<BacktestResultRecord(id={self.id}, strategy={self.strategy_name!r}, "
            f"return={self.total_return})>"
        )


class AlertLogRecord(Base):
    """Notification delivery audit log.

    Maps to the `alerts_log` table. Records every alert sent with
    delivery channel information.
    """

    __tablename__ = "alerts_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    delivered_channels: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AlertLogRecord(id={self.id}, event_type={self.event_type!r}, "
            f"priority={self.priority!r})>"
        )


class OptionsTradeRecord(Base):
    """Closed options trade record for the Wheel strategy.

    Maps to the `options_trades` table. Tracks individual options trades
    from open to close with premium and P&L tracking.
    """

    __tablename__ = "options_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    underlying: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    contract_symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    right: Mapped[str] = mapped_column(String(1), nullable=False)
    strike: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    expiration: Mapped[date] = mapped_column(Date, nullable=False)
    action: Mapped[str] = mapped_column(String(15), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    premium_collected: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    commission: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="open", index=True)

    def __repr__(self) -> str:
        return (
            f"<OptionsTradeRecord(id={self.id}, underlying={self.underlying!r}, "
            f"right={self.right!r}, strike={self.strike}, status={self.status!r})>"
        )


class StrategySnapshotRecord(Base):
    """Per-strategy daily equity snapshot for historical performance tracking.

    Maps to the `strategy_snapshots` table. One record per strategy per
    trading day with aggregate P&L metrics.
    """

    __tablename__ = "strategy_snapshots"
    __table_args__ = (UniqueConstraint("strategy_name", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    equity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    total_pnl: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return (
            f"<StrategySnapshotRecord(id={self.id}, strategy={self.strategy_name!r}, "
            f"date={self.date}, equity={self.equity})>"
        )
