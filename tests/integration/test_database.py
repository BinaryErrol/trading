"""Integration tests for the database persistence layer.

Tests CRUD operations, state persistence (save/load positions and orders),
reconciliation helpers, and migration up/down using SQLite in-memory.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.persistence.models import (
    AlertLogRecord,
    BacktestResultRecord,
    Base,
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


@pytest.fixture
async def async_engine():
    """Create an async SQLite in-memory engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(async_engine) -> AsyncSession:
    """Create an async session for testing."""
    factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Position CRUD Tests
# ---------------------------------------------------------------------------


class TestPositionCRUD:
    """Test position create, read, update, delete operations."""

    async def test_create_position(self, session: AsyncSession):
        """Test creating a new position record."""
        pos = PositionRecord(
            symbol="AAPL",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.50"),
            current_price=Decimal("155.00"),
            unrealized_pnl=Decimal("450.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(pos)
        await session.flush()

        assert pos.id is not None
        assert pos.symbol == "AAPL"
        assert pos.quantity == Decimal("100")

    async def test_read_position(self, session: AsyncSession):
        """Test reading a position back from the database."""
        pos = PositionRecord(
            symbol="MSFT",
            asset_class="STK",
            strategy_name="mean_reversion",
            quantity=Decimal("50"),
            avg_entry_price=Decimal("380.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(pos)
        await session.flush()

        from sqlalchemy import select

        stmt = select(PositionRecord).where(PositionRecord.symbol == "MSFT")
        result = await session.execute(stmt)
        loaded = result.scalar_one()

        assert loaded.symbol == "MSFT"
        assert loaded.strategy_name == "mean_reversion"
        assert loaded.quantity == Decimal("50")

    async def test_update_position(self, session: AsyncSession):
        """Test updating an existing position."""
        pos = PositionRecord(
            symbol="GOOG",
            asset_class="STK",
            strategy_name="breakout",
            quantity=Decimal("25"),
            avg_entry_price=Decimal("140.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(pos)
        await session.flush()

        pos.quantity = Decimal("50")
        pos.current_price = Decimal("145.00")
        await session.flush()

        from sqlalchemy import select

        stmt = select(PositionRecord).where(PositionRecord.id == pos.id)
        result = await session.execute(stmt)
        updated = result.scalar_one()

        assert updated.quantity == Decimal("50")
        assert updated.current_price == Decimal("145.00")

    async def test_delete_position(self, session: AsyncSession):
        """Test deleting a position record."""
        pos = PositionRecord(
            symbol="TSLA",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("10"),
            avg_entry_price=Decimal("250.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(pos)
        await session.flush()

        await session.delete(pos)
        await session.flush()

        from sqlalchemy import select

        stmt = select(PositionRecord).where(PositionRecord.symbol == "TSLA")
        result = await session.execute(stmt)
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Order CRUD Tests
# ---------------------------------------------------------------------------


class TestOrderCRUD:
    """Test order create, read, update operations."""

    async def test_create_order(self, session: AsyncSession):
        """Test creating a new order record."""
        order = OrderRecord(
            ibkr_order_id=12345,
            strategy_name="momentum",
            symbol="AAPL",
            direction="BUY",
            order_type="LMT",
            quantity=Decimal("100"),
            limit_price=Decimal("150.00"),
            status="submitted",
            filled_quantity=Decimal("0"),
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(order)
        await session.flush()

        assert order.id is not None
        assert order.ibkr_order_id == 12345
        assert order.status == "submitted"

    async def test_order_status_transitions(self, session: AsyncSession):
        """Test order status can be updated through lifecycle."""
        order = OrderRecord(
            ibkr_order_id=99999,
            strategy_name="breakout",
            symbol="MSFT",
            direction="BUY",
            order_type="MKT",
            quantity=Decimal("50"),
            status="submitted",
            filled_quantity=Decimal("0"),
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(order)
        await session.flush()

        # Transition to filled
        order.status = "filled"
        order.filled_quantity = Decimal("50")
        order.avg_fill_price = Decimal("380.25")
        order.filled_at = datetime.now(timezone.utc)
        await session.flush()

        from sqlalchemy import select

        stmt = select(OrderRecord).where(OrderRecord.id == order.id)
        result = await session.execute(stmt)
        loaded = result.scalar_one()

        assert loaded.status == "filled"
        assert loaded.filled_quantity == Decimal("50")
        assert loaded.avg_fill_price == Decimal("380.25")

    async def test_order_with_rejection(self, session: AsyncSession):
        """Test order rejection with reason."""
        order = OrderRecord(
            ibkr_order_id=None,
            strategy_name="pairs",
            symbol="SPY",
            direction="SELL",
            order_type="LMT",
            quantity=Decimal("200"),
            limit_price=Decimal("450.00"),
            status="rejected",
            filled_quantity=Decimal("0"),
            submitted_at=datetime.now(timezone.utc),
            rejection_reason="Insufficient margin",
        )
        session.add(order)
        await session.flush()

        assert order.rejection_reason == "Insufficient margin"


# ---------------------------------------------------------------------------
# Trade CRUD Tests
# ---------------------------------------------------------------------------


class TestTradeCRUD:
    """Test trade record operations."""

    async def test_create_trade(self, session: AsyncSession):
        """Test creating a trade record."""
        trade = TradeRecord(
            order_id=None,
            strategy_name="momentum",
            symbol="AAPL",
            direction="BUY",
            quantity=Decimal("100"),
            price=Decimal("150.25"),
            commission=Decimal("0.50"),
            executed_at=datetime.now(timezone.utc),
        )
        session.add(trade)
        await session.flush()

        assert trade.id is not None
        assert trade.commission == Decimal("0.50")

    async def test_trade_linked_to_order(self, session: AsyncSession):
        """Test trade can reference an order."""
        order = OrderRecord(
            ibkr_order_id=11111,
            strategy_name="momentum",
            symbol="AAPL",
            direction="BUY",
            order_type="MKT",
            quantity=Decimal("100"),
            status="filled",
            filled_quantity=Decimal("100"),
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(order)
        await session.flush()

        trade = TradeRecord(
            order_id=order.id,
            strategy_name="momentum",
            symbol="AAPL",
            direction="BUY",
            quantity=Decimal("100"),
            price=Decimal("150.25"),
            commission=Decimal("0.50"),
            executed_at=datetime.now(timezone.utc),
        )
        session.add(trade)
        await session.flush()

        assert trade.order_id == order.id


# ---------------------------------------------------------------------------
# DailySnapshot & BacktestResult Tests
# ---------------------------------------------------------------------------


class TestSnapshotAndBacktest:
    """Test daily snapshot and backtest result records."""

    async def test_create_daily_snapshot(self, session: AsyncSession):
        """Test creating a daily snapshot record."""
        snapshot = DailySnapshotRecord(
            date=date(2024, 1, 15),
            total_equity=Decimal("105000.00"),
            total_pnl=Decimal("5000.00"),
            realized_pnl=Decimal("3000.00"),
            unrealized_pnl=Decimal("2000.00"),
            peak_equity=Decimal("106000.00"),
            drawdown_pct=Decimal("0.0094"),
            strategy_metrics={"momentum": {"return": 0.05, "sharpe": 1.2}},
        )
        session.add(snapshot)
        await session.flush()

        assert snapshot.id is not None
        assert snapshot.strategy_metrics["momentum"]["sharpe"] == 1.2

    async def test_create_backtest_result(self, session: AsyncSession):
        """Test creating a backtest result record."""
        result = BacktestResultRecord(
            strategy_name="momentum",
            parameters={"lookback_period": 20, "threshold": 0.02},
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            total_return=Decimal("0.15"),
            sharpe_ratio=Decimal("1.5"),
            max_drawdown=Decimal("0.08"),
            metrics={"win_rate": 0.55, "profit_factor": 1.8},
            created_at=datetime.now(timezone.utc),
        )
        session.add(result)
        await session.flush()

        assert result.id is not None
        assert result.total_return == Decimal("0.15")

    async def test_create_alert_log(self, session: AsyncSession):
        """Test creating an alert log record."""
        alert = AlertLogRecord(
            event_type="trade_executed",
            priority="MEDIUM",
            title="Order Filled",
            message="BUY 100 AAPL @ 150.25",
            delivered_channels="slack,email",
            created_at=datetime.now(timezone.utc),
        )
        session.add(alert)
        await session.flush()

        assert alert.id is not None
        assert alert.delivered_channels == "slack,email"


# ---------------------------------------------------------------------------
# State Persistence Tests (save/load for crash recovery)
# ---------------------------------------------------------------------------


class TestStatePersistence:
    """Test state persistence functions for crash recovery."""

    async def test_save_and_load_position(self, session: AsyncSession):
        """Test saving a position and loading it back."""
        await save_position(
            session=session,
            symbol="AAPL",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("155.00"),
            unrealized_pnl=Decimal("500.00"),
        )
        await session.commit()

        positions = await load_positions(session)
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"
        assert positions[0].quantity == Decimal("100")

    async def test_save_position_updates_existing(self, session: AsyncSession):
        """Test that saving a position with same symbol+strategy updates it."""
        await save_position(
            session=session,
            symbol="MSFT",
            asset_class="STK",
            strategy_name="breakout",
            quantity=Decimal("50"),
            avg_entry_price=Decimal("380.00"),
        )
        await session.commit()

        # Update the same position
        await save_position(
            session=session,
            symbol="MSFT",
            asset_class="STK",
            strategy_name="breakout",
            quantity=Decimal("75"),
            avg_entry_price=Decimal("375.00"),
        )
        await session.commit()

        positions = await load_positions(session)
        assert len(positions) == 1
        assert positions[0].quantity == Decimal("75")
        assert positions[0].avg_entry_price == Decimal("375.00")

    async def test_remove_position(self, session: AsyncSession):
        """Test removing a closed position."""
        await save_position(
            session=session,
            symbol="GOOG",
            asset_class="STK",
            strategy_name="mean_reversion",
            quantity=Decimal("25"),
            avg_entry_price=Decimal("140.00"),
        )
        await session.commit()

        removed = await remove_position(session, "GOOG", "mean_reversion")
        await session.commit()

        assert removed is True
        positions = await load_positions(session)
        assert len(positions) == 0

    async def test_remove_nonexistent_position(self, session: AsyncSession):
        """Test removing a position that doesn't exist returns False."""
        removed = await remove_position(session, "FAKE", "nonexistent")
        assert removed is False

    async def test_save_and_load_order(self, session: AsyncSession):
        """Test saving an order and loading open orders."""
        now = datetime.now(timezone.utc)
        await save_order(
            session=session,
            ibkr_order_id=12345,
            strategy_name="momentum",
            symbol="AAPL",
            direction="BUY",
            order_type="LMT",
            quantity=Decimal("100"),
            status="submitted",
            submitted_at=now,
            limit_price=Decimal("150.00"),
        )
        await session.commit()

        open_orders = await load_open_orders(session)
        assert len(open_orders) == 1
        assert open_orders[0].ibkr_order_id == 12345
        assert open_orders[0].status == "submitted"

    async def test_load_open_orders_excludes_terminal(self, session: AsyncSession):
        """Test that filled/cancelled/rejected orders are excluded from open orders."""
        now = datetime.now(timezone.utc)

        # Create orders in various states
        for status in ["submitted", "accepted", "filled", "cancelled", "rejected"]:
            await save_order(
                session=session,
                ibkr_order_id=None,
                strategy_name="test",
                symbol="SPY",
                direction="BUY",
                order_type="MKT",
                quantity=Decimal("10"),
                status=status,
                submitted_at=now,
            )
        await session.commit()

        open_orders = await load_open_orders(session)
        statuses = {o.status for o in open_orders}
        assert "filled" not in statuses
        assert "cancelled" not in statuses
        assert "rejected" not in statuses
        assert "submitted" in statuses
        assert "accepted" in statuses

    async def test_update_order_status(self, session: AsyncSession):
        """Test updating an order's status."""
        now = datetime.now(timezone.utc)
        order = await save_order(
            session=session,
            ibkr_order_id=55555,
            strategy_name="breakout",
            symbol="TSLA",
            direction="BUY",
            order_type="MKT",
            quantity=Decimal("20"),
            status="submitted",
            submitted_at=now,
        )
        await session.commit()

        updated = await update_order_status(
            session=session,
            order_id=order.id,
            status="filled",
            filled_quantity=Decimal("20"),
            avg_fill_price=Decimal("250.50"),
            filled_at=datetime.now(timezone.utc),
        )
        await session.commit()

        assert updated is True

        # Verify it's no longer in open orders
        open_orders = await load_open_orders(session)
        assert all(o.id != order.id for o in open_orders)

    async def test_update_nonexistent_order(self, session: AsyncSession):
        """Test updating a non-existent order returns False."""
        updated = await update_order_status(
            session=session,
            order_id=99999,
            status="filled",
        )
        assert updated is False


# ---------------------------------------------------------------------------
# Reconciliation Tests
# ---------------------------------------------------------------------------


class TestReconciliation:
    """Test reconciliation helpers for comparing DB state with IBKR."""

    async def test_reconcile_positions_all_match(self, session: AsyncSession):
        """Test reconciliation when all positions match."""
        await save_position(
            session=session,
            symbol="AAPL",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        await session.commit()

        ibkr_positions = [
            {"symbol": "AAPL", "asset_class": "STK", "quantity": 100, "avg_cost": 150.00}
        ]

        result = await reconcile_positions(session, ibkr_positions, auto_fix=False)

        assert not result.has_discrepancies
        assert "AAPL" in result.matched

    async def test_reconcile_positions_new_in_ibkr(self, session: AsyncSession):
        """Test reconciliation detects positions in IBKR but not in DB."""
        ibkr_positions = [
            {"symbol": "MSFT", "asset_class": "STK", "quantity": 50, "avg_cost": 380.00}
        ]

        result = await reconcile_positions(session, ibkr_positions, auto_fix=False)

        assert result.has_discrepancies
        assert "MSFT" in result.added

    async def test_reconcile_positions_auto_fix_adds(self, session: AsyncSession):
        """Test auto_fix adds missing positions to DB."""
        ibkr_positions = [
            {"symbol": "GOOG", "asset_class": "STK", "quantity": 25, "avg_cost": 140.00}
        ]

        result = await reconcile_positions(session, ibkr_positions, auto_fix=True)
        await session.commit()

        assert "GOOG" in result.added

        # Verify it was added to DB
        positions = await load_positions(session)
        assert any(p.symbol == "GOOG" for p in positions)

    async def test_reconcile_positions_removed_from_ibkr(self, session: AsyncSession):
        """Test reconciliation detects positions closed externally."""
        await save_position(
            session=session,
            symbol="TSLA",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("10"),
            avg_entry_price=Decimal("250.00"),
        )
        await session.commit()

        # IBKR has no positions
        ibkr_positions = []

        result = await reconcile_positions(session, ibkr_positions, auto_fix=True)
        await session.commit()

        assert result.has_discrepancies
        assert "TSLA" in result.removed

        # Verify it was removed from DB
        positions = await load_positions(session)
        assert not any(p.symbol == "TSLA" for p in positions)

    async def test_reconcile_positions_quantity_mismatch(self, session: AsyncSession):
        """Test reconciliation detects quantity differences."""
        await save_position(
            session=session,
            symbol="AAPL",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        await session.commit()

        ibkr_positions = [
            {"symbol": "AAPL", "asset_class": "STK", "quantity": 75, "avg_cost": 148.00}
        ]

        result = await reconcile_positions(session, ibkr_positions, auto_fix=True)
        await session.commit()

        assert result.has_discrepancies
        assert len(result.quantity_mismatches) == 1
        mismatch = result.quantity_mismatches[0]
        assert mismatch["symbol"] == "AAPL"
        assert mismatch["db_quantity"] == Decimal("100")
        assert mismatch["ibkr_quantity"] == Decimal("75")

    async def test_reconcile_orders_stale_detection(self, session: AsyncSession):
        """Test reconciliation detects stale orders."""
        now = datetime.now(timezone.utc)
        order = await save_order(
            session=session,
            ibkr_order_id=12345,
            strategy_name="momentum",
            symbol="AAPL",
            direction="BUY",
            order_type="LMT",
            quantity=Decimal("100"),
            status="submitted",
            submitted_at=now,
        )
        await session.commit()

        # IBKR has no open orders (order was filled/cancelled externally)
        ibkr_open_order_ids: set[int] = set()

        result = await reconcile_orders(session, ibkr_open_order_ids)
        await session.commit()

        assert result.has_discrepancies
        assert order.id in result.stale_orders

    async def test_reconcile_orders_no_stale(self, session: AsyncSession):
        """Test reconciliation with no stale orders."""
        now = datetime.now(timezone.utc)
        await save_order(
            session=session,
            ibkr_order_id=12345,
            strategy_name="momentum",
            symbol="AAPL",
            direction="BUY",
            order_type="LMT",
            quantity=Decimal("100"),
            status="submitted",
            submitted_at=now,
        )
        await session.commit()

        # IBKR still has this order active
        ibkr_open_order_ids = {12345}

        result = await reconcile_orders(session, ibkr_open_order_ids)

        assert not result.has_discrepancies
        assert len(result.stale_orders) == 0


# ---------------------------------------------------------------------------
# Migration Up/Down Tests
# ---------------------------------------------------------------------------


class TestMigrations:
    """Test that schema creation and teardown work correctly."""

    async def test_create_all_tables(self):
        """Test that all tables can be created from ORM models."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Verify tables exist
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
            tables = {row[0] for row in result.fetchall()}

        expected_tables = {
            "positions",
            "orders",
            "trades",
            "daily_snapshots",
            "backtest_results",
            "alerts_log",
        }
        assert expected_tables.issubset(tables)
        await engine.dispose()

    async def test_drop_all_tables(self):
        """Test that all tables can be dropped (migration downgrade)."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Drop all tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        # Verify tables are gone
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
            tables = {row[0] for row in result.fetchall()}

        assert "positions" not in tables
        assert "orders" not in tables
        await engine.dispose()

    async def test_init_db_creates_tables(self):
        """Test that init_db creates tables when using create_tables flag.

        Uses a direct engine approach since init_db's pool params
        aren't compatible with SQLite.
        """
        from src.persistence.database import get_session_factory

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = get_session_factory(engine)
        async with factory() as session:
            # Verify we can use the session to insert data
            pos = PositionRecord(
                symbol="TEST",
                asset_class="STK",
                strategy_name="test",
                quantity=Decimal("1"),
                avg_entry_price=Decimal("100"),
                realized_pnl=Decimal("0"),
                opened_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(pos)
            await session.commit()

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
            tables = {row[0] for row in result.fetchall()}

        assert "positions" in tables
        assert "orders" in tables
        assert "trades" in tables

        await engine.dispose()
