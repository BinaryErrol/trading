import type {
  PortfolioSummary,
  Position,
  StrategyStatus,
  RiskMetrics,
  Order,
  EquityCurvePoint,
  StrategyPerformancePoint,
} from './types';

export const mockPortfolio: PortfolioSummary = {
  total_value: 1_045_230.5,
  unrealized_pnl: 12_450.75,
  peak_equity: 1_052_000.0,
  drawdown_pct: 0.64,
};

export const mockPositions: Position[] = [
  {
    symbol: 'AAPL',
    asset_class: 'STK',
    strategy_name: 'momentum',
    quantity: 150,
    avg_entry_price: 178.5,
    current_price: 185.2,
    unrealized_pnl: 1005.0,
    realized_pnl: 320.0,
  },
  {
    symbol: 'MSFT',
    asset_class: 'STK',
    strategy_name: 'trend_following',
    quantity: 80,
    avg_entry_price: 410.0,
    current_price: 422.5,
    unrealized_pnl: 1000.0,
    realized_pnl: 540.0,
  },
  {
    symbol: 'GOOGL',
    asset_class: 'STK',
    strategy_name: 'mean_reversion',
    quantity: -50,
    avg_entry_price: 155.0,
    current_price: 152.3,
    unrealized_pnl: 135.0,
    realized_pnl: 0.0,
  },
  {
    symbol: 'TSLA',
    asset_class: 'STK',
    strategy_name: 'breakout',
    quantity: 60,
    avg_entry_price: 245.0,
    current_price: 238.7,
    unrealized_pnl: -378.0,
    realized_pnl: 150.0,
  },
  {
    symbol: 'NVDA',
    asset_class: 'STK',
    strategy_name: 'momentum',
    quantity: 40,
    avg_entry_price: 875.0,
    current_price: 920.5,
    unrealized_pnl: 1820.0,
    realized_pnl: 2100.0,
  },
  {
    symbol: 'SPY',
    asset_class: 'STK',
    strategy_name: 'ma_crossover',
    quantity: 200,
    avg_entry_price: 520.0,
    current_price: 525.8,
    unrealized_pnl: 1160.0,
    realized_pnl: 890.0,
  },
];

export const mockStrategies: StrategyStatus[] = [
  {
    name: 'momentum',
    state: 'running',
    total_return: 12.5,
    sharpe_ratio: 1.85,
    sortino_ratio: 2.1,
    max_drawdown: 4.2,
    win_rate: 0.62,
    profit_factor: 1.95,
    total_trades: 145,
    allocation: 200000,
  },
  {
    name: 'trend_following',
    state: 'running',
    total_return: 8.3,
    sharpe_ratio: 1.42,
    sortino_ratio: 1.7,
    max_drawdown: 5.8,
    win_rate: 0.55,
    profit_factor: 1.65,
    total_trades: 89,
    allocation: 180000,
  },
  {
    name: 'mean_reversion',
    state: 'running',
    total_return: 6.1,
    sharpe_ratio: 1.2,
    sortino_ratio: 1.5,
    max_drawdown: 3.1,
    win_rate: 0.68,
    profit_factor: 1.45,
    total_trades: 210,
    allocation: 150000,
  },
  {
    name: 'breakout',
    state: 'paused',
    total_return: -1.2,
    sharpe_ratio: 0.3,
    sortino_ratio: 0.4,
    max_drawdown: 7.5,
    win_rate: 0.42,
    profit_factor: 0.85,
    total_trades: 67,
    allocation: 120000,
  },
  {
    name: 'ma_crossover',
    state: 'running',
    total_return: 4.8,
    sharpe_ratio: 1.1,
    sortino_ratio: 1.3,
    max_drawdown: 4.0,
    win_rate: 0.58,
    profit_factor: 1.35,
    total_trades: 112,
    allocation: 160000,
  },
  {
    name: 'pairs_trading',
    state: 'halted',
    total_return: -3.5,
    sharpe_ratio: -0.2,
    sortino_ratio: -0.1,
    max_drawdown: 9.2,
    win_rate: 0.38,
    profit_factor: 0.72,
    total_trades: 34,
    allocation: 100000,
  },
];

export const mockRisk: RiskMetrics = {
  portfolio_value: 1_045_230.5,
  peak_equity: 1_052_000.0,
  drawdown_pct: 0.64,
  unrealized_pnl: 12_450.75,
  position_count: 6,
};

export const mockOrders: Order[] = [
  {
    id: 1,
    ibkr_order_id: 10001,
    strategy_name: 'momentum',
    symbol: 'AAPL',
    direction: 'BUY',
    order_type: 'LMT',
    quantity: 150,
    limit_price: 179.0,
    stop_price: null,
    status: 'Filled',
    filled_quantity: 150,
    avg_fill_price: 178.5,
    submitted_at: '2024-01-15T09:31:00',
    filled_at: '2024-01-15T09:31:05',
    cancelled_at: null,
  },
  {
    id: 2,
    ibkr_order_id: 10002,
    strategy_name: 'trend_following',
    symbol: 'MSFT',
    direction: 'BUY',
    order_type: 'MKT',
    quantity: 80,
    limit_price: null,
    stop_price: null,
    status: 'Filled',
    filled_quantity: 80,
    avg_fill_price: 410.0,
    submitted_at: '2024-01-15T10:15:00',
    filled_at: '2024-01-15T10:15:02',
    cancelled_at: null,
  },
  {
    id: 3,
    ibkr_order_id: 10003,
    strategy_name: 'mean_reversion',
    symbol: 'GOOGL',
    direction: 'SELL',
    order_type: 'LMT',
    quantity: 50,
    limit_price: 155.5,
    stop_price: null,
    status: 'Filled',
    filled_quantity: 50,
    avg_fill_price: 155.0,
    submitted_at: '2024-01-15T11:00:00',
    filled_at: '2024-01-15T11:00:08',
    cancelled_at: null,
  },
  {
    id: 4,
    ibkr_order_id: 10004,
    strategy_name: 'breakout',
    symbol: 'TSLA',
    direction: 'BUY',
    order_type: 'STP',
    quantity: 60,
    limit_price: null,
    stop_price: 244.0,
    status: 'Filled',
    filled_quantity: 60,
    avg_fill_price: 245.0,
    submitted_at: '2024-01-16T09:45:00',
    filled_at: '2024-01-16T09:45:12',
    cancelled_at: null,
  },
  {
    id: 5,
    ibkr_order_id: 10005,
    strategy_name: 'momentum',
    symbol: 'NVDA',
    direction: 'BUY',
    order_type: 'LMT',
    quantity: 40,
    limit_price: 880.0,
    stop_price: null,
    status: 'Filled',
    filled_quantity: 40,
    avg_fill_price: 875.0,
    submitted_at: '2024-01-16T10:30:00',
    filled_at: '2024-01-16T10:30:04',
    cancelled_at: null,
  },
  {
    id: 6,
    ibkr_order_id: 10006,
    strategy_name: 'ma_crossover',
    symbol: 'SPY',
    direction: 'BUY',
    order_type: 'MKT',
    quantity: 200,
    limit_price: null,
    stop_price: null,
    status: 'Filled',
    filled_quantity: 200,
    avg_fill_price: 520.0,
    submitted_at: '2024-01-17T09:30:00',
    filled_at: '2024-01-17T09:30:01',
    cancelled_at: null,
  },
  {
    id: 7,
    ibkr_order_id: 10007,
    strategy_name: 'momentum',
    symbol: 'AMD',
    direction: 'BUY',
    order_type: 'LMT',
    quantity: 100,
    limit_price: 165.0,
    stop_price: null,
    status: 'Cancelled',
    filled_quantity: 0,
    avg_fill_price: null,
    submitted_at: '2024-01-17T14:00:00',
    filled_at: null,
    cancelled_at: '2024-01-17T14:05:00',
  },
];

export function generateEquityCurve(): EquityCurvePoint[] {
  const points: EquityCurvePoint[] = [];
  let equity = 1_000_000;
  const startDate = new Date('2024-01-01');

  for (let i = 0; i < 90; i++) {
    const date = new Date(startDate);
    date.setDate(date.getDate() + i);
    // Skip weekends
    if (date.getDay() === 0 || date.getDay() === 6) continue;

    const dailyReturn = (Math.random() - 0.48) * 0.015;
    equity *= 1 + dailyReturn;
    points.push({
      date: date.toISOString().split('T')[0],
      equity: Math.round(equity * 100) / 100,
    });
  }
  return points;
}

export function generateStrategyPerformance(): StrategyPerformancePoint[] {
  const points: StrategyPerformancePoint[] = [];
  const strategies = ['momentum', 'trend_following', 'mean_reversion', 'ma_crossover'];
  const startDate = new Date('2024-01-01');
  const returns: Record<string, number> = {};
  strategies.forEach((s) => (returns[s] = 0));

  for (let i = 0; i < 90; i++) {
    const date = new Date(startDate);
    date.setDate(date.getDate() + i);
    if (date.getDay() === 0 || date.getDay() === 6) continue;

    const point: StrategyPerformancePoint = {
      date: date.toISOString().split('T')[0],
    };

    strategies.forEach((s) => {
      const dailyReturn = (Math.random() - 0.47) * 0.8;
      returns[s] += dailyReturn;
      point[s] = Math.round(returns[s] * 100) / 100;
    });

    points.push(point);
  }
  return points;
}
