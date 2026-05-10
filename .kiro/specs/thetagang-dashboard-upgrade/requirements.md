# Requirements Document

## Introduction

This feature adds a ThetaGang-style Options Wheel strategy to the IBKR Trading Bot, along with per-strategy P&L tracking and a multi-page React dashboard. The Wheel strategy sells cash-secured puts to enter positions and writes covered calls on assigned shares, cycling through the "wheel" continuously. The dashboard upgrade provides strategy-level performance comparison and detailed drill-down views.

## Glossary

- **Wheel_Strategy**: An options income strategy that sells cash-secured puts on target underlyings; upon assignment, sells covered calls on the resulting shares until called away, then repeats.
- **Strategy_Engine**: The existing `StrategyEngine` class that orchestrates strategy evaluation cycles and dispatches signals to the order manager.
- **Risk_Manager**: The existing `RiskManager` module that enforces position limits, drawdown controls, and portfolio-level risk checks.
- **Capital_Allocator**: The existing `CapitalAllocator` that distributes available capital across enabled strategies.
- **Dashboard_API**: The FastAPI backend serving REST endpoints and WebSocket feeds to the frontend.
- **Dashboard_UI**: The React + TypeScript + Tailwind frontend application.
- **DTE**: Days to expiration — the number of calendar days remaining until an option contract expires.
- **Delta**: The option Greek measuring sensitivity of option price to underlying price movement; used as a proxy for probability of assignment.
- **VIX**: The CBOE Volatility Index, used as a market regime indicator.
- **Rolling**: Closing an existing option position and simultaneously opening a new position at a later expiration date.
- **Assignment**: When a short put option is exercised by the counterparty, resulting in the seller purchasing shares at the strike price.
- **P&L_Tracker**: A component that records realized and unrealized profit/loss per strategy over time.
- **Equity_Curve**: A time-series of cumulative returns for a strategy, plotted as a line chart.

## Requirements

### Requirement 1: Put Selling Signal Generation

**User Story:** As a trader, I want the Wheel strategy to generate put-selling signals when market conditions are favorable, so that I can collect premium income on underlyings I am willing to own.

#### Acceptance Criteria

1. WHEN the Wheel_Strategy evaluation cycle runs AND no existing short put position exists for a symbol AND available buying power exceeds the cash required to secure the put, THE Wheel_Strategy SHALL generate a SELL PUT signal for that symbol.
2. THE Wheel_Strategy SHALL select a put strike with a delta closest to the configured target delta (default 0.30) from the available option chain.
3. THE Wheel_Strategy SHALL select an expiration date within the configured DTE range (default 30 to 45 days).
4. IF no option contract exists within the configured DTE range for a symbol, THEN THE Wheel_Strategy SHALL skip signal generation for that symbol and log the reason.
5. IF available buying power is insufficient to secure a put at the selected strike, THEN THE Wheel_Strategy SHALL skip signal generation for that symbol and log the reason.

### Requirement 2: Covered Call Writing on Assigned Shares

**User Story:** As a trader, I want the Wheel strategy to automatically write covered calls on shares received through put assignment, so that I continue collecting premium while holding the position.

#### Acceptance Criteria

1. WHEN the Wheel_Strategy detects that shares have been assigned from a short put (position quantity >= 100 shares for the underlying), THE Wheel_Strategy SHALL generate a SELL CALL signal for that underlying.
2. THE Wheel_Strategy SHALL select a call strike with a delta closest to the configured target delta (default 0.30) from the available option chain.
3. THE Wheel_Strategy SHALL select a call expiration within the configured DTE range (default 30 to 45 days).
4. THE Wheel_Strategy SHALL generate call signals only for complete lots of 100 shares.
5. IF the underlying already has an open short call position, THEN THE Wheel_Strategy SHALL skip call signal generation for that underlying.

### Requirement 3: Option Rolling Logic

**User Story:** As a trader, I want expiring options to be rolled forward rather than expiring worthless or being assigned at unfavorable prices, so that I maintain premium income and avoid unnecessary assignment.

#### Acceptance Criteria

1. WHEN a short option position has fewer than the configured minimum DTE remaining (default 7 days) AND the position is profitable, THE Wheel_Strategy SHALL generate a ROLL signal consisting of a BUY-TO-CLOSE on the current position and a SELL-TO-OPEN at a new expiration within the configured DTE range.
2. WHEN a short put is within the rolling window AND the put is in-the-money, THE Wheel_Strategy SHALL evaluate whether rolling down-and-out for a net credit is possible before generating a roll signal.
3. IF rolling would result in a net debit, THEN THE Wheel_Strategy SHALL skip the roll and allow the existing position to proceed to expiration.
4. THE Wheel_Strategy SHALL log all rolling decisions with the current position details, proposed new position, and net credit or debit amount.

### Requirement 4: VIX-Based Regime Detection

**User Story:** As a trader, I want the Wheel strategy to reduce activity during high-volatility market environments, so that I avoid selling options when premiums reflect elevated risk of large moves.

#### Acceptance Criteria

1. WHILE the VIX level exceeds the configured high-volatility threshold (default 30), THE Wheel_Strategy SHALL suppress new put-selling signals.
2. WHILE the VIX level exceeds the configured high-volatility threshold, THE Wheel_Strategy SHALL continue managing existing positions (rolling, call writing) without suppression.
3. WHEN the VIX level drops below the configured re-entry threshold (default 25), THE Wheel_Strategy SHALL resume normal put-selling signal generation.
4. THE Wheel_Strategy SHALL retrieve the current VIX level from the Market_Data_Hub at the start of each evaluation cycle.
5. IF VIX data is unavailable, THEN THE Wheel_Strategy SHALL log a warning and proceed with the last known VIX value, or suppress new puts if no VIX data has ever been received.

### Requirement 5: Integration with Risk Manager and Capital Allocator

**User Story:** As a trader, I want the Wheel strategy to respect existing risk limits and capital allocation rules, so that it operates within the portfolio's overall risk budget.

#### Acceptance Criteria

1. THE Wheel_Strategy SHALL request capital allocation from the Capital_Allocator before generating any signal, and respect the allocated amount as the maximum notional exposure.
2. WHEN the Wheel_Strategy generates a signal, THE Risk_Manager SHALL validate the signal against position concentration limits, drawdown limits, and daily loss limits before the signal is forwarded to the order manager.
3. THE Wheel_Strategy SHALL implement the `BaseStrategy` interface (evaluate, required_indicators, validate_capital) and register with the Strategy_Engine like all other strategies.
4. THE Wheel_Strategy SHALL declare `asset_classes: ["option"]` in its configuration and support the `option` asset class in signal generation.
5. IF the Risk_Manager rejects a signal, THEN THE Wheel_Strategy SHALL log the rejection reason and skip that trade opportunity.

### Requirement 6: Wheel Strategy Configuration

**User Story:** As a trader, I want to configure the Wheel strategy parameters through the existing config.yaml file, so that I can tune behavior without code changes.

#### Acceptance Criteria

1. THE Wheel_Strategy SHALL read its parameters from the `strategies.wheel` section of config.yaml, following the same structure as existing strategies (enabled, frequency, symbols, asset_classes, parameters).
2. THE Wheel_Strategy SHALL support the following configurable parameters: target_delta (float), min_dte (int), max_dte (int), roll_dte_threshold (int), vix_high_threshold (float), vix_reentry_threshold (float), and max_positions_per_symbol (int).
3. WHEN config.yaml is updated at runtime, THE Wheel_Strategy SHALL pick up new parameter values through the existing hot-reload mechanism (update_parameters method).
4. IF a required parameter is missing from config.yaml, THEN THE Wheel_Strategy SHALL use documented default values and log a warning indicating which defaults are in use.

### Requirement 7: Per-Strategy P&L Tracking

**User Story:** As a trader, I want to see real-time profit and loss for each strategy separately, so that I can identify which strategies are making money and which are losing.

#### Acceptance Criteria

1. THE P&L_Tracker SHALL compute realized P&L per strategy by summing closed trade profits and losses attributed to that strategy.
2. THE P&L_Tracker SHALL compute unrealized P&L per strategy by marking open positions to current market prices.
3. THE P&L_Tracker SHALL update unrealized P&L values at least every 60 seconds while the market is open.
4. THE Dashboard_API SHALL expose a `GET /api/strategies/{name}/pnl` endpoint returning realized P&L, unrealized P&L, and total P&L for the specified strategy.
5. THE Dashboard_API SHALL expose a `GET /api/strategies/comparison` endpoint returning P&L and key metrics for all strategies in a single response.

### Requirement 8: Historical Performance Tracking

**User Story:** As a trader, I want to view historical performance over time for each strategy, so that I can evaluate strategy effectiveness across different market conditions.

#### Acceptance Criteria

1. THE P&L_Tracker SHALL record daily end-of-day equity snapshots per strategy to the database.
2. THE Dashboard_API SHALL expose a `GET /api/strategies/{name}/history` endpoint returning the equity curve time-series for the specified strategy, with optional date range filtering via `start` and `end` query parameters.
3. THE Dashboard_API SHALL expose a `GET /api/strategies/{name}/trades` endpoint returning individual trade records for the specified strategy, with pagination support (limit, offset query parameters).
4. WHEN a trade is closed, THE P&L_Tracker SHALL record the trade with strategy name, symbol, entry price, exit price, quantity, realized P&L, entry timestamp, and exit timestamp.

### Requirement 9: Multi-Page Dashboard Navigation

**User Story:** As a trader, I want the dashboard to have multiple pages with navigation, so that I can access detailed views without cluttering the overview.

#### Acceptance Criteria

1. THE Dashboard_UI SHALL implement client-side routing with the following pages: Overview (/), Strategy Comparison (/strategies), Strategy Detail (/strategies/:name), and Trade History (/trades).
2. THE Dashboard_UI SHALL display a persistent navigation bar allowing access to all top-level pages from any page.
3. WHEN a user navigates to a page, THE Dashboard_UI SHALL update the browser URL and support browser back/forward navigation.
4. THE Dashboard_UI SHALL preserve the existing Overview page content (portfolio summary, positions, risk gauges, strategy status, performance charts, order history) at the root route (/).

### Requirement 10: Strategy Comparison Page

**User Story:** As a trader, I want a dedicated page comparing all strategies side-by-side, so that I can quickly assess relative performance.

#### Acceptance Criteria

1. THE Dashboard_UI SHALL display a comparison table on the Strategy Comparison page showing each strategy's total return, Sharpe ratio, Sortino ratio, max drawdown, win rate, profit factor, and total trades.
2. THE Dashboard_UI SHALL display a chart overlaying equity curves for all strategies on the Strategy Comparison page.
3. THE Dashboard_UI SHALL highlight strategies with positive total return in green and negative total return in red.
4. WHEN a user clicks a strategy name in the comparison table, THE Dashboard_UI SHALL navigate to that strategy's detail page.

### Requirement 11: Strategy Detail Page

**User Story:** As a trader, I want a detailed page for each strategy showing its equity curve, trades, open positions, metrics, and parameters, so that I can deeply analyze individual strategy performance.

#### Acceptance Criteria

1. THE Dashboard_UI SHALL display the following sections on the Strategy Detail page: equity curve chart, key metrics panel, open positions table, recent trades table, and strategy parameters display.
2. THE Dashboard_UI SHALL render the equity curve using Recharts with time on the x-axis and cumulative P&L on the y-axis.
3. THE Dashboard_UI SHALL display open positions for the strategy with symbol, quantity, entry price, current price, and unrealized P&L.
4. THE Dashboard_UI SHALL display recent trades for the strategy with symbol, direction, entry price, exit price, quantity, realized P&L, and date.
5. THE Dashboard_UI SHALL display the strategy's current configuration parameters in a readable format.

### Requirement 12: Trade History Page

**User Story:** As a trader, I want a filterable trade history page, so that I can review past trades across all strategies with flexible search criteria.

#### Acceptance Criteria

1. THE Dashboard_UI SHALL display a paginated table of all trades on the Trade History page, ordered by date descending.
2. THE Dashboard_UI SHALL provide filter controls for: strategy name (dropdown), date range (start and end date pickers), and symbol (text input).
3. WHEN filters are applied, THE Dashboard_UI SHALL update the displayed trades to match the filter criteria without a full page reload.
4. THE Dashboard_UI SHALL display each trade row with: strategy name, symbol, direction, entry price, exit price, quantity, realized P&L, and trade date.
5. THE Dashboard_UI SHALL support pagination with configurable page size (default 25 trades per page).
