#!/usr/bin/env python
"""Monthly strategy rebalance script.

Runs backtests to determine the best strategy per symbol, compares to
the current best_per_symbol mapping, and optionally auto-updates it.

Usage:
    python scripts/rebalance.py                    # Report only
    python scripts/rebalance.py --auto-update      # Update source file
    python scripts/rebalance.py --months 6         # Use 6-month lookback
    python scripts/rebalance.py --alert            # Print prominent warnings for changes
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.backtest import download_yahoo_data, load_strategy, save_csv
from src.backtesting.engine import BacktestEngine
from src.config.settings import BacktestConfig
from src.strategies.implementations.best_per_symbol import DEFAULT_SYMBOL_STRATEGY_MAP

# All 15 symbols from the current mapping
ALL_SYMBOLS = list(DEFAULT_SYMBOL_STRATEGY_MAP.keys())

# Base strategies to evaluate (no meta-strategies)
BASE_STRATEGIES = [
    "momentum",
    "ma_crossover",
    "mean_reversion",
    "bollinger",
    "rsi_divergence",
    "trend_following",
    "breakout",
    "vwap",
]

BEST_PER_SYMBOL_PATH = Path(__file__).parent.parent / "src" / "strategies" / "implementations" / "best_per_symbol.py"


async def run_rebalance(
    symbols: list[str],
    months: int,
    auto_update: bool,
    alert: bool,
) -> None:
    """Run backtests and compare to current mapping."""
    end_date = date.today()
    start_date = end_date - timedelta(days=int(months * 30.44))

    print(f"\n{'='*70}")
    print(f"  MONTHLY REBALANCE CHECK")
    print(f"  Symbols: {len(symbols)} symbols")
    print(f"  Lookback: {months} months ({start_date} to {end_date})")
    print(f"{'='*70}\n")

    # Download data for all symbols
    print("Downloading historical data...")
    all_data = {}
    for symbol in symbols:
        csv_path = Path(f"data/historical/{symbol}.csv")
        if csv_path.exists():
            import pandas as pd

            df = pd.read_csv(csv_path, parse_dates=True, index_col=0)
            df.columns = [c.lower() for c in df.columns]
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            print(f"  Loaded {len(df)} bars for {symbol} from cache")
        else:
            df = download_yahoo_data(symbol, start_date, end_date)
            save_csv(df, symbol)
        all_data[symbol] = df

    # Run each base strategy on each symbol
    print(f"\nRunning backtests ({len(BASE_STRATEGIES)} strategies x {len(symbols)} symbols)...")
    engine = BacktestEngine(
        BacktestConfig(
            slippage_bps=5.0,
            commission_per_share=Decimal("0.005"),
            market_impact_bps=2.0,
        )
    )

    # results[symbol][strategy_name] = sharpe_ratio
    results: dict[str, dict[str, float]] = {s: {} for s in symbols}

    for strategy_name in BASE_STRATEGIES:
        for symbol in symbols:
            try:
                strategy = load_strategy(strategy_name, [symbol], {})
                data = all_data[symbol]
                result = await engine.run(strategy, data, start_date=start_date, end_date=end_date)
                results[symbol][strategy_name] = float(result.sharpe_ratio)
            except Exception as exc:
                results[symbol][strategy_name] = float("-inf")
        print(f"  {strategy_name:<16} done")

    # Determine best strategy per symbol
    recommended: dict[str, str] = {}
    for symbol in symbols:
        best_name = max(results[symbol], key=lambda s: results[symbol][s])
        recommended[symbol] = best_name

    # Compare to current mapping
    current_map = dict(DEFAULT_SYMBOL_STRATEGY_MAP)
    unchanged: list[str] = []
    changed: list[str] = []

    for symbol in symbols:
        current = current_map.get(symbol, "none")
        new = recommended[symbol]
        if current == new:
            unchanged.append(symbol)
        else:
            changed.append(symbol)

    # Print report
    print(f"\n{'='*70}")
    print(f"  REBALANCE REPORT")
    print(f"{'='*70}\n")

    # Table header
    print(f"  {'Symbol':<8} {'Current':<18} {'Recommended':<18} {'Status':<12} {'Sharpe':>7}")
    print(f"  {'─'*8} {'─'*18} {'─'*18} {'─'*12} {'─'*7}")

    for symbol in symbols:
        current = current_map.get(symbol, "none")
        new = recommended[symbol]
        sharpe = results[symbol][new]
        if current == new:
            status = "✓ same"
        else:
            status = "⚠ CHANGED"
        print(f"  {symbol:<8} {current:<18} {new:<18} {status:<12} {sharpe:>7.2f}")

    # Summary
    print(f"\n  {'─'*70}")
    print(f"  Summary: {len(unchanged)} unchanged, {len(changed)} changed")

    if changed:
        print(f"\n  REGIME CHANGES DETECTED:")
        for symbol in changed:
            current = current_map.get(symbol, "none")
            new = recommended[symbol]
            old_sharpe = results[symbol].get(current, float("-inf"))
            new_sharpe = results[symbol][new]
            print(
                f"    {symbol}: {current} (Sharpe {old_sharpe:.2f}) → {new} (Sharpe {new_sharpe:.2f})"
            )

        if alert:
            print(f"\n  {'!'*70}")
            print(f"  !!!  WARNING: {len(changed)} SYMBOL(S) HAVE A NEW BEST STRATEGY  !!!")
            print(f"  !!!  Review the changes above before updating.              !!!")
            print(f"  {'!'*70}")

        # Print recommended new mapping
        print(f"\n  Recommended DEFAULT_SYMBOL_STRATEGY_MAP:")
        print(f"  {{")
        for symbol in symbols:
            print(f'      "{symbol}": "{recommended[symbol]}",')
        print(f"  }}")
    else:
        print(f"\n  ✓ No changes needed — all symbols still have the same best strategy.")

    # Auto-update if requested
    if auto_update and changed:
        _update_source_file(recommended)
        print(f"\n  ✓ AUTO-UPDATED {BEST_PER_SYMBOL_PATH}")
        print(f"    {len(changed)} mapping(s) changed.")
    elif auto_update and not changed:
        print(f"\n  ✓ No update needed — mapping is already optimal.")

    print(f"\n{'='*70}\n")


def _update_source_file(new_map: dict[str, str]) -> None:
    """Update the DEFAULT_SYMBOL_STRATEGY_MAP in best_per_symbol.py."""
    source = BEST_PER_SYMBOL_PATH.read_text()

    # Build the new dict string
    lines = ["DEFAULT_SYMBOL_STRATEGY_MAP: dict[str, str] = {"]
    for symbol, strategy in new_map.items():
        lines.append(f'    "{symbol}": "{strategy}",')
    lines.append("}")
    new_dict_str = "\n".join(lines)

    # Replace the existing dict using regex
    pattern = r"DEFAULT_SYMBOL_STRATEGY_MAP: dict\[str, str\] = \{[^}]+\}"
    if not re.search(pattern, source, re.DOTALL):
        raise RuntimeError("Could not find DEFAULT_SYMBOL_STRATEGY_MAP in source file")

    updated = re.sub(pattern, new_dict_str, source, flags=re.DOTALL)
    BEST_PER_SYMBOL_PATH.write_text(updated)


def main():
    parser = argparse.ArgumentParser(
        description="Monthly strategy rebalance — find best strategy per symbol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/rebalance.py                    # Report only
  python scripts/rebalance.py --auto-update      # Update source file
  python scripts/rebalance.py --months 6         # Use 6-month lookback
  python scripts/rebalance.py --symbols AAPL,MSFT,GOOGL  # Subset of symbols
        """,
    )

    parser.add_argument(
        "--months", "-m", type=int, default=3, help="Lookback period in months (default: 3)"
    )
    parser.add_argument(
        "--symbols", "-s", type=str, default=None, help="Comma-separated symbols (default: all 15)"
    )
    parser.add_argument(
        "--auto-update", action="store_true", help="Automatically update best_per_symbol.py"
    )
    parser.add_argument(
        "--alert", action="store_true", help="Print prominent warning for any changes"
    )

    args = parser.parse_args()

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols
        else ALL_SYMBOLS
    )

    asyncio.run(run_rebalance(
        symbols=symbols,
        months=args.months,
        auto_update=args.auto_update,
        alert=args.alert,
    ))


if __name__ == "__main__":
    main()
