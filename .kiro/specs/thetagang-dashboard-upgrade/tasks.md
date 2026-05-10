# Implementation Plan: ThetaGang Dashboard Upgrade

## Overview

This plan implements the Options Wheel strategy, per-strategy P&L tracking, and multi-page dashboard in dependency order. Backend infrastructure comes first (options chain, signal extension, contract builder), then the Wheel strategy, then P&L tracking, then API endpoints, then database migration, and finally the frontend pages. Property-based tests are placed close to the code they validate.

## Tasks

- [ ] 1. Options infrastructure
  - [ ] 1.1 Create OptionsChainProvider module
    - Create `src/data/options_chain.py` with `OptionContract` dataclass and `OptionsChainProvider` class
    - Implement `get_chain()` using `ib_async.reqSecDefOptParams` and `ib_async.reqTickers` for greeks
    - Implement `get_vix()` to fetch VIX index level
    - Implement TTL-based caching with `invalidate_cache()` method
    - _Requirements: 1.2, 1.3, 4.4_

  - [ ] 1.2 Extend Signal dataclass with option params
    - Add `OptionSignalParams` dataclass to `src/strategies/signals.py` with fields: underlying, strike, expiration, right, action
    - Add optional `option_params: OptionSignalParams | None = None` field to existing `Signal` dataclass
    - _Requirements: 5.4_

  - [ ] 1.3 Create options contract builder
    - Create `src/orders/options_contract.py` with `build_option_contract(params: OptionSignalParams)` function
    - Returns an `ib_async.Option` contract configured for SMART exchange
    - Wire into `StrategyEngine` to call `build_option_contract` when `signal.option_params` is present before passing to `OrderManager.submit_order`
    - _Requirements: 5.3_

- [ ] 2. Wheel strategy implementation
  - [ ] 2.1 Create WheelStrategy class skeleton
    - Create `src/strategies/wheel.py` subclassing `BaseStrategy`
    - Implement `__init__`, `required_indicators`, `validate_capital`, `update_parameters`
    - Define all default parameter constants (target_delta, min/max_dte, roll_dte_threshold, vix thresholds, max_positions_per_symbol)
    - Register with StrategyEngine in `src/strategies/__init__.py`
    - _Requirements: 5.3, 6.1, 6.2, 6.4_

  - [ ] 2.2 Implement put signal generation
    - Implement `_generate_put_signals()` method
    - Check for existing short put positions via `_has_existing_short_position()`
    - Verify buying power with `_calculate_cash_required()`
    - Select strike via `_select_strike_by_delta()` and validate DTE range via `_is_within_dte_range()`
    - Log skip reasons when no valid contract found or insufficient capital
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ] 2.3 Implement covered call signal generation
    - Implement `_generate_call_signals()` and `_get_assigned_shares()` methods
    - Detect assigned shares (>= 100 shares per underlying)
    - Generate SELL CALL signals only for complete lots, skip if existing short call
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ] 2.4 Implement rolling logic
    - Implement `_generate_roll_signals()` method
    - Check positions within roll_dte_threshold window
    - Evaluate net credit/debit for roll; skip if net debit
    - Generate paired BUY_TO_CLOSE + SELL_TO_OPEN signals
    - Log all rolling decisions with position details and credit/debit amounts
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ] 2.5 Implement VIX regime detection
    - Implement `_get_vix_level()` with fallback to last known value
    - Add hysteresis logic: suppress puts above vix_high_threshold, resume below vix_reentry_threshold
    - Never suppress rolling or call-writing signals
    - Handle VIX unavailable case (suppress puts if never received)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ] 2.6 Implement evaluate() orchestration
    - Wire `evaluate()` to call VIX check, then call signals, then roll signals, then put signals in order
    - Integrate with Capital_Allocator: request allocation and bound total notional
    - Return combined signal list
    - _Requirements: 5.1, 5.2_

  - [ ]* 2.7 Write property tests for put signal generation (Property 1)
    - **Property 1: Put signal generation requires preconditions**
    - Test that SELL PUT signals are generated iff: no existing short put, sufficient buying power, VIX not suppressed
    - Use Hypothesis strategies to generate random symbol lists, buying power levels, and VIX states
    - **Validates: Requirements 1.1, 1.5, 4.1**

  - [ ]* 2.8 Write property tests for call signal generation (Property 2)
    - **Property 2: Call signal generation requires assignment and lot completeness**
    - Test that SELL CALL signals generated iff shares >= 100 and no existing short call; contracts = floor(shares/100)
    - **Validates: Requirements 2.1, 2.4, 2.5**

  - [ ]* 2.9 Write property tests for strike selection (Property 3)
    - **Property 3: Option strike selection minimizes delta distance**
    - Generate random option chains with varying deltas; verify selected strike minimizes |delta - target_delta|
    - **Validates: Requirements 1.2, 2.2**

  - [ ]* 2.10 Write property tests for DTE range (Property 4)
    - **Property 4: Expiration selection stays within DTE range**
    - Generate random chains with various expirations; verify selected expiration satisfies min_dte <= DTE <= max_dte
    - **Validates: Requirements 1.3, 1.4, 2.3**

  - [ ]* 2.11 Write property tests for rolling logic (Property 5)
    - **Property 5: Roll signals require net credit**
    - Generate positions within rolling window with varying premium scenarios; verify roll only when net credit
    - **Validates: Requirements 3.1, 3.2, 3.3**

  - [ ]* 2.12 Write property tests for VIX regime (Property 6)
    - **Property 6: VIX regime detection with hysteresis**
    - Generate VIX sequences; verify suppression/resumption follows hysteresis thresholds
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [ ]* 2.13 Write property tests for capital bounds (Property 7)
    - **Property 7: Signal notional bounded by capital allocation**
    - Generate random allocations and signal sets; verify total notional never exceeds allocation
    - **Validates: Requirements 5.1**

  - [ ]* 2.14 Write property tests for parameter hot-reload (Property 8)
    - **Property 8: Parameter hot-reload round trip**
    - Generate random parameter dicts; verify update_parameters() sets values correctly and missing keys retain previous values
    - **Validates: Requirements 6.3, 6.4**

- [ ] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. P&L tracker implementation
  - [ ] 4.1 Create PnLTracker class
    - Create `src/portfolio/pnl_tracker.py` with `StrategyPnL`, `EquityPoint`, `TradeDetail` dataclasses
    - Implement `PnLTracker.__init__`, `start()`, `stop()` with asyncio periodic update loop
    - Implement `get_strategy_pnl()` and `get_all_strategies_pnl()` computing realized + unrealized P&L
    - _Requirements: 7.1, 7.2, 7.3_

  - [ ] 4.2 Implement trade recording and equity snapshots
    - Implement `record_trade_close()` to persist closed trades to database
    - Implement `record_daily_snapshot()` to write end-of-day per-strategy equity
    - Implement `get_equity_history()` with optional date range filtering
    - Implement `get_trades()` with strategy, symbol, date range filters and pagination
    - _Requirements: 7.1, 8.1, 8.2, 8.3, 8.4_

  - [ ]* 4.3 Write property tests for P&L computation (Property 9)
    - **Property 9: Realized P&L equals sum of closed trade profits**
    - Generate random sets of closed trades; verify realized P&L = sum((exit - entry) * qty - commission)
    - **Validates: Requirements 7.1**

  - [ ]* 4.4 Write property tests for unrealized P&L (Property 10)
    - **Property 10: Unrealized P&L equals mark-to-market of open positions**
    - Generate open positions with random market prices; verify unrealized = sum((current - entry) * qty)
    - **Validates: Requirements 7.2**

  - [ ]* 4.5 Write property tests for date range filtering (Property 11)
    - **Property 11: History endpoint respects date range filter**
    - Generate equity points with random dates; verify all returned points satisfy start <= date <= end
    - **Validates: Requirements 8.2**

  - [ ]* 4.6 Write property tests for pagination (Property 12)
    - **Property 12: Pagination returns correct slice**
    - Generate N records; verify query(limit=L, offset=O) returns min(L, N-O) records from position O
    - **Validates: Requirements 8.3, 12.5**

  - [ ]* 4.7 Write property tests for trade record completeness (Property 13)
    - **Property 13: Closed trade records contain all required fields**
    - Generate closed trades; verify all required fields are non-null after persistence
    - **Validates: Requirements 8.4**

- [ ] 5. Database migration
  - [ ] 5.1 Add SQLAlchemy models for options trades and strategy snapshots
    - Add `OptionsTradeRecord` and `StrategySnapshotRecord` to `src/persistence/models.py`
    - Include all columns, indexes, and unique constraints per design
    - _Requirements: 8.1, 8.4_

  - [ ] 5.2 Create Alembic migration
    - Generate migration at `alembic/versions/002_options_trades_and_snapshots.py`
    - Create `options_trades` and `strategy_snapshots` tables with indexes
    - _Requirements: 8.1, 8.4_

- [ ] 6. New API endpoints
  - [ ] 6.1 Add strategy P&L and comparison endpoints
    - Add `GET /api/strategies/{name}/pnl` returning `StrategyPnLResponse`
    - Add `GET /api/strategies/comparison` returning `StrategyComparisonResponse` with metrics for all strategies
    - Add Pydantic response models to `src/dashboard/api.py`
    - _Requirements: 7.4, 7.5_

  - [ ] 6.2 Add history and trades endpoints
    - Add `GET /api/strategies/{name}/history?start=&end=` returning equity curve time-series
    - Add `GET /api/strategies/{name}/trades?limit=&offset=` returning paginated trades for one strategy
    - Add `GET /api/trades?strategy=&symbol=&start=&end=&limit=&offset=` returning all trades with filters
    - Add `PaginatedTradesResponse`, `EquityPointResponse`, `TradeDetailResponse` models
    - _Requirements: 8.2, 8.3, 12.1, 12.2_

  - [ ]* 6.3 Write property tests for trade ordering and filters (Properties 14, 15)
    - **Property 14: Trade history is ordered by date descending**
    - **Property 15: Trade filters produce matching results**
    - Generate trade collections; verify ordering and filter correctness
    - **Validates: Requirements 12.1, 12.3**

- [ ] 7. Checkpoint - Ensure all backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Frontend multi-page routing
  - [ ] 8.1 Install react-router-dom and set up routing
    - Add `react-router-dom` dependency to `dashboard-ui/package.json`
    - Create `dashboard-ui/src/components/Layout.tsx` with `<Outlet />` for nested routes
    - Create `dashboard-ui/src/components/NavBar.tsx` with links to /, /strategies, /trades
    - Update `dashboard-ui/src/App.tsx` to use `BrowserRouter`, `Routes`, and `Route` components
    - _Requirements: 9.1, 9.2, 9.3_

  - [ ] 8.2 Extract OverviewPage from existing App content
    - Create `dashboard-ui/src/pages/OverviewPage.tsx`
    - Move existing dashboard content (PortfolioSummaryPanel, PositionsTable, RiskGauges, StrategyStatusPanel, PerformanceCharts, OrderHistory) into OverviewPage
    - Mount at root route (/)
    - _Requirements: 9.4_

- [ ] 9. Frontend pages
  - [ ] 9.1 Add TypeScript interfaces for new API responses
    - Add `StrategyPnL`, `StrategyComparison`, `EquityPoint`, `TradeDetail`, `PaginatedResponse<T>` interfaces to `dashboard-ui/src/types.ts`
    - _Requirements: 7.4, 7.5, 8.2, 8.3_

  - [ ] 9.2 Implement Strategy Comparison page
    - Create `dashboard-ui/src/pages/StrategyComparisonPage.tsx`
    - Create `dashboard-ui/src/components/ComparisonTable.tsx` with columns: total return, Sharpe, Sortino, max drawdown, win rate, profit factor, total trades
    - Create `dashboard-ui/src/components/EquityCurveOverlay.tsx` using Recharts to overlay all strategy equity curves
    - Apply green/red color coding based on total_return sign
    - Link strategy names to `/strategies/:name` detail page
    - Fetch data from `GET /api/strategies/comparison`
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [ ] 9.3 Implement Strategy Detail page
    - Create `dashboard-ui/src/pages/StrategyDetailPage.tsx`
    - Create `dashboard-ui/src/components/EquityCurveChart.tsx` using Recharts (time x-axis, cumulative P&L y-axis)
    - Create `dashboard-ui/src/components/MetricsPanel.tsx` for key metrics display
    - Create `dashboard-ui/src/components/StrategyParamsDisplay.tsx` for configuration parameters
    - Display open positions table, recent trades table
    - Fetch from `/api/strategies/:name/pnl`, `/api/strategies/:name/history`, `/api/strategies/:name/trades`
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [ ] 9.4 Implement Trade History page
    - Create `dashboard-ui/src/pages/TradeHistoryPage.tsx`
    - Create `dashboard-ui/src/components/TradeFilters.tsx` with strategy dropdown, date range pickers, symbol text input
    - Create `dashboard-ui/src/components/TradeTable.tsx` displaying: strategy, symbol, direction, entry/exit price, quantity, realized P&L, date
    - Create `dashboard-ui/src/components/Pagination.tsx` with configurable page size (default 25)
    - Fetch from `GET /api/trades` with filter query params; update without full page reload
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [ ]* 9.5 Write property test for return-based color coding (Property 16)
    - **Property 16: Return-based color coding**
    - Generate strategy data with random total_return values; verify green class when > 0, red class when < 0
    - **Validates: Requirements 10.3**

- [ ] 10. Integration and wiring
  - [ ] 10.1 Wire PnLTracker into application lifecycle
    - Instantiate `PnLTracker` in `src/main.py` with `PortfolioMonitor` and DB session factory
    - Start/stop tracker with application lifecycle
    - Subscribe to trade close events from `OrderManager` to call `record_trade_close()`
    - Schedule `record_daily_snapshot()` at market close
    - _Requirements: 7.3, 8.1_

  - [ ] 10.2 Wire WheelStrategy into StrategyEngine
    - Register WheelStrategy in strategy factory/config loading
    - Add `strategies.wheel` section to `config.example.yaml` with all parameters and defaults
    - Verify StrategyEngine dispatches options signals through `build_option_contract` to OrderManager
    - _Requirements: 5.3, 6.1_

  - [ ]* 10.3 Write integration tests for strategy-to-order pipeline
    - Test WheelStrategy registration, evaluate cycle, signal routing through StrategyEngine to OrderManager
    - Test RiskManager validation of options signals
    - _Requirements: 5.2, 5.3_

  - [ ]* 10.4 Write integration tests for API endpoints with test database
    - Test full request/response cycle for all new endpoints
    - Test PnLTracker snapshot recording and trade querying with real SQLAlchemy session
    - _Requirements: 7.4, 7.5, 8.2, 8.3_

- [ ] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The frontend uses the existing `@tanstack/react-query` pattern (`useApi` hooks) for data fetching
- `react-router-dom` is the only new frontend runtime dependency
- All backend code is Python; all frontend code is TypeScript/React

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "5.1"] },
    { "id": 1, "tasks": ["1.3", "5.2", "9.1"] },
    { "id": 2, "tasks": ["2.1", "8.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "2.4", "2.5", "8.2"] },
    { "id": 4, "tasks": ["2.6", "4.1"] },
    { "id": 5, "tasks": ["2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13", "2.14", "4.2"] },
    { "id": 6, "tasks": ["4.3", "4.4", "4.5", "4.6", "4.7", "6.1", "6.2"] },
    { "id": 7, "tasks": ["6.3", "9.2", "9.3", "9.4"] },
    { "id": 8, "tasks": ["9.5", "10.1", "10.2"] },
    { "id": 9, "tasks": ["10.3", "10.4"] }
  ]
}
```
