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
    "adaptive": {
        "class": "src.strategies.implementations.adaptive.AdaptiveStrategy",
        "defaults": {"lookback_window": 60, "rebalance_period": 20, "min_sharpe_threshold": 0.0},
    },
    "regime_hmm": {
        "class": "src.strategies.implementations.regime_hmm.RegimeHMMStrategy",
        "defaults": {"hmm_lookback": 120, "volatility_window": 14, "trend_window": 14, "vol_threshold": 0.03},
    },
    "bandit": {
        "class": "src.strategies.implementations.bandit.BanditStrategy",
        "defaults": {"gamma": 0.1, "switching_cost": 0.02, "min_rounds": 10},
    },
    "best_per_symbol": {
        "class": "src.strategies.implementations.best_per_symbol.BestPerSymbolStrategy",
        "defaults": {},
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
  python scripts/backtest.py --symbol AAPL --compare-all --years 2

Available strategies:
  momentum, ma_crossover, mean_reversion, bollinger, rsi_divergence,
  trend_following, breakout, vwap
        """,
    )

    parser.add_argument("--symbol", "-s", required=True, help="Comma-separated tickers (e.g. AAPL,MSFT)")
    parser.add_argument("--strategy", "-t", choices=list(STRATEGIES.keys()), help="Strategy to test")
    parser.add_argument("--compare-all", "-c", action="store_true", help="Run ALL strategies and show comparison")
    parser.add_argument("--years", "-y", type=float, default=None, help="How many years back (e.g. 2)")
    parser.add_argument("--months", "-m", type=float, default=None, help="How many months back (e.g. 1)")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD (overrides --years/--months)")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--params", "-p", type=str, default=None, help="Params as key=value,key=value")
    parser.add_argument("--slippage", type=float, default=5.0, help="Slippage in bps (default: 5)")
    parser.add_argument("--no-save", action="store_true", help="Don't cache downloaded CSVs")

    args = parser.parse_args()

    if not args.strategy and not args.compare_all:
        parser.error("Either --strategy or --compare-all is required")

    symbols = [s.strip().upper() for s in args.symbol.split(",")]
    end_date = date.fromisoformat(args.end) if args.end else date.today()

    if args.start:
        start_date = date.fromisoformat(args.start)
    elif args.years:
        start_date = end_date - timedelta(days=int(args.years * 365.25))
    elif args.months:
        start_date = end_date - timedelta(days=int(args.months * 30.44))
    else:
        start_date = end_date - timedelta(days=365)

    if args.compare_all:
        asyncio.run(run_compare_all(
            symbols=symbols,
            start=start_date,
            end=end_date,
            slippage_bps=args.slippage,
            save_data=not args.no_save,
        ))
    else:
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


async def run_compare_all(
    symbols: list[str],
    start: date,
    end: date,
    slippage_bps: float = 5.0,
    save_data: bool = True,
) -> None:
    """Run ALL strategies on ALL symbols and print a comparison matrix."""
    print(f"\n{'='*70}")
    print(f"  STRATEGY x SYMBOL COMPARISON")
    print(f"  Symbols: {', '.join(symbols)}")
    print(f"  Period: {start} to {end} ({(end - start).days} days)")
    print(f"{'='*70}\n")

    # Download data for all symbols
    print("Downloading historical data...")
    all_data = {}
    for symbol in symbols:
        csv_path = Path(f"data/historical/{symbol}.csv")
        if csv_path.exists():
            df = pd.read_csv(csv_path, parse_dates=True, index_col=0)
            df.columns = [c.lower() for c in df.columns]
            if hasattr(df.index, 'tz') and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            print(f"  Loaded {len(df)} bars for {symbol} from cache")
        else:
            df = download_yahoo_data(symbol, start, end)
            if save_data:
                save_csv(df, symbol)
        all_data[symbol] = df

    # Run each strategy on each symbol
    engine = BacktestEngine(BacktestConfig(
        slippage_bps=slippage_bps,
        commission_per_share=Decimal("0.005"),
        market_impact_bps=2.0,
    ))

    # results[strategy_name][symbol] = BacktestResult
    results: dict[str, dict[str, object]] = {}
    for strategy_name in STRATEGIES:
        results[strategy_name] = {}
        for symbol in symbols:
            try:
                strategy = load_strategy(strategy_name, [symbol], {})
                data = all_data[symbol]
                result = await engine.run(strategy, data, start_date=start, end_date=end)
                results[strategy_name][symbol] = result
            except Exception:
                results[strategy_name][symbol] = None

        # Progress indicator
        done_count = sum(1 for r in results[strategy_name].values() if r is not None)
        print(f"  {strategy_name:<16} — {done_count}/{len(symbols)} symbols completed")

    # Print per-symbol summary
    print(f"\n\n{'='*70}")
    print(f"  RESULTS BY SYMBOL")
    print(f"{'='*70}")

    for symbol in symbols:
        print(f"\n  ┌─ {symbol} {'─'*(60-len(symbol))}")
        print(f"  │ {'Strategy':<16} {'Return':>8} {'Sharpe':>7} {'MaxDD':>7} {'Trades':>7}")
        print(f"  │ {'─'*16} {'─'*8} {'─'*7} {'─'*7} {'─'*7}")

        for strategy_name in STRATEGIES:
            result = results[strategy_name][symbol]
            if result is None:
                print(f"  │ {strategy_name:<16} {'ERROR':>8}")
            else:
                print(
                    f"  │ {strategy_name:<16} "
                    f"{result.total_return:>7.2%} "
                    f"{result.sharpe_ratio:>7.2f} "
                    f"{result.max_drawdown:>6.2%} "
                    f"{result.total_trades:>7d}"
                )
        print(f"  └{'─'*65}")

    # Print overall ranking (average Sharpe across all symbols)
    print(f"\n\n{'='*70}")
    print(f"  OVERALL RANKING (avg Sharpe across {len(symbols)} symbols)")
    print(f"{'='*70}\n")

    strategy_scores = []
    for strategy_name in STRATEGIES:
        sharpes = []
        returns = []
        for symbol in symbols:
            result = results[strategy_name][symbol]
            if result is not None:
                sharpes.append(float(result.sharpe_ratio))
                returns.append(float(result.total_return))
        if sharpes:
            avg_sharpe = sum(sharpes) / len(sharpes)
            avg_return = sum(returns) / len(returns)
            strategy_scores.append((strategy_name, avg_sharpe, avg_return))

    strategy_scores.sort(key=lambda x: x[1], reverse=True)

    print(f"  {'#':<4} {'Strategy':<16} {'Avg Sharpe':>11} {'Avg Return':>11}")
    print(f"  {'─'*4} {'─'*16} {'─'*11} {'─'*11}")
    for i, (name, sharpe, ret) in enumerate(strategy_scores, 1):
        marker = " ★ BEST" if i == 1 else ""
        print(f"  {i:<4} {name:<16} {sharpe:>11.2f} {ret:>10.2%}{marker}")

    # Best strategy per symbol
    print(f"\n  {'─'*70}")
    print(f"  BEST STRATEGY PER SYMBOL:")
    for symbol in symbols:
        best_name = None
        best_sharpe = -999
        for strategy_name in STRATEGIES:
            result = results[strategy_name][symbol]
            if result is not None and float(result.sharpe_ratio) > best_sharpe:
                best_sharpe = float(result.sharpe_ratio)
                best_name = strategy_name
        if best_name:
            result = results[best_name][symbol]
            print(f"    {symbol:<6} → {best_name} (Sharpe: {best_sharpe:.2f}, Return: {result.total_return:.2%})")

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
