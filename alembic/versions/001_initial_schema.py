"""Initial schema with all tables from design doc.

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000+00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all initial tables for the IBKR Trading Bot."""

    # Positions table
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("asset_class", sa.String(10), nullable=False),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("avg_entry_price", sa.Numeric(), nullable=False),
        sa.Column("current_price", sa.Numeric(), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_positions_symbol", "positions", ["symbol"])
    op.create_index("ix_positions_strategy_name", "positions", ["strategy_name"])

    # Orders table
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ibkr_order_id", sa.Integer(), nullable=True),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("order_type", sa.String(15), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("limit_price", sa.Numeric(), nullable=True),
        sa.Column("stop_price", sa.Numeric(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("filled_quantity", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.Numeric(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_ibkr_order_id", "orders", ["ibkr_order_id"])
    op.create_index("ix_orders_strategy_name", "orders", ["strategy_name"])
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_status", "orders", ["status"])

    # Trades table
    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("commission", sa.Numeric(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trades_order_id", "trades", ["order_id"])
    op.create_index("ix_trades_strategy_name", "trades", ["strategy_name"])
    op.create_index("ix_trades_symbol", "trades", ["symbol"])

    # Daily snapshots table
    op.create_table(
        "daily_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("total_equity", sa.Numeric(), nullable=False),
        sa.Column("total_pnl", sa.Numeric(), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(), nullable=False),
        sa.Column("peak_equity", sa.Numeric(), nullable=False),
        sa.Column("drawdown_pct", sa.Numeric(), nullable=False),
        sa.Column("strategy_metrics", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date"),
    )
    op.create_index("ix_daily_snapshots_date", "daily_snapshots", ["date"])

    # Backtest results table
    op.create_table(
        "backtest_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("total_return", sa.Numeric(), nullable=False),
        sa.Column("sharpe_ratio", sa.Numeric(), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_results_strategy_name", "backtest_results", ["strategy_name"])

    # Alerts log table
    op.create_table(
        "alerts_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("delivered_channels", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_log_event_type", "alerts_log", ["event_type"])
    op.create_index("ix_alerts_log_created_at", "alerts_log", ["created_at"])


def downgrade() -> None:
    """Drop all tables created in the initial migration."""
    op.drop_table("alerts_log")
    op.drop_table("backtest_results")
    op.drop_table("daily_snapshots")
    op.drop_table("trades")
    op.drop_table("orders")
    op.drop_table("positions")
