# IBKR Trading Bot — Deployment Guide

Full steps to deploy and run the trading bot on your workstation.

---

## Prerequisites

1. **Docker Desktop** installed and running
2. **IBKR Gateway or TWS** installed (download from [Interactive Brokers](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php))
3. An IBKR account (paper trading account works)

---

## Step 1: Configure IBKR Gateway

1. Launch **IB Gateway** (or TWS)
2. Log in with your paper trading credentials
3. Go to **Configure → Settings → API → Settings**:
   - Enable "Enable ActiveX and Socket Clients"
   - Set Socket port to **4002** (paper) or **4001** (live)
   - Uncheck "Read-Only API"
   - Add `127.0.0.1` to Trusted IPs
4. Leave IB Gateway running

---

## Step 2: Configure the Bot

```bash
cd /Users/F8870709/trading

# Copy and edit the config file
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your settings:

```yaml
connection:
  mode: tws              # or "gateway"
  host: host.docker.internal  # Docker → host networking (use 127.0.0.1 if running without Docker)
  port: 7497             # TWS paper=7497, TWS live=7496, Gateway paper=4002, Gateway live=4001
  client_id: 1
  timeout: 30

capital:
  total_capital: 100000
  allocation_mode: equal_weight  # or fixed_amount, percentage

strategies:
  momentum:
    enabled: true
    frequency: 5min
    symbols: ["AAPL", "MSFT", "GOOGL"]
    asset_classes: ["equity"]
    parameters:
      lookback_period: 14
      momentum_threshold: 0.02

  ma_crossover:
    enabled: true
    frequency: 15min
    symbols: ["SPY", "QQQ"]
    asset_classes: ["equity"]
    parameters:
      fast_period: 10
      slow_period: 30
      ma_type: ema

risk:
  max_position_pct: 0.05
  max_drawdown_pct: 0.10
  max_daily_loss_pct: 0.02
  max_sector_concentration: 0.25

alerts:
  channels:
    slack_webhook_url: ""  # Optional: your Slack webhook
    email_smtp_host: ""    # Optional: SMTP settings
  routing:
    trade_executed: ["slack"]
    risk_breach: ["slack", "email"]

database:
  url: postgresql+asyncpg://bot:bot@postgres:5432/trading
  pool_size: 5

dashboard:
  port: 8080
```

---

## Step 3: Create the `.env` file

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Dashboard auth (optional, leave empty to disable)
DASHBOARD_AUTH_TOKEN=your-secret-token

# Ports (defaults shown)
DASHBOARD_PORT=8080
UI_PORT=3000

# For live trading (leave unset for paper)
# TRADING_BOT_CONFIRM_LIVE=yes
```

---

## Step 4: Deploy with Docker Compose

```bash
# Build all services
docker compose build

# Start everything (detached)
docker compose up -d

# Watch logs
docker compose logs -f trading-bot
```

This starts:

| Service | Port | Purpose |
|---------|------|---------|
| PostgreSQL | 5432 | Trade history, positions, backtest results |
| Redis | 6379 | Market data cache |
| Trading Bot | 8080 | The bot + REST API |
| Dashboard UI | 3000 | React monitoring frontend |

---

## Step 5: Verify It's Running

```bash
# Check all services are healthy
docker compose ps

# Test the health endpoint
curl http://localhost:8080/health

# View portfolio (with auth)
curl -H "Authorization: Bearer your-secret-token" http://localhost:8080/api/portfolio

# Open the dashboard
open http://localhost:3000
```

---

## Step 6: Monitor & Manage

### Dashboard UI

Open `http://localhost:3000` for:

- Real-time portfolio value, P&L, drawdown
- Open positions by strategy
- Strategy performance (Sharpe, win rate, profit factor)
- Risk utilization gauges
- Order history with audit trail

### Useful Commands

```bash
# Stop the bot gracefully
docker compose stop trading-bot

# Restart after config change (hot-reload handles strategy params,
# but structural changes need restart)
docker compose restart trading-bot

# View recent orders via API
curl -H "Authorization: Bearer your-secret-token" http://localhost:8080/api/orders

# Export trade history to CSV
curl -H "Authorization: Bearer your-secret-token" \
  "http://localhost:8080/api/export/csv?start=2024-01-01&end=2024-12-31"

# Tear everything down (keeps data volumes)
docker compose down

# Tear down AND delete data
docker compose down -v
```

---

## Running a Backtest

From the project root (not inside Docker):

```bash
pip install -e ".[dev]"

python -c "
import asyncio
from src.backtesting.engine import BacktestEngine
from src.strategies.implementations.momentum import MomentumStrategy
from src.config.settings import BacktestConfig, StrategyConfig

async def main():
    engine = BacktestEngine(BacktestConfig(slippage_bps=5))
    data = await engine.load_data('AAPL', source='csv', filepath='data/historical/AAPL.csv')
    config = StrategyConfig(
        enabled=True, frequency='daily', symbols=['AAPL'],
        asset_classes=['equity'], parameters={'lookback_period': 14}
    )
    strategy = MomentumStrategy(config, None)
    result = await engine.run(strategy, data)
    print(f'Return: {result.total_return:.2%}, Sharpe: {result.sharpe_ratio:.2f}')

asyncio.run(main())
"
```

---

## Running Without Docker (Development)

If you prefer running directly on your machine:

```bash
# Install dependencies
pip install -e ".[dev]"

# Start PostgreSQL and Redis locally (or use Docker just for those)
docker compose up -d postgres redis

# Run migrations
alembic upgrade head

# Start the bot
python -m src.main
```

---

## Available Strategies

| Strategy | Type | Key Parameters |
|----------|------|----------------|
| `momentum` | Trend | lookback_period, momentum_threshold |
| `ma_crossover` | Trend | fast_period, slow_period, ma_type |
| `trend_following` | Trend | fast_ma, slow_ma, atr_filter |
| `breakout` | Trend | consolidation_period, breakout_atr_multiple |
| `mean_reversion` | Mean-Reversion | lookback_period, z_score_threshold |
| `bollinger` | Mean-Reversion | bb_period, bb_std, entry_band |
| `rsi_divergence` | Mean-Reversion | rsi_period, overbought, oversold |
| `vwap` | Mean-Reversion | deviation_threshold, session_type |
| `pairs_trading` | Statistical | pair_symbols, entry_z, exit_z |
| `market_making` | Market Making | spread_bps, inventory_limit, skew_factor |
| `wheel` | Options (Wheel) | target_delta, min_dte, max_dte, roll_dte_threshold, vix_high_threshold, vix_reentry_threshold |

---

## API Endpoints

All endpoints (except `/health`) require `Authorization: Bearer <token>` when `DASHBOARD_AUTH_TOKEN` is set.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (no auth) |
| GET | `/api/portfolio` | Portfolio summary |
| GET | `/api/positions` | Current positions |
| GET | `/api/strategies` | Strategy performance metrics |
| GET | `/api/performance/{strategy}` | Per-strategy metrics |
| GET | `/api/risk` | Risk utilization |
| GET | `/api/orders` | Order history |
| GET | `/api/export/csv` | Export trades to CSV |
| GET | `/api/strategies/{name}/pnl` | Per-strategy P&L (realized + unrealized) |
| GET | `/api/strategies/comparison` | All strategies side-by-side with metrics |
| GET | `/api/strategies/{name}/history` | Equity curve time-series |
| GET | `/api/strategies/{name}/trades` | Paginated trades for one strategy |
| GET | `/api/trades` | All trades with filters (strategy, symbol, dates) |
| WS | `/ws/live` | Real-time streaming |

---

## Dashboard Pages

| Route | Page | Description |
|-------|------|-------------|
| `/` | Overview | Portfolio summary, positions, risk, strategy status |
| `/strategies` | Strategy Comparison | Side-by-side metrics, equity curve overlay |
| `/strategies/:name` | Strategy Detail | In-depth view: equity curve, trades, params |
| `/trades` | Trade History | Filterable, paginated trade history |

---

## Safety Notes

- The bot starts in **paper trading mode** by default (port 4002)
- To switch to live trading, change port to `4001` AND set `TRADING_BOT_CONFIRM_LIVE=yes`
- Automatic circuit breakers halt trading at 10% drawdown or 2% daily loss
- All orders have a full audit trail in the database
- Graceful shutdown cancels all pending orders before disconnecting
- Strategy isolation ensures one crashing strategy doesn't affect others
