#!/usr/bin/env python
"""Standalone backtesting runner.

Downloads historical data from Yahoo Finance, runs any strategy over a
configurable date range, and prints results. Completely separate from the
live trading bot — no IBKR connection needed.

Usage:
    python scripts/backtest.py --symbol AAPL --strategy momentum --years 2
    python scripts/backtest.py --symbol SPY,QQQ --strategy ma_crossover --years 5
    python scripts/backtest.py --symbol MSFT --strategy mean_reversion --start 2020-01-01 --end 2024-12-31
    python scripts/backtest.py --symbol AAPL --strategy bollinger --years 3 --params bb_period=30,bb_std=2.5
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtesting.engine import BacktestEngine
from src.config.settings import BacktestConfig, StrategyConfig


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES = {
    "momentum": {
        "class": "src.strategies.implementations.momentum.MomentumStrategy",
        "defaults": {"lookback_period": 14, "momentum_threshold": 0.02},
    },
    "ma_crossover": {
        "class": "src.strategies.implementations.ma_crossover.MACrossoverStrategy",
        "defaults": {"fast_period": 10, "slow_period": 30, "ma_type": "ema"},
    },
    "mean_reversion": {
        "class": "src.strategies.implementations.mean_reversion.MeanReversionStrategy",
        "defaults": {"lookback_period": 20, "z_score_threshold": 2.0},
    },
    "bollinger": {
        "class": "src.strategies.implementations.bollinger.BollingerStrategy",
        "defaults": {"bb_period": 20, "bb_std": 2.0, "entry_band": "both"},
    },
    "rsi_divergence": {
        "class": "src.strategies.implementations.rsi_divergence.RSIDivergenceStrategy",
        "defaults": {"rsi_period": 14, "overbought": 70, "oversold": 30},
    },
    "trend_following": {
        "class": "src.strategies.implementations.trend_following.TrendFollowingStrategy",
        "defaults": {"fast_ma": 10, "slow_ma": 30, "atr_filter": 0.01},
    },
    "breakout": {
        "class": "src.strategies.implementations.breakout.BreakoutStrategy",
        "defaults": {"consolidation_period": 20, "breakout_atr_multiple": 1.5},
    },
    "vwap": {
        "class": "src.strategies.implementations.vwap.VWAPStrategy",
        "defaults": {"deviation_threshold": 0.02, "session_type": "regular"},
    },
}


# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------


def download_yahoo_data(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Download historical OHLCV data from Yahoo Finance.

    Uses yfinance if installed, otherwise falls back to direct URL download.
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start.isoformat(), end=end.isoformat())
        if df.empty:
            raise ValueError(f"No data returned for {symbol}")
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]]
        # Strip timezone info to avoid tz-naive vs tz-aware comparison issues
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        print(f"  Downloaded {len(df)} bars for {symbol} via yfinance")
        return df

    except ImportError:
        print(f"  yfinance not installed, using direct download for {symbol}...")
        return _download_yahoo_direct(symbol, start, end)


def _download_yahoo_direct(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Download from Yahoo Finance using direct HTTP (no yfinance dependency)."""
    import io
    import time
    import urllib.request

    period1 = int(time.mktime(start.timetuple()))
    period2 = int(time.mktime(end.timetuple()))

    url = (
        f"https://query1.finance.yahoo.com/v7/finance/download/{symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history"
    )

    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            csv_data = response.read().decode("utf-8")
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download data for {symbol}. "
            f"Install yfinance for better reliability: pip install yfinance\n"
            f"Error: {exc}"
        ) from exc

    df = pd.read_csv(io.StringIO(csv_data), parse_dates=["Date"], index_col="Date")
    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]]
    df = df.dropna()

    if df.empty:
        raise ValueError(f"No data returned for {symbol}")

    print(f"  Downloaded {len(df)} bars for {symbol} via direct download")
    return df


def save_csv(df: pd.DataFrame, symbol: str) -> Path:
    """Save downloaded data to data/historical/ for future use."""
    data_dir = Path("data/historical")
    data_dir.mkdir(parents=True, exist_ok=True)
    filepath = data_dir / f"{symbol}.csv"
    df.to_csv(filepath)
    return filepath


# ---------------------------------------------------------------------------
# Strategy loader
# ---------------------------------------------------------------------------


def load_strategy(name: str, symbols: list[str], params: dict):
    """Dynamically load a strategy class and instantiate it."""
    if name not in STRATEGIES:
        available = ", ".join(sorted(STRATEGIES.keys()))
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")

    entry = STRATEGIES[name]
    module_path, class_name = entry["class"].rsplit(".", 1)

    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    merged_params = {**entry["defaults"], **params}

    config = StrategyConfig(
        enabled=True,
        frequency="daily",
        symbols=symbols,
        asset_classes=["equity"],
        parameters=merged_params,
    )

    return cls(config=config, data_hub=None)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


async def run_backtest(
    symbols: list[str],
    strategy_name: str,
    start: date,
    end: date,
    params: dict,
    slippage_bps: float = 5.0,
    save_data: bool = True,
) -> None:
    """Run a backtest and print results."""
    print(f"\n{'='*60}")
    print(f"  BACKTEST: {strategy_name.upper()}")
    print(f"  Symbols: {', '.join(symbols)}")
    print(f"  Period: {start} to {end} ({(end - start).days} days)")
    print(f"  Slippage: {slippage_bps} bps")
    print(f"{'='*60}\n")

    # Download data
    print("Downloading historical data...")
    all_data = {}
    for symbol in symbols:
        csv_path = Path(f"data/historical/{symbol}.csv")
        if csv_path.exists():
            df = pd.read_csv(csv_path, parse_dates=True, index_col=0)
            df.columns = [c.lower() for c in df.columns]
            # Strip timezone if present
            if hasattr(df.index, 'tz') and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            print(f"  Loaded {len(df)} bars for {symbol} from cache")
        else:
            df = download_yahoo_data(symbol, start, end)
            if save_data:
                save_csv(df, symbol)
                print(f"  Saved to data/historical/{symbol}.csv")
        all_data[symbol] = df

    # Load strategy
    print(f"\nLoading strategy: {strategy_name}")
    strategy = load_strategy(strategy_name, symbols, params)
    print(f"  Parameters: {strategy.config.parameters}")

    # Run backtest
    engine = BacktestEngine(BacktestConfig(
        slippage_bps=slippage_bps,
        commission_per_share=Decimal("0.005"),
        market_impact_bps=2.0,
    ))

    if len(symbols) == 1:
        data = all_data[symbols[0]]
        result = await engine.run(strategy, data, start_date=start, end_date=end)

        print(f"\n{'─'*60}")
        print(f"  RESULTS")
        print(f"{'─'*60}")
        print(f"  Total Return:      {result.total_return:>10.2%}")
        print(f"  Annualized Return: {result.annualized_return:>10.2%}")
        print(f"  Sharpe Ratio:      {result.sharpe_ratio:>10.2f}")
        print(f"  Sortino Ratio:     {result.sortino_ratio:>10.2f}")
        print(f"  Max Drawdown:      {result.max_drawdown:>10.2%}")
        print(f"  Win Rate:          {result.win_rate:>10.1%}")
        print(f"  Profit Factor:     {result.profit_factor:>10.2f}")
        print(f"  Total Trades:      {result.total_trades:>10d}")
        if result.avg_trade_duration:
            print(f"  Avg Trade Duration:{result.avg_trade_duration.days:>8d} days")
        print(f"{'─'*60}")

        results_dir = Path("data/backtest_results")
        results_dir.mkdir(parents=True, exist_ok=True)
        result_path = engine.store_result(result)
        print(f"\n  Result saved to: {result_path}")
    else:
        allocations = {strategy.name: Decimal(str(100000 // len(symbols)))}
        result = await engine.run_portfolio(
            strategies=[strategy],
            allocations=allocations,
            data=all_data,
            start_date=start,
            end_date=end,
        )

        print(f"\n{'─'*60}")
        print(f"  PORTFOLIO RESULTS")
        print(f"{'─'*60}")
        print(f"  Total Return:      {result.total_return:>10.2%}")
        print(f"  Annualized Return: {result.annualized_return:>10.2%}")
        print(f"  Sharpe Ratio:      {result.sharpe_ratio:>10.2f}")
        print(f"  Max Drawdown:      {result.max_drawdown:>10.2%}")
        print(f"{'─'*60}")

    print("\nDone.\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_params(params_str: str | None) -> dict:
    """Parse comma-separated key=value params into a dict."""
    if not params_str:
        return {}
    result = {}
    for pair in params_str.split(","):
        key, value = pair.strip().split("=", 1)
        try:
            result[key.strip()] = int(value.strip())
        except ValueError:
            try:
                result[key.strip()] = float(value.strip())
            except ValueError:
                result[key.strip()] = value.strip()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Run backtests with automatic data download",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/backtest.py --symbol AAPL --strategy momentum --years 2
  python scripts/backtest.py --symbol SPY,QQQ,IWM --strategy ma_crossover --years 5
  python scripts/backtest.py --symbol MSFT --strategy bollinger --start 2020-01-01 --end 2024-12-31
  python scripts/backtest.py --symbol AAPL --strategy momentum --years 3 --params lookback_period=20

Available strategies:
  momentum, ma_crossover, mean_reversion, bollinger, rsi_divergence,
  trend_following, breakout, vwap
        """,
    )

    parser.add_argument("--symbol", "-s", required=True, help="Comma-separated tickers (e.g. AAPL,MSFT)")
    parser.add_argument("--strategy", "-t", required=True, choices=list(STRATEGIES.keys()), help="Strategy to test")
    parser.add_argument("--years", "-y", type=float, default=None, help="How many years back (e.g. 2)")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD (overrides --years)")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--params", "-p", type=str, default=None, help="Params as key=value,key=value")
    parser.add_argument("--slippage", type=float, default=5.0, help="Slippage in bps (default: 5)")
    parser.add_argument("--no-save", action="store_true", help="Don't cache downloaded CSVs")

    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbol.split(",")]
    end_date = date.fromisoformat(args.end) if args.end else date.today()

    if args.start:
        start_date = date.fromisoformat(args.start)
    elif args.years:
        start_date = end_date - timedelta(days=int(args.years * 365.25))
    else:
        start_date = end_date - timedelta(days=365)

    params = parse_params(args.params)

    asyncio.run(run_backtest(
        symbols=symbols,
        strategy_name=args.strategy,
        start=start_date,
        end=end_date,
        params=params,
        slippage_bps=args.slippage,
        save_data=not args.no_save,
    ))


if __name__ == "__main__":
    main()
