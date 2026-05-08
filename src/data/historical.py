"""Historical data loading from IBKR API or local CSV files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


def load_historical_csv(
    filepath: Path | str,
    symbol: str | None = None,
) -> pd.DataFrame:
    """Load historical OHLCV data from a CSV file.

    Expected CSV columns: date/timestamp, open, high, low, close, volume.
    The function is flexible about column naming (case-insensitive).

    Args:
        filepath: Path to the CSV file.
        symbol: Optional symbol name to add as a column.

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume.
        Indexed by timestamp (datetime).

    Raises:
        FileNotFoundError: If the CSV file does not exist.
        ValueError: If required columns are missing.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Historical data file not found: {filepath}")

    logger.info("loading_historical_csv", filepath=str(filepath), symbol=symbol)

    df = pd.read_csv(filepath)

    # Normalize column names to lowercase
    df.columns = [col.strip().lower() for col in df.columns]

    # Identify timestamp column
    timestamp_col = None
    for candidate in ("timestamp", "date", "datetime", "time"):
        if candidate in df.columns:
            timestamp_col = candidate
            break

    if timestamp_col is None:
        raise ValueError(
            f"CSV must have a timestamp/date column. Found columns: {list(df.columns)}"
        )

    # Parse timestamps
    df["timestamp"] = pd.to_datetime(df[timestamp_col])
    if timestamp_col != "timestamp":
        df = df.drop(columns=[timestamp_col])

    # Validate required OHLCV columns
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}. Found: {list(df.columns)}")

    # Add symbol column if provided
    if symbol:
        df["symbol"] = symbol

    # Set timestamp as index and sort
    df = df.set_index("timestamp").sort_index()

    # Keep only OHLCV + symbol columns
    keep_cols = ["open", "high", "low", "close", "volume"]
    if "symbol" in df.columns:
        keep_cols.append("symbol")
    df = df[keep_cols]

    logger.info(
        "historical_csv_loaded",
        filepath=str(filepath),
        rows=len(df),
        date_range=f"{df.index.min()} to {df.index.max()}" if len(df) > 0 else "empty",
    )

    return df


async def load_historical_ibkr(
    symbol: str,
    duration: str = "1 Y",
    bar_size: str = "1 day",
    connection: object | None = None,
) -> pd.DataFrame:
    """Load historical data from IBKR API (placeholder).

    This is a placeholder for the IBKR historical data API integration.
    In production, this would use the ConnectionManager to request
    historical bars from Interactive Brokers.

    Args:
        symbol: The ticker symbol.
        duration: Duration string (e.g. "1 Y", "6 M", "30 D").
        bar_size: Bar size string (e.g. "1 day", "1 hour", "5 mins").
        connection: Optional ConnectionManager instance.

    Returns:
        DataFrame with OHLCV data indexed by timestamp.

    Raises:
        NotImplementedError: Always, until IBKR API integration is complete.
    """
    logger.info(
        "load_historical_ibkr_requested",
        symbol=symbol,
        duration=duration,
        bar_size=bar_size,
    )
    raise NotImplementedError(
        "IBKR historical data loading requires an active connection. "
        "Use load_historical_csv() for local data or implement IBKR API calls."
    )
