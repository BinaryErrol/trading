"""Add options_trades and strategy_snapshots tables.

Revision ID: 002
Revises: 001
Create Date: 2024-01-15 00:00:00.000000+00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create options_trades and strategy_snapshots tables."""

    # Options trades table
    op.create_table(
        "options_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("underlying", sa.String(20), nullable=False),
        sa.Column("contract_symbol", sa.String(40), nullable=False),
        sa.Column("right", sa.String(1), nullable=False),
        sa.Column("strike", sa.Numeric(), nullable=False),
        sa.Column("expiration", sa.Date(), nullable=False),
        sa.Column("action", sa.String(15), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("entry_price", sa.Numeric(), nullable=False),
        sa.Column("exit_price", sa.Numeric(), nullable=True),
        sa.Column("premium_collected", sa.Numeric(), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(), nullable=True),
        sa.Column("commission", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(15), nullable=False, server_default="open"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_options_trades_strategy_name", "options_trades", ["strategy_name"])
    op.create_index("ix_options_trades_underlying", "options_trades", ["underlying"])
    op.create_index("ix_options_trades_status", "options_trades", ["status"])

    # Strategy snapshots table
    op.create_table(
        "strategy_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("equity", sa.Numeric(), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(), nullable=False),
        sa.Column("total_pnl", sa.Numeric(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_name", "date"),
    )
    op.create_index(
        "ix_strategy_snapshots_name_date", "strategy_snapshots", ["strategy_name", "date"]
    )


def downgrade() -> None:
    """Drop options_trades and strategy_snapshots tables."""
    op.drop_table("strategy_snapshots")
    op.drop_table("options_trades")
