# Implementation Tasks

## Task Dependency Graph

```
Task 1 (Project Scaffold)
├── Task 2 (Configuration System)
│   ├── Task 3 (Connection Manager)
│   │   ├── Task 4 (Market Data Hub)
│   │   │   ├── Task 7 (Strategy Engine + Base Strategy)
│   │   │   │   ├── Task 8 (Strategy Implementations - Batch 1)
│   │   │   │   ├── Task 9 (Strategy Implementations - Batch 2)
│   │   │   │   └── Task 10 (Strategy Implementations - Batch 3)
│   │   ├── Task 5 (Order Manager)
│   │   └── Task 6 (Portfolio Monitor)
│   ├── Task 11 (Capital Allocator)
│   ├── Task 12 (Risk Manager)
│   │   └── Task 13 (Stop-Loss & VaR)
│   └── Task 14 (Alert Service)
├── Task 15 (Backtesting Engine)
├── Task 16 (Database & Persistence)
├── Task 17 (Dashboard API)
├── Task 18 (Dashboard Frontend)
├── Task 19 (Docker Deployment)
├── Task 20 (Integration & Orchestration)
└── Task 21 (Property-Based Tests)
```

## Tasks

### Task 1: Project Scaffold & Dependencies

**Requirements:** R1, R12, R14
**Dependencies:** None

Set up the Python project structure, dependency management, and base configuration.

**Sub-tasks:**
- [x] Create project directory structure matching design doc layout (`src/`, `tests/`, `data/`, `dashboard-ui/`)
- [x] Create `pyproject.toml` with all dependencies: ib_async, asyncio, sqlalchemy[asyncio], asyncpg, redis, fastapi, uvicorn, pydantic, pydantic-settings, pyyaml, structlog, pandas, numpy, scipy, hypothesis, pytest, pytest-asyncio, ruff, alembic, httpx, aiosmtplib, watchfiles
- [x] Create `src/__init__.py` and `src/main.py` entry point with asyncio event loop setup
- [x] Create `src/utils/__init__.py` and `src/utils/logging.py` with structlog configuration (JSON output, log levels, rotation)
- [x] Create `.env.example` with all environment variable placeholders
- [x] Create `config.example.yaml` with documented defaults for all settings
- [x] Create `pytest.ini` / `conftest.py` with async test fixtures
- [x] Verify: `pip install -e .` succeeds, `pytest` runs (0 tests collected is fine)

---

### Task 2: Configuration System

**Requirements:** R12
**Dependencies:** Task 1

Implement the Pydantic-based configuration system with YAML loading, validation, and hot-reload.

**Sub-tasks:**
- [x] Create `src/config/__init__.py`
- [x] Create `src/config/settings.py` with Pydantic `BaseSettings` models: `ConnectionConfig`, `StrategyConfig`, `RiskConfig`, `CapitalConfig`, `AlertConfig`, `BacktestConfig`, `DatabaseConfig`, `DashboardConfig`, root `Settings`
- [x] Implement YAML file loading via `pydantic-settings` yaml support
- [x] Implement environment variable overrides with `TRADING_BOT_` prefix and `__` nested delimiter
- [x] Implement validation: reject invalid configs with descriptive error messages, refuse to start on missing required values
- [x] Create `src/config/watcher.py` with `ConfigWatcher` class using `watchfiles` for hot-reload of strategy parameters
- [x] Write tests: `tests/unit/test_config.py` — valid config loads, invalid config raises, env vars override, missing required rejects startup
- [x] Verify: tests pass, config loads from example yaml

---

### Task 3: Connection Manager

**Requirements:** R1, R8
**Dependencies:** Task 2

Implement IBKR connectivity via ib_async with reconnection logic and account verification.

**Sub-tasks:**
- [x] Create `src/connection/__init__.py`
- [x] Create `src/connection/manager.py` with `ConnectionManager` class
- [x] Implement `connect()` — establish socket connection to TWS or IB Gateway based on config (host, port, client_id)
- [x] Implement `disconnect()` — graceful disconnection
- [x] Implement `_on_disconnected()` — exponential backoff reconnection (base 2s, max 5 retries)
- [x] Implement `_verify_account()` — check account permissions, log account type (paper/live)
- [x] Implement heartbeat monitoring to maintain connection
- [x] Implement `subscribe_market_data()`, `subscribe_account_updates()` for concurrent subscriptions
- [x] On all retries exhausted: emit event for Alert_Service, set strategies to halted state
- [x] Write tests: `tests/unit/test_connection.py` — mock ib_async, test reconnection logic, test account verification
- [x] Verify: unit tests pass

---

### Task 4: Market Data Hub

**Requirements:** R5, R13
**Dependencies:** Task 3

Implement real-time market data aggregation, bar building at multiple timeframes, and stale data detection.

**Sub-tasks:**
- [x] Create `src/data/__init__.py`
- [x] Create `src/data/market_data_hub.py` with `MarketDataHub` class
- [x] Create `src/data/bar_builder.py` with `BarBuilder` class — aggregate ticks into OHLCV bars at configurable timeframes (tick, 1min, 5min, 15min, 1hour, daily, weekly)
- [x] Implement `subscribe()` — subscribe to market data for a symbol/asset class via ConnectionManager
- [x] Implement `on_tick()` — process incoming ticks, update bar builders, cache latest in Redis
- [x] Implement `get_latest_bar()` — return latest completed bar for symbol/timeframe
- [x] Implement `get_history()` — return N historical bars from cache or IBKR historical data API
- [x] Implement `_detect_stale_data()` — flag symbols with no updates beyond configurable threshold
- [x] Create `src/data/historical.py` — load historical data from IBKR API or local CSV files
- [x] Define `Timeframe` enum and `Bar` dataclass
- [x] Write tests: `tests/unit/test_market_data.py` — bar builder aggregation, stale detection, history retrieval
- [x] Verify: unit tests pass

---

### Task 5: Order Manager

**Requirements:** R9, R13
**Dependencies:** Task 3

Implement order lifecycle management with rate limiting, timeout handling, and audit trail.

**Sub-tasks:**
- [x] Create `src/orders/__init__.py`
- [x] Create `src/orders/manager.py` with `OrderManager` class
- [x] Create `src/orders/rate_limiter.py` with `RateLimiter` class (token bucket, 45 msg/sec with buffer below IBKR's 50)
- [x] Implement `submit_order()` — translate Signal into IBKR order, apply rate limiting, submit via ConnectionManager
- [x] Support order types: market, limit, stop, stop-limit, trailing stop, bracket (via `ib_async.bracketOrder()`)
- [x] Implement `on_order_status()` — track through all IBKR states (submitted, accepted, filled, partially filled, cancelled, rejected)
- [x] Implement `on_fill()` — update position, notify CapitalAllocator and PortfolioMonitor
- [x] Implement `cancel_order()` and `cancel_stale_orders()` — timeout logic (60s market, 5min limit, configurable)
- [x] On rejection: log reason, notify Strategy_Engine to update internal state
- [x] Use IBKR SmartRouting by default, allow exchange override
- [x] Persist all orders to database (audit trail with timestamps, fills, strategy association)
- [x] Define `ManagedOrder` dataclass, `OrderType` enum, `OrderStatus` enum
- [x] Write tests: `tests/unit/test_orders.py` — rate limiting, timeout cancellation, state transitions, partial fills
- [x] Verify: unit tests pass

---

### Task 6: Portfolio Monitor

**Requirements:** R10
**Dependencies:** Task 3

Implement real-time portfolio tracking, per-strategy metrics, and reporting.

**Sub-tasks:**
- [x] Create `src/portfolio/__init__.py`
- [x] Create `src/portfolio/monitor.py` with `PortfolioMonitor` class
- [x] Implement `sync_positions()` — reconcile internal state with IBKR account positions and open orders
- [x] Implement `get_total_value()`, `get_unrealized_pnl()`, `get_peak_equity()`
- [x] Implement `calculate_strategy_metrics()` — return, Sharpe ratio, Sortino ratio, max drawdown, win rate, profit factor per strategy
- [x] Implement `generate_daily_report()` — end-of-day summary with all trades, P&L, risk metrics
- [x] Implement `export_csv()` — export trade history and performance data for date range
- [x] Define `Position`, `StrategyMetrics`, `DailyReport` dataclasses
- [x] Write tests: `tests/unit/test_portfolio.py` — metric calculations, reconciliation logic, CSV export
- [x] Verify: unit tests pass

---

### Task 7: Strategy Engine & Base Strategy

**Requirements:** R2, R5
**Dependencies:** Task 4

Implement the strategy orchestration engine, base strategy class, and signal model.

**Sub-tasks:**
- [x] Create `src/strategies/__init__.py`
- [x] Create `src/strategies/base.py` with `BaseStrategy` ABC — `evaluate()`, `required_indicators()`, `validate_capital()`
- [x] Create `src/strategies/signals.py` with `Signal` dataclass (strategy_name, symbol, direction, confidence, suggested_size, order_type, limit_price, stop_price, metadata, timestamp) and `SignalDirection` enum
- [x] Create `src/strategies/engine.py` with `StrategyEngine` class
- [x] Implement `start()` / `stop()` — start/stop all enabled strategies on their configured schedules
- [x] Implement `enable_strategy()` / `disable_strategy()` — independent lifecycle per strategy
- [x] Implement `_run_strategy_loop()` — evaluate strategy at configured frequency, suppress during market close for intraday strategies
- [x] Validate sufficient capital allocated before enabling a strategy
- [x] Route generated signals to RiskManager for pre-trade checks
- [x] Write tests: `tests/unit/test_strategy_engine.py` — scheduling, enable/disable, signal routing, market hours suppression
- [x] Verify: unit tests pass

---

### Task 8: Strategy Implementations - Batch 1 (Trend-Based)

**Requirements:** R2
**Dependencies:** Task 7

Implement Momentum, Moving Average Crossover, Trend Following (Dual MA), and Breakout strategies.

**Sub-tasks:**
- [x] Create `src/strategies/implementations/__init__.py`
- [x] Create `src/strategies/implementations/momentum.py` — lookback_period, momentum_threshold, generate LONG/SHORT signals based on price momentum
- [x] Create `src/strategies/implementations/ma_crossover.py` — fast_period, slow_period, ma_type (SMA/EMA), crossover signal generation
- [x] Create `src/strategies/implementations/trend_following.py` — fast_ma, slow_ma, atr_filter for trend confirmation
- [x] Create `src/strategies/implementations/breakout.py` — consolidation_period, breakout_atr_multiple, volume confirmation
- [x] Each strategy: implement `evaluate()` returning list[Signal], implement `required_indicators()`
- [x] Each strategy: configurable parameters via StrategyConfig
- [x] Write tests: `tests/unit/test_strategies/test_momentum.py`, `test_ma_crossover.py`, `test_trend_following.py`, `test_breakout.py` — signal generation with known data
- [x] Verify: unit tests pass

---

### Task 9: Strategy Implementations - Batch 2 (Mean-Reversion)

**Requirements:** R2
**Dependencies:** Task 7

Implement Mean Reversion, Bollinger Band Mean Reversion, RSI Divergence, and VWAP strategies.

**Sub-tasks:**
- [x] Create `src/strategies/implementations/mean_reversion.py` — lookback_period, z_score_threshold, generate signals when price deviates from mean
- [x] Create `src/strategies/implementations/bollinger.py` — bb_period, bb_std, entry_band, mean reversion within Bollinger Bands
- [x] Create `src/strategies/implementations/rsi_divergence.py` — rsi_period, overbought/oversold levels, divergence detection between price and RSI
- [x] Create `src/strategies/implementations/vwap.py` — deviation_threshold, session_type, trade toward VWAP when price deviates
- [x] Each strategy: implement `evaluate()` returning list[Signal], implement `required_indicators()`
- [x] Each strategy: configurable parameters via StrategyConfig
- [x] Write tests: `tests/unit/test_strategies/test_mean_reversion.py`, `test_bollinger.py`, `test_rsi_divergence.py`, `test_vwap.py`
- [x] Verify: unit tests pass

---

### Task 10: Strategy Implementations - Batch 3 (Statistical & Market Making)

**Requirements:** R2
**Dependencies:** Task 7

Implement Pairs Trading and Market Making strategies.

**Sub-tasks:**
- [x] Create `src/strategies/implementations/pairs_trading.py` — pair_symbols, cointegration_window, entry_z, exit_z, hedge_ratio calculation, spread monitoring
- [x] Create `src/strategies/implementations/market_making.py` — spread_bps, inventory_limit, skew_factor, place bid/ask quotes, manage inventory risk
- [x] Pairs Trading: cointegration test (Engle-Granger), z-score of spread, entry/exit logic
- [x] Market Making: dynamic spread based on volatility, inventory skew, position limits
- [x] Each strategy: implement `evaluate()` returning list[Signal], implement `required_indicators()`
- [x] Write tests: `tests/unit/test_strategies/test_pairs_trading.py`, `test_market_making.py`
- [x] Verify: unit tests pass

---

### Task 11: Capital Allocator

**Requirements:** R3
**Dependencies:** Task 2

Implement capital distribution across strategies with allocation modes and P&L tracking.

**Sub-tasks:**
- [x] Create `src/portfolio/capital_allocator.py` with `CapitalAllocator` class
- [x] Define `AllocationMode` enum: FIXED_AMOUNT, PERCENTAGE, EQUAL_WEIGHT
- [x] Implement `allocate()` — assign capital to strategy (fixed dollar, percentage, or equal-weight)
- [x] Implement `get_available()` — return remaining available capital for a strategy
- [x] Implement `can_place_order()` — check if order value fits within strategy allocation
- [x] Implement `record_fill()` — update per-strategy realized/unrealized P&L on fill
- [x] Implement `release()` — release undeployed capital back to pool when strategy disabled
- [x] Reject allocation when total exceeds available portfolio value, notify user
- [x] Define `StrategyAllocation` dataclass
- [x] Write tests: `tests/unit/test_capital.py` — allocation modes, over-allocation rejection, P&L tracking, release logic
- [x] Verify: unit tests pass

---

### Task 12: Risk Manager

**Requirements:** R6
**Dependencies:** Task 2, Task 6

Implement pre-trade risk checks: position limits, drawdown, daily loss, sector concentration, correlation.

**Sub-tasks:**
- [x] Create `src/risk/__init__.py`
- [x] Create `src/risk/manager.py` with `RiskManager` class
- [x] Implement `check_order()` — run all pre-trade risk checks, return RiskCheckResult (approve/reject + reason)
- [x] Implement `check_position_size()` — max position as % of portfolio (default 5%)
- [x] Implement `check_drawdown()` — halt all trading when portfolio drawdown exceeds threshold (default 10% from peak)
- [x] Implement `check_daily_loss()` — halt trading for day when daily loss exceeds limit (default 2%)
- [x] Implement `check_sector_concentration()` — prevent >25% in any single sector
- [x] Implement `check_correlation()` — limit exposure to highly correlated positions (correlation > 0.7)
- [x] Implement `halt_trading()` — set halted flag, emit alert event
- [x] Define `RiskCheckResult` dataclass, `RiskConfig` model
- [x] Write tests: `tests/unit/test_risk/test_risk_manager.py` — each check individually, combined check, halt logic
- [x] Verify: unit tests pass

---

### Task 13: Stop-Loss & VaR

**Requirements:** R6
**Dependencies:** Task 12

Implement stop-loss monitoring (fixed % and ATR trailing) and portfolio Value at Risk calculation.

**Sub-tasks:**
- [x] Create `src/risk/stops.py` with stop-loss monitoring logic
- [x] Implement fixed percentage stop-loss — generate close signal when price drops below entry * (1 - stop_pct)
- [x] Implement ATR-based trailing stop — trail stop at entry + N * ATR, update as price moves favorably
- [x] Implement `monitor_stops()` — check all open positions against their stop levels, return close signals
- [x] Create `src/risk/var.py` with VaR calculation
- [x] Implement historical simulation VaR — 95% confidence, 252-day lookback
- [x] Enforce portfolio-level VaR limit
- [x] Write tests: `tests/unit/test_risk/test_stops.py` — fixed stop trigger, trailing stop update, ATR calculation
- [x] Write tests: `tests/unit/test_risk/test_var.py` — VaR calculation with known data
- [x] Verify: unit tests pass

---

### Task 14: Alert Service

**Requirements:** R11
**Dependencies:** Task 2

Implement notification delivery via email, Slack, and webhooks with rate limiting.

**Sub-tasks:**
- [x] Create `src/alerts/__init__.py`
- [x] Create `src/alerts/service.py` with `AlertService` class and `AlertRateLimiter`
- [x] Create `src/alerts/channels/__init__.py`
- [x] Create `src/alerts/channels/slack.py` — send via Slack webhook URL (httpx POST)
- [x] Create `src/alerts/channels/email.py` — send via SMTP (aiosmtplib)
- [x] Create `src/alerts/channels/webhook.py` — send via generic HTTP webhook (httpx POST)
- [x] Implement `send()` — route alert to configured channels based on event type
- [x] Implement `send_critical()` — bypass rate limits for critical alerts
- [x] Implement rate limiting: max 1 notification per event type per minute (except critical)
- [x] Define `Alert` dataclass, `AlertEventType` enum, `AlertPriority` enum
- [x] Configurable event-to-channel routing in AlertConfig
- [x] Write tests: `tests/unit/test_alerts.py` — rate limiting, channel routing, critical bypass
- [ ] Verify: unit tests pass

---

### Task 15: Backtesting Engine

**Requirements:** R7
**Dependencies:** Task 7, Task 11, Task 12

Implement strategy backtesting with realistic execution simulation and walk-forward optimization.

**Sub-tasks:**
- [x] Create `src/backtesting/__init__.py`
- [x] Create `src/backtesting/engine.py` with `BacktestEngine` class
- [x] Create `src/backtesting/simulator.py` with `SimulatedExecution` — model slippage (configurable bps), commissions (per share), market impact
- [x] Implement `run()` — single strategy backtest against historical OHLCV data with configurable date range
- [x] Implement look-ahead bias prevention — strategies only access data at timestamps <= current simulated time
- [x] Implement `run_portfolio()` — multi-strategy backtest with capital allocation and risk management rules
- [x] Create `src/backtesting/walk_forward.py` — split data into in-sample/out-of-sample, run optimization
- [x] Generate performance report: total return, annualized return, Sharpe, Sortino, max drawdown, win rate, profit factor, avg trade duration
- [x] Store backtest results to database for comparison
- [x] Support data from IBKR historical API and local CSV files
- [x] Write tests: `tests/unit/test_backtesting.py` — execution simulation, look-ahead prevention, metric calculation
- [ ] Verify: unit tests pass

---

### Task 16: Database & Persistence

**Requirements:** R13
**Dependencies:** Task 1

Set up PostgreSQL with SQLAlchemy async, Alembic migrations, and ORM models.

**Sub-tasks:**
- [x] Create `src/persistence/__init__.py`
- [x] Create `src/persistence/database.py` — async SQLAlchemy engine, session factory, connection pooling
- [x] Create `src/persistence/models.py` — ORM models: Position, Order, Trade, DailySnapshot, BacktestResult, AlertLog
- [x] Create `alembic.ini` and `alembic/env.py` configured for async
- [x] Create initial migration: `alembic/versions/001_initial_schema.py` with all tables from design doc
- [x] Implement state persistence: save/load positions and orders for crash recovery
- [x] Implement reconciliation helper: compare persisted state with IBKR account on startup
- [x] Write tests: `tests/integration/test_database.py` — CRUD operations, migration up/down
- [x] Verify: migrations run, tests pass against test database

---

### Task 17: Dashboard API (FastAPI)

**Requirements:** R10, R14
**Dependencies:** Task 6, Task 16

Implement the REST API and WebSocket endpoints for the monitoring dashboard.

**Sub-tasks:**
- [x] Create `src/dashboard/__init__.py`
- [x] Create `src/dashboard/api.py` with FastAPI app
- [x] Implement endpoints: `GET /api/portfolio`, `GET /api/positions`, `GET /api/strategies`, `GET /api/performance/{strategy}`, `GET /api/risk`, `GET /api/orders`
- [x] Create `src/dashboard/websocket.py` — WebSocket endpoint `/ws/live` streaming real-time updates (positions, P&L, signals, orders)
- [x] Implement `GET /health` — health check endpoint for Docker
- [x] Implement `GET /api/export/csv` — trigger CSV export
- [x] Add authentication token middleware (configurable via env var)
- [x] Write tests: `tests/integration/test_api.py` — endpoint responses, WebSocket connection, auth
- [x] Verify: FastAPI starts, endpoints respond

---

### Task 18: Dashboard Frontend (React)

**Requirements:** R10
**Dependencies:** Task 17

Build the React frontend for real-time portfolio monitoring.

**Sub-tasks:**
- [x] Create `dashboard-ui/` with Vite + React + TypeScript scaffold
- [x] Create `dashboard-ui/package.json` with dependencies: react, react-dom, recharts, @tanstack/react-query, tailwindcss
- [x] Implement portfolio summary component — total value, P&L, drawdown
- [x] Implement positions table — symbol, quantity, entry price, current price, P&L, strategy
- [x] Implement strategy status panel — per-strategy state (running/paused/halted), allocation, return
- [x] Implement performance charts — equity curve, per-strategy comparison (Recharts)
- [x] Implement risk utilization gauges — position limits, drawdown, daily loss, sector concentration
- [x] Implement WebSocket connection for real-time updates
- [x] Implement order history table with audit trail
- [x] Create `dashboard-ui/Dockerfile` for production build (nginx serving static files)
- [x] Verify: `npm run build` succeeds, UI renders with mock data

---

### Task 19: Docker Deployment

**Requirements:** R14
**Dependencies:** Task 16, Task 17, Task 18

Create Docker and docker-compose configuration for the complete system.

**Sub-tasks:**
- [x] Create `Dockerfile` — Python 3.11-slim, install dependencies, copy source, run migrations + start bot
- [x] Create `docker-compose.yml` — services: trading-bot, postgres (15-alpine), redis (7-alpine), dashboard-ui
- [x] Configure health checks for all services (pg_isready, redis-cli ping, curl /health)
- [x] Configure Docker volumes: postgres-data, redis-data, bot-data, bot-logs
- [x] Expose dashboard on configurable port (default 8080)
- [x] Configure `restart: unless-stopped` for all services
- [x] Create `.dockerignore` — exclude .git, __pycache__, .env, data/
- [x] Write `scripts/start.sh` — run alembic migrations then start the bot
- [x] Verify: `docker-compose build` succeeds, `docker-compose up` starts all services

---

### Task 20: Integration & Orchestration

**Requirements:** R1, R8, R13
**Dependencies:** Tasks 3-14

Wire all components together in `src/main.py` — startup sequence, shutdown, paper/live mode switching.

**Sub-tasks:**
- [x] Implement `src/main.py` startup sequence: load config → connect DB/Redis → connect IBKR → reconcile positions → start strategies → start dashboard
- [x] Implement graceful shutdown: stop strategies → cancel pending orders → disconnect IBKR → close DB/Redis
- [x] Implement paper/live mode: connect to correct IBKR endpoint based on config, require explicit confirmation for live mode switch
- [x] Implement strategy isolation: unhandled exception in one strategy doesn't crash others (asyncio task exception handling)
- [x] Implement stale data handling: halt signal generation for affected instruments, notify user
- [x] Implement IBKR rate limit backpressure: queue requests when approaching limit
- [x] Wire signal flow: Strategy → RiskManager → CapitalAllocator → OrderManager
- [x] Wire event flow: fills → PortfolioMonitor + CapitalAllocator + AlertService
- [x] Write tests: `tests/integration/test_order_flow.py` — end-to-end signal-to-order with mocked IBKR
- [x] Verify: bot starts in paper mode, connects to IBKR paper account (or mock)

---

### Task 21: Property-Based Tests

**Requirements:** All
**Dependencies:** Tasks 11, 12, 5, 15

Implement property-based tests using Hypothesis to verify correctness properties from the design.

**Sub-tasks:**
- [x] Create `tests/property/test_capital_properties.py`:
  - P1: sum of allocations never exceeds total portfolio value
  - P9: no strategy deploys capital exceeding its allocation
- [x] Create `tests/property/test_risk_properties.py`:
  - P2: no order executes that would breach risk limits
  - P7: message rate never exceeds 50/sec
- [x] Create `tests/property/test_strategy_properties.py`:
  - P5: failure in one strategy doesn't affect others
  - P6: backtest strategies only access data at time <= t
- [x] Create `tests/property/test_order_properties.py`:
  - P4: every order state transition is recorded in audit trail
  - P8: stop-loss trigger generates close order within next cycle
- [x] Create `tests/property/test_portfolio_properties.py`:
  - P3: internal position state matches IBKR after reconciliation
  - P10: critical events always result in notification delivery attempt
- [x] Use Hypothesis strategies: `st.decimals()`, `st.lists()`, `st.datetimes()`, custom strategies for Signal, Order, Position
- [x] Verify: all property tests pass with default settings (100 examples per property)
