# Technical Design Document: IBKR Trading Bot

## Overview

This document describes the technical architecture for a Python-based automated trading bot that connects to Interactive Brokers (IBKR). The system uses an event-driven architecture with modular components for connectivity, strategy execution, risk management, order management, backtesting, and monitoring.

## Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Language | Python 3.11+ | Rich ecosystem for quantitative finance (numpy, pandas, scipy) |
| IBKR Client | ib_async (ib-api-reloaded) | Actively maintained async fork of ib_insync, native asyncio support |
| Async Runtime | asyncio | Event-driven architecture, non-blocking I/O for market data |
| Database | PostgreSQL 15 | ACID compliance for order/position state, time-series queries |
| ORM | SQLAlchemy 2.0 (async) | Type-safe database access, migration support via Alembic |
| Cache | Redis 7 | Real-time market data cache, pub/sub for inter-component messaging |
| Dashboard | FastAPI + React | REST/WebSocket API backend, real-time frontend |
| Configuration | Pydantic Settings + YAML | Validated configuration with environment variable overrides |
| Containerization | Docker + Docker Compose | Reproducible deployment, service orchestration |
| Testing | pytest + Hypothesis | Unit/integration tests with property-based testing |
| Logging | structlog | Structured JSON logging with context propagation |

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Docker Compose                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │                    Trading Bot Service                          │   │
│  │                                                                 │   │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    │   │
│  │  │ Connection   │    │  Strategy    │    │    Risk      │    │   │
│  │  │  Manager     │◄──►│   Engine     │◄──►│   Manager    │    │   │
│  │  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘    │   │
│  │         │                    │                    │            │   │
│  │         ▼                    ▼                    ▼            │   │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    │   │
│  │  │   Market     │    │   Capital    │    │    Order     │    │   │
│  │  │   Data Hub   │    │  Allocator   │    │   Manager    │    │   │
│  │  └──────────────┘    └──────────────┘    └──────────────┘    │   │
│  │                                                                 │   │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    │   │
│  │  │  Portfolio   │    │   Alert      │    │  Backtesting │    │   │
│  │  │  Monitor     │    │   Service    │    │   Engine     │    │   │
│  │  └──────────────┘    └──────────────┘    └──────────────┘    │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │
│  │  PostgreSQL  │    │    Redis     │    │  Dashboard   │           │
│  │              │    │              │    │  (FastAPI +  │           │
│  │              │    │              │    │   React)     │           │
│  └──────────────┘    └──────────────┘    └──────────────┘           │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│  IBKR TWS / IB   │
│     Gateway       │
└──────────────────┘
```

## Component Design

### 1. Connection Manager

**Module:** `src/connection/manager.py`

**Responsibilities:**
- Establish and maintain socket connections to TWS or IB Gateway
- Handle reconnection with exponential backoff
- Manage concurrent data subscriptions
- Verify account permissions on connect

```python
class ConnectionManager:
    def __init__(self, config: ConnectionConfig):
        self.ib = IB()
        self.config = config
        self._reconnect_attempts = 0
        self._max_retries = 5

    async def connect(self) -> None:
        """Connect to IBKR with configured host/port."""

    async def disconnect(self) -> None:
        """Gracefully disconnect."""

    async def _on_disconnected(self) -> None:
        """Handle disconnection with exponential backoff reconnection."""

    async def _verify_account(self) -> AccountInfo:
        """Verify account permissions and type (paper/live)."""

    def subscribe_market_data(self, contract: Contract) -> None:
        """Subscribe to real-time market data for a contract."""

    def subscribe_account_updates(self) -> None:
        """Subscribe to account value and position updates."""
```

**Connection Config:**
```yaml
connection:
  mode: "gateway"  # "tws" or "gateway"
  host: "127.0.0.1"
  port: 4002       # 4001=TWS live, 4002=TWS paper, 7496=Gateway live, 7497=Gateway paper
  client_id: 1
  timeout: 30
  readonly: false
```


### 2. Market Data Hub

**Module:** `src/data/market_data_hub.py`

**Responsibilities:**
- Aggregate and distribute real-time market data
- Maintain OHLCV bar builders at multiple timeframes
- Cache latest quotes in Redis for fast access
- Detect stale data conditions

```python
class MarketDataHub:
    def __init__(self, connection: ConnectionManager, redis: Redis):
        self._subscriptions: dict[str, MarketDataSubscription] = {}
        self._bar_builders: dict[str, dict[Timeframe, BarBuilder]] = {}

    async def subscribe(self, symbol: str, asset_class: AssetClass) -> None:
        """Subscribe to market data for a symbol."""

    async def get_latest_bar(self, symbol: str, timeframe: Timeframe) -> Bar:
        """Get the latest completed bar for a symbol/timeframe."""

    async def get_history(self, symbol: str, timeframe: Timeframe, periods: int) -> pd.DataFrame:
        """Get historical bars from cache or IBKR."""

    def on_tick(self, ticker: Ticker) -> None:
        """Process incoming tick data, update bar builders."""

    async def _detect_stale_data(self, symbol: str) -> bool:
        """Check if market data is stale beyond threshold."""
```

**Timeframe Enum:**
```python
class Timeframe(Enum):
    TICK = "tick"
    ONE_MIN = "1min"
    FIVE_MIN = "5min"
    FIFTEEN_MIN = "15min"
    ONE_HOUR = "1hour"
    DAILY = "1day"
    WEEKLY = "1week"
```

### 3. Strategy Engine

**Module:** `src/strategies/engine.py`

**Responsibilities:**
- Manage strategy lifecycle (enable, disable, pause)
- Schedule strategy evaluations at configured frequencies
- Collect and route trading signals to risk management
- Provide base class for strategy implementations

```python
class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub):
        self.config = config
        self.data_hub = data_hub
        self.state: StrategyState = StrategyState.IDLE

    @abstractmethod
    async def evaluate(self) -> list[Signal]:
        """Evaluate market conditions and generate signals."""

    @abstractmethod
    def required_indicators(self) -> list[Indicator]:
        """Return indicators this strategy needs computed."""

    def validate_capital(self, allocated: Decimal) -> bool:
        """Check if allocated capital is sufficient."""


class StrategyEngine:
    def __init__(self, strategies: list[BaseStrategy], risk_manager: RiskManager):
        self._strategies: dict[str, BaseStrategy] = {}
        self._schedulers: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """Start all enabled strategies on their configured schedules."""

    async def stop(self) -> None:
        """Stop all strategies gracefully."""

    async def enable_strategy(self, name: str) -> None:
        """Enable a strategy and start its evaluation loop."""

    async def disable_strategy(self, name: str) -> None:
        """Disable a strategy, cancel pending signals."""

    async def _run_strategy_loop(self, strategy: BaseStrategy) -> None:
        """Run strategy evaluation at configured frequency."""
```

**Signal Model:**
```python
@dataclass
class Signal:
    strategy_name: str
    symbol: str
    direction: SignalDirection  # LONG, SHORT, CLOSE
    confidence: float          # 0.0 to 1.0
    suggested_size: Decimal    # Suggested position size in dollars
    order_type: OrderType
    limit_price: Optional[Decimal]
    stop_price: Optional[Decimal]
    metadata: dict[str, Any]
    timestamp: datetime
```

### 4. Strategy Implementations

**Module:** `src/strategies/implementations/`

Each strategy is a separate module implementing `BaseStrategy`:

| Strategy | File | Key Parameters |
|----------|------|----------------|
| Momentum | `momentum.py` | lookback_period, momentum_threshold |
| Mean Reversion | `mean_reversion.py` | lookback_period, z_score_threshold |
| Pairs Trading | `pairs_trading.py` | pair_symbols, cointegration_window, entry_z |
| Breakout | `breakout.py` | consolidation_period, breakout_atr_multiple |
| Moving Average Crossover | `ma_crossover.py` | fast_period, slow_period, ma_type |
| RSI Divergence | `rsi_divergence.py` | rsi_period, overbought, oversold |
| VWAP | `vwap.py` | deviation_threshold, session_type |
| Bollinger Band Mean Reversion | `bollinger.py` | bb_period, bb_std, entry_band |
| Trend Following (Dual MA) | `trend_following.py` | fast_ma, slow_ma, atr_filter |
| Market Making | `market_making.py` | spread_bps, inventory_limit, skew_factor |

### 5. Capital Allocator

**Module:** `src/portfolio/capital_allocator.py`

**Responsibilities:**
- Distribute capital across strategies based on allocation rules
- Track per-strategy P&L independently
- Enforce allocation limits on order generation
- Release capital when strategies are disabled

```python
class AllocationMode(Enum):
    FIXED_AMOUNT = "fixed_amount"
    PERCENTAGE = "percentage"
    EQUAL_WEIGHT = "equal_weight"


class CapitalAllocator:
    def __init__(self, total_capital: Decimal, allocations: dict[str, Allocation]):
        self._allocations: dict[str, StrategyAllocation] = {}

    def allocate(self, strategy_name: str, amount: Decimal, mode: AllocationMode) -> None:
        """Assign capital to a strategy."""

    def get_available(self, strategy_name: str) -> Decimal:
        """Get remaining available capital for a strategy."""

    def can_place_order(self, strategy_name: str, order_value: Decimal) -> bool:
        """Check if order fits within strategy allocation."""

    def record_fill(self, strategy_name: str, fill: Fill) -> None:
        """Update P&L tracking on fill."""

    def release(self, strategy_name: str) -> Decimal:
        """Release undeployed capital back to pool."""
```


### 6. Risk Manager

**Module:** `src/risk/manager.py`

**Responsibilities:**
- Pre-trade risk checks (position limits, drawdown, daily loss, sector concentration)
- Real-time position monitoring (stop-loss, trailing stops)
- Portfolio-level VaR calculation
- Correlation-based diversification enforcement
- Trading halt logic

```python
class RiskManager:
    def __init__(self, config: RiskConfig, portfolio: PortfolioMonitor):
        self._config = config
        self._portfolio = portfolio
        self._halted = False
        self._daily_pnl = Decimal("0")

    async def check_order(self, signal: Signal) -> RiskCheckResult:
        """Run all pre-trade risk checks on a signal. Returns approve/reject with reason."""

    async def check_position_size(self, signal: Signal) -> bool:
        """Verify position doesn't exceed max % of portfolio."""

    async def check_drawdown(self) -> bool:
        """Check if portfolio drawdown exceeds threshold."""

    async def check_daily_loss(self) -> bool:
        """Check if daily loss limit is breached."""

    async def check_sector_concentration(self, signal: Signal) -> bool:
        """Check sector exposure limits."""

    async def check_correlation(self, signal: Signal) -> bool:
        """Check correlation-based diversification limits."""

    async def calculate_var(self) -> Decimal:
        """Calculate portfolio VaR using historical simulation."""

    async def monitor_stops(self) -> list[Signal]:
        """Check all open positions against stop-loss levels."""

    async def halt_trading(self, reason: str) -> None:
        """Halt all trading and notify alert service."""


@dataclass
class RiskCheckResult:
    approved: bool
    reason: Optional[str]
    risk_metrics: dict[str, Any]
```

**Risk Configuration:**
```yaml
risk:
  max_position_pct: 0.05          # 5% of portfolio per instrument
  max_drawdown_pct: 0.10          # 10% from peak equity
  max_daily_loss_pct: 0.02        # 2% daily loss limit
  max_sector_concentration: 0.25  # 25% per sector
  max_correlation: 0.7            # Correlation threshold
  var_confidence: 0.95            # 95% VaR
  var_lookback_days: 252          # 1 year historical simulation
  stop_loss:
    type: "atr_trailing"          # "fixed_pct" or "atr_trailing"
    atr_multiplier: 2.0
    fixed_pct: 0.03
```

### 7. Order Manager

**Module:** `src/orders/manager.py`

**Responsibilities:**
- Translate signals into IBKR orders
- Track order lifecycle through all states
- Handle partial fills, rejections, timeouts
- Rate limiting (50 msg/sec IBKR limit)
- Maintain complete audit trail

```python
class OrderManager:
    def __init__(self, connection: ConnectionManager, db: AsyncSession):
        self._connection = connection
        self._pending_orders: dict[int, ManagedOrder] = {}
        self._rate_limiter = RateLimiter(max_per_second=45)  # Buffer below 50

    async def submit_order(self, signal: Signal, contract: Contract) -> ManagedOrder:
        """Create and submit an order from a signal."""

    async def cancel_order(self, order_id: int) -> None:
        """Cancel a pending order."""

    async def cancel_stale_orders(self) -> list[int]:
        """Cancel orders exceeding timeout thresholds."""

    def on_order_status(self, trade: Trade) -> None:
        """Handle order status updates from IBKR."""

    def on_fill(self, trade: Trade, fill: Fill) -> None:
        """Handle fill events, update positions."""

    async def get_audit_trail(self, strategy: Optional[str] = None) -> list[OrderRecord]:
        """Get complete order history with optional strategy filter."""


@dataclass
class ManagedOrder:
    order_id: int
    strategy_name: str
    signal: Signal
    contract: Contract
    status: OrderStatus
    submitted_at: datetime
    filled_quantity: Decimal
    average_fill_price: Optional[Decimal]
    timeout: timedelta
```

**Order Types Supported:**
```python
class OrderType(Enum):
    MARKET = "MKT"
    LIMIT = "LMT"
    STOP = "STP"
    STOP_LIMIT = "STP LMT"
    TRAILING_STOP = "TRAIL"
    BRACKET = "BRACKET"
```

### 8. Portfolio Monitor

**Module:** `src/portfolio/monitor.py`

**Responsibilities:**
- Track real-time P&L (realized + unrealized)
- Calculate per-strategy performance metrics
- Generate daily summary reports
- Export trade history to CSV

```python
class PortfolioMonitor:
    def __init__(self, connection: ConnectionManager, db: AsyncSession):
        self._positions: dict[str, Position] = {}
        self._strategy_metrics: dict[str, StrategyMetrics] = {}

    async def sync_positions(self) -> None:
        """Reconcile internal state with IBKR account."""

    def get_total_value(self) -> Decimal:
        """Get current total portfolio value."""

    def get_unrealized_pnl(self) -> Decimal:
        """Get total unrealized P&L."""

    def get_peak_equity(self) -> Decimal:
        """Get historical peak equity for drawdown calculation."""

    async def calculate_strategy_metrics(self, strategy: str) -> StrategyMetrics:
        """Calculate Sharpe, Sortino, drawdown for a strategy."""

    async def generate_daily_report(self) -> DailyReport:
        """Generate end-of-day summary report."""

    async def export_csv(self, start: date, end: date) -> Path:
        """Export trade history to CSV file."""


@dataclass
class StrategyMetrics:
    total_return: Decimal
    annualized_return: Decimal
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: Decimal
    win_rate: float
    profit_factor: float
    total_trades: int
```

### 9. Backtesting Engine

**Module:** `src/backtesting/engine.py`

**Responsibilities:**
- Simulate strategy execution against historical data
- Model slippage, commissions, market impact
- Prevent look-ahead bias
- Walk-forward optimization
- Portfolio-level multi-strategy backtests

```python
class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        self._config = config
        self._results_store = BacktestResultStore()

    async def run(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        start_date: date,
        end_date: date,
    ) -> BacktestResult:
        """Run a single strategy backtest."""

    async def run_portfolio(
        self,
        strategies: list[BaseStrategy],
        allocations: dict[str, Decimal],
        start_date: date,
        end_date: date,
    ) -> PortfolioBacktestResult:
        """Run portfolio-level backtest with capital allocation and risk rules."""

    async def walk_forward(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        in_sample_pct: float,
        num_folds: int,
    ) -> WalkForwardResult:
        """Walk-forward optimization with in-sample/out-of-sample splits."""

    async def load_data(self, symbol: str, source: DataSource) -> pd.DataFrame:
        """Load historical data from IBKR API or local CSV."""


@dataclass
class BacktestResult:
    total_return: Decimal
    annualized_return: Decimal
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: Decimal
    win_rate: float
    profit_factor: float
    avg_trade_duration: timedelta
    total_trades: int
    trades: list[BacktestTrade]
    equity_curve: pd.Series


class SimulatedExecution:
    """Models realistic execution with slippage and commissions."""

    def __init__(self, slippage_bps: float, commission_per_share: Decimal):
        pass

    def simulate_fill(self, order: Order, market_data: Bar) -> SimulatedFill:
        """Apply slippage and commission to get realistic fill."""
```

**Backtest Configuration:**
```yaml
backtesting:
  slippage_bps: 5              # 5 basis points slippage
  commission_per_share: 0.005  # $0.005 per share
  market_impact_bps: 2         # 2 bps market impact
  data_source: "ibkr"          # "ibkr" or "csv"
  csv_directory: "./data/historical"
  results_directory: "./data/backtest_results"
```


### 10. Alert Service

**Module:** `src/alerts/service.py`

**Responsibilities:**
- Send notifications via email, Slack, and HTTP webhooks
- Rate limiting to prevent flooding
- Configurable event-to-channel routing
- Priority levels for different event types

```python
class AlertService:
    def __init__(self, config: AlertConfig):
        self._channels: list[AlertChannel] = []
        self._rate_limiter = AlertRateLimiter(max_per_type_per_minute=1)

    async def send(self, alert: Alert) -> None:
        """Send alert through configured channels based on event type."""

    async def send_critical(self, alert: Alert) -> None:
        """Send critical alert immediately, bypassing rate limits."""

    def register_channel(self, channel: AlertChannel) -> None:
        """Register a notification channel."""


class AlertChannel(ABC):
    @abstractmethod
    async def deliver(self, alert: Alert) -> bool:
        """Deliver alert through this channel."""


class SlackChannel(AlertChannel):
    async def deliver(self, alert: Alert) -> bool: ...

class EmailChannel(AlertChannel):
    async def deliver(self, alert: Alert) -> bool: ...

class WebhookChannel(AlertChannel):
    async def deliver(self, alert: Alert) -> bool: ...


@dataclass
class Alert:
    event_type: AlertEventType
    priority: AlertPriority  # LOW, MEDIUM, HIGH, CRITICAL
    title: str
    message: str
    metadata: dict[str, Any]
    timestamp: datetime
```

### 11. Dashboard API & Frontend

**Module:** `src/dashboard/`

**Backend (FastAPI):**
```python
# src/dashboard/api.py
app = FastAPI(title="IBKR Trading Bot Dashboard")

@app.get("/api/portfolio")
async def get_portfolio() -> PortfolioSummary: ...

@app.get("/api/positions")
async def get_positions() -> list[Position]: ...

@app.get("/api/strategies")
async def get_strategies() -> list[StrategyStatus]: ...

@app.get("/api/performance/{strategy}")
async def get_performance(strategy: str, period: str) -> PerformanceData: ...

@app.get("/api/risk")
async def get_risk_metrics() -> RiskMetrics: ...

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket): ...
    # Stream real-time updates: positions, P&L, signals, orders
```

**Frontend (React):**
- Real-time portfolio dashboard with WebSocket updates
- Strategy performance comparison charts (Recharts)
- Position table with P&L
- Risk utilization gauges
- Order history with audit trail
- Backtest result viewer

### 12. Configuration System

**Module:** `src/config/`

```python
# src/config/settings.py
class Settings(BaseSettings):
    """Root configuration loaded from YAML + environment variables."""

    connection: ConnectionConfig
    strategies: dict[str, StrategyConfig]
    risk: RiskConfig
    capital: CapitalConfig
    alerts: AlertConfig
    backtesting: BacktestConfig
    database: DatabaseConfig
    dashboard: DashboardConfig

    model_config = SettingsConfigDict(
        yaml_file="config.yaml",
        env_prefix="TRADING_BOT_",
        env_nested_delimiter="__",
    )


class ConfigWatcher:
    """Watch config file for changes and hot-reload strategy parameters."""

    def __init__(self, config_path: Path, on_change: Callable):
        self._path = config_path
        self._on_change = on_change

    async def watch(self) -> None:
        """Monitor file for changes using watchfiles."""
```

## Database Schema

```sql
-- Core tables for state persistence

CREATE TABLE positions (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    asset_class VARCHAR(10) NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    quantity DECIMAL NOT NULL,
    avg_entry_price DECIMAL NOT NULL,
    current_price DECIMAL,
    unrealized_pnl DECIMAL,
    realized_pnl DECIMAL DEFAULT 0,
    opened_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    ibkr_order_id INTEGER,
    strategy_name VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    direction VARCHAR(5) NOT NULL,  -- BUY, SELL
    order_type VARCHAR(15) NOT NULL,
    quantity DECIMAL NOT NULL,
    limit_price DECIMAL,
    stop_price DECIMAL,
    status VARCHAR(20) NOT NULL,
    filled_quantity DECIMAL DEFAULT 0,
    avg_fill_price DECIMAL,
    submitted_at TIMESTAMP NOT NULL,
    filled_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    rejection_reason TEXT
);

CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    strategy_name VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    direction VARCHAR(5) NOT NULL,
    quantity DECIMAL NOT NULL,
    price DECIMAL NOT NULL,
    commission DECIMAL NOT NULL,
    executed_at TIMESTAMP NOT NULL
);

CREATE TABLE daily_snapshots (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    total_equity DECIMAL NOT NULL,
    total_pnl DECIMAL NOT NULL,
    realized_pnl DECIMAL NOT NULL,
    unrealized_pnl DECIMAL NOT NULL,
    peak_equity DECIMAL NOT NULL,
    drawdown_pct DECIMAL NOT NULL,
    strategy_metrics JSONB NOT NULL
);

CREATE TABLE backtest_results (
    id SERIAL PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    parameters JSONB NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    total_return DECIMAL NOT NULL,
    sharpe_ratio DECIMAL,
    max_drawdown DECIMAL,
    metrics JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE alerts_log (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(30) NOT NULL,
    priority VARCHAR(10) NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    delivered_channels TEXT[],
    created_at TIMESTAMP NOT NULL
);
```

## Project Structure

```
ibkr-trading-bot/
├── docker-compose.yml
├── Dockerfile
├── config.yaml                    # Main configuration
├── config.example.yaml            # Documented defaults
├── pyproject.toml                 # Python project config (uv/pip)
├── alembic/                       # Database migrations
│   ├── alembic.ini
│   └── versions/
├── src/
│   ├── __init__.py
│   ├── main.py                    # Application entry point
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py            # Pydantic settings models
│   │   └── watcher.py             # Hot-reload config watcher
│   ├── connection/
│   │   ├── __init__.py
│   │   └── manager.py             # IBKR connection management
│   ├── data/
│   │   ├── __init__.py
│   │   ├── market_data_hub.py     # Real-time data aggregation
│   │   ├── bar_builder.py         # OHLCV bar construction
│   │   └── historical.py          # Historical data loading
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── engine.py              # Strategy orchestration
│   │   ├── base.py                # BaseStrategy ABC
│   │   ├── signals.py             # Signal models
│   │   └── implementations/
│   │       ├── __init__.py
│   │       ├── momentum.py
│   │       ├── mean_reversion.py
│   │       ├── pairs_trading.py
│   │       ├── breakout.py
│   │       ├── ma_crossover.py
│   │       ├── rsi_divergence.py
│   │       ├── vwap.py
│   │       ├── bollinger.py
│   │       ├── trend_following.py
│   │       └── market_making.py
│   ├── portfolio/
│   │   ├── __init__.py
│   │   ├── capital_allocator.py
│   │   └── monitor.py
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── manager.py             # Risk checks and enforcement
│   │   ├── stops.py               # Stop-loss monitoring
│   │   └── var.py                 # Value at Risk calculation
│   ├── orders/
│   │   ├── __init__.py
│   │   ├── manager.py             # Order lifecycle management
│   │   └── rate_limiter.py        # IBKR rate limit compliance
│   ├── backtesting/
│   │   ├── __init__.py
│   │   ├── engine.py              # Backtest execution
│   │   ├── simulator.py           # Simulated execution model
│   │   └── walk_forward.py        # Walk-forward optimization
│   ├── alerts/
│   │   ├── __init__.py
│   │   ├── service.py             # Alert routing and delivery
│   │   └── channels/
│   │       ├── __init__.py
│   │       ├── slack.py
│   │       ├── email.py
│   │       └── webhook.py
│   ├── dashboard/
│   │   ├── __init__.py
│   │   ├── api.py                 # FastAPI routes
│   │   └── websocket.py           # Real-time WebSocket handler
│   ├── persistence/
│   │   ├── __init__.py
│   │   ├── database.py            # SQLAlchemy session management
│   │   └── models.py              # ORM models
│   └── utils/
│       ├── __init__.py
│       ├── logging.py             # Structured logging setup
│       └── contracts.py           # IBKR contract helpers
├── dashboard-ui/                  # React frontend
│   ├── package.json
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   └── hooks/
│   └── vite.config.ts
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_strategies/
│   │   ├── test_risk/
│   │   ├── test_orders/
│   │   └── test_capital/
│   ├── integration/
│   │   ├── test_connection.py
│   │   └── test_order_flow.py
│   └── property/
│       ├── test_risk_properties.py
│       ├── test_capital_properties.py
│       └── test_strategy_properties.py
└── data/
    ├── historical/                # Local CSV data
    └── backtest_results/          # Stored backtest outputs
```


## Data Flow

### Signal Generation to Order Execution

```
Market Data (IBKR) 
    │
    ▼
MarketDataHub (aggregate, build bars)
    │
    ▼
StrategyEngine (evaluate at configured frequency)
    │
    ▼
Signal (direction, symbol, confidence, size)
    │
    ▼
RiskManager.check_order() ──── REJECT ──► Log + Alert
    │
    ▼ APPROVE
CapitalAllocator.can_place_order() ──── REJECT ──► Log
    │
    ▼ APPROVE
OrderManager.submit_order()
    │
    ▼
IBKR (execute via SmartRouting)
    │
    ▼
OrderManager.on_fill() ──► PortfolioMonitor.update()
                        ──► CapitalAllocator.record_fill()
                        ──► AlertService.send() (trade notification)
```

### Startup Reconciliation Flow

```
Bot Starts
    │
    ▼
Load Configuration (YAML + env vars)
    │
    ▼
Connect to PostgreSQL + Redis
    │
    ▼
ConnectionManager.connect() to IBKR
    │
    ▼
PortfolioMonitor.sync_positions() (reconcile with IBKR)
    │
    ▼
OrderManager.sync_open_orders() (reconcile pending orders)
    │
    ▼
StrategyEngine.start() (begin evaluation loops)
    │
    ▼
Dashboard API starts (FastAPI on port 8080)
```

## Docker Deployment

**docker-compose.yml:**
```yaml
version: "3.9"
services:
  trading-bot:
    build: .
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - bot-data:/app/data
      - bot-logs:/app/logs
    environment:
      - TRADING_BOT_DATABASE__URL=postgresql+asyncpg://bot:bot@postgres:5432/trading
      - TRADING_BOT_REDIS__URL=redis://redis:6379/0
    ports:
      - "${DASHBOARD_PORT:-8080}:8080"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: bot
      POSTGRES_PASSWORD: bot
      POSTGRES_DB: trading
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bot"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  dashboard-ui:
    build: ./dashboard-ui
    ports:
      - "${UI_PORT:-3000}:3000"
    depends_on:
      - trading-bot

volumes:
  postgres-data:
  redis-data:
  bot-data:
  bot-logs:
```

**Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Run migrations and start
CMD ["sh", "-c", "alembic upgrade head && python -m src.main"]
```

## Correctness Properties

The following properties must hold for the system to be considered correct:

### P1: Capital Conservation
**Property:** The sum of all strategy allocations SHALL never exceed total portfolio value.
```
∀ t: sum(allocation[s] for s in active_strategies) <= total_portfolio_value(t)
```

### P2: Risk Limit Enforcement
**Property:** No order SHALL be executed that would cause any risk limit to be breached.
```
∀ order o: if risk_check(o) == REJECT then o.status != FILLED
```

### P3: Position Consistency
**Property:** Internal position state SHALL always match IBKR account state after reconciliation.
```
∀ symbol s: internal_position(s) == ibkr_position(s) after sync_positions()
```

### P4: Order Audit Completeness
**Property:** Every order state transition SHALL be recorded in the audit trail.
```
∀ order o, state_change sc: exists(audit_record(o, sc))
```

### P5: Strategy Isolation
**Property:** A failure in one strategy SHALL not affect the execution of other strategies.
```
∀ strategy s1, s2 where s1 != s2: failure(s1) → state(s2) == RUNNING
```

### P6: No Look-Ahead Bias
**Property:** During backtesting, a strategy at time t SHALL only access data from times <= t.
```
∀ strategy evaluation at time t: data_accessed ⊆ {d | d.timestamp <= t}
```

### P7: Rate Limit Compliance
**Property:** The system SHALL never exceed IBKR's message rate limit of 50 messages per second.
```
∀ 1-second window w: count(messages_sent(w)) <= 50
```

### P8: Stop-Loss Guarantee
**Property:** When a stop-loss is triggered, a close order SHALL be generated within the next evaluation cycle.
```
∀ position p: if price(p) crosses stop_level(p) then close_order(p) generated within next_cycle
```

### P9: Allocation Boundary
**Property:** No strategy SHALL deploy capital exceeding its allocation.
```
∀ strategy s: deployed_capital(s) <= allocated_capital(s)
```

### P10: Alert Delivery
**Property:** Critical events SHALL always result in at least one notification delivery attempt.
```
∀ critical_event e: exists(delivery_attempt(e))
```

## Error Handling Strategy

| Error Type | Handling | Recovery |
|-----------|----------|----------|
| IBKR connection lost | Exponential backoff reconnection (5 retries) | Resume from persisted state |
| Strategy exception | Isolate strategy, continue others | Log, alert, manual restart |
| Database unavailable | Queue writes in Redis, retry | Flush queue on reconnect |
| Stale market data | Halt signals for affected symbols | Resume on fresh data |
| Rate limit hit | Queue and backpressure | Drain queue at safe rate |
| Config validation fail | Refuse to start | Fix config, restart |
| Order rejection | Log reason, notify strategy | Strategy adjusts next signal |

## Security Considerations

- All sensitive values (API keys, webhook URLs, account credentials) stored as environment variables, never in config files
- Database credentials use Docker secrets in production
- Dashboard API requires authentication token (configurable)
- No outbound network calls except to IBKR and configured alert channels
- Structured logging redacts sensitive fields automatically
