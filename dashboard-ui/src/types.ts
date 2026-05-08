export interface PortfolioSummary {
  total_value: number;
  unrealized_pnl: number;
  peak_equity: number;
  drawdown_pct: number;
}

export interface Position {
  symbol: string;
  asset_class: string;
  strategy_name: string;
  quantity: number;
  avg_entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
}

export interface StrategyStatus {
  name: string;
  state: 'running' | 'paused' | 'halted';
  total_return: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  allocation: number;
}

export interface RiskMetrics {
  portfolio_value: number;
  peak_equity: number;
  drawdown_pct: number;
  unrealized_pnl: number;
  position_count: number;
}

export interface Order {
  id: number;
  ibkr_order_id: number | null;
  strategy_name: string;
  symbol: string;
  direction: string;
  order_type: string;
  quantity: number;
  limit_price: number | null;
  stop_price: number | null;
  status: string;
  filled_quantity: number;
  avg_fill_price: number | null;
  submitted_at: string;
  filled_at: string | null;
  cancelled_at: string | null;
}

export interface EquityCurvePoint {
  date: string;
  equity: number;
}

export interface StrategyPerformancePoint {
  date: string;
  [strategy: string]: number | string;
}

export interface WebSocketMessage {
  type: string;
  data: Record<string, unknown>;
}
