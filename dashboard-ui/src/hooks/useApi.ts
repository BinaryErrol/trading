import { useQuery } from '@tanstack/react-query';
import type {
  PortfolioSummary,
  Position,
  StrategyStatus,
  RiskMetrics,
  Order,
  StrategyComparison,
  StrategyPnL,
  EquityPoint,
  TradeDetail,
  PaginatedResponse,
} from '../types';
import {
  mockPortfolio,
  mockPositions,
  mockStrategies,
  mockRisk,
  mockOrders,
  mockStrategyComparisons,
  generateStrategyEquityCurve,
  getMockPaginatedTrades,
} from '../mockData';

const USE_MOCK = false;

async function fetchApi<T>(path: string, mockData: T): Promise<T> {
  if (USE_MOCK) {
    return mockData;
  }
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }
  return response.json();
}

export function usePortfolio() {
  return useQuery<PortfolioSummary>({
    queryKey: ['portfolio'],
    queryFn: () => fetchApi('/api/portfolio', mockPortfolio),
  });
}

export function usePositions() {
  return useQuery<Position[]>({
    queryKey: ['positions'],
    queryFn: () => fetchApi('/api/positions', mockPositions),
  });
}

export function useStrategies() {
  return useQuery<StrategyStatus[]>({
    queryKey: ['strategies'],
    queryFn: () => fetchApi('/api/strategies', mockStrategies),
  });
}

export function useRiskMetrics() {
  return useQuery<RiskMetrics>({
    queryKey: ['risk'],
    queryFn: () => fetchApi('/api/risk', mockRisk),
  });
}

export function useOrders() {
  return useQuery<Order[]>({
    queryKey: ['orders'],
    queryFn: () => fetchApi('/api/orders', mockOrders),
  });
}


export function useStrategyComparison() {
  return useQuery<StrategyComparison[]>({
    queryKey: ['strategyComparison'],
    queryFn: () => fetchApi('/api/strategies/comparison', mockStrategyComparisons),
  });
}

export function useStrategyPnL(name: string) {
  const mockPnl: StrategyPnL = {
    strategy_name: name,
    realized_pnl: mockStrategyComparisons.find((s) => s.name === name)?.realized_pnl ?? 0,
    unrealized_pnl: mockStrategyComparisons.find((s) => s.name === name)?.unrealized_pnl ?? 0,
    total_pnl:
      (mockStrategyComparisons.find((s) => s.name === name)?.realized_pnl ?? 0) +
      (mockStrategyComparisons.find((s) => s.name === name)?.unrealized_pnl ?? 0),
  };

  return useQuery<StrategyPnL>({
    queryKey: ['strategyPnl', name],
    queryFn: () => fetchApi(`/api/strategies/${name}/pnl`, mockPnl),
    enabled: !!name,
  });
}

export function useStrategyHistory(name: string) {
  return useQuery<EquityPoint[]>({
    queryKey: ['strategyHistory', name],
    queryFn: () => fetchApi(`/api/strategies/${name}/history`, generateStrategyEquityCurve(name)),
    enabled: !!name,
  });
}

export function useStrategyTrades(name: string, limit = 25, offset = 0) {
  const mockData = getMockPaginatedTrades(name, undefined, undefined, undefined, limit, offset);

  return useQuery<PaginatedResponse<TradeDetail>>({
    queryKey: ['strategyTrades', name, limit, offset],
    queryFn: () => fetchApi(`/api/strategies/${name}/trades?limit=${limit}&offset=${offset}`, mockData),
    enabled: !!name,
  });
}

export interface TradeFilters {
  strategy?: string;
  symbol?: string;
  startDate?: string;
  endDate?: string;
  limit?: number;
  offset?: number;
}

export function useTradeHistory(filters: TradeFilters) {
  const { strategy, symbol, startDate, endDate, limit = 25, offset = 0 } = filters;
  const mockData = getMockPaginatedTrades(strategy, symbol, startDate, endDate, limit, offset);

  const params = new URLSearchParams();
  if (strategy) params.set('strategy', strategy);
  if (symbol) params.set('symbol', symbol);
  if (startDate) params.set('start', startDate);
  if (endDate) params.set('end', endDate);
  params.set('limit', String(limit));
  params.set('offset', String(offset));

  return useQuery<PaginatedResponse<TradeDetail>>({
    queryKey: ['tradeHistory', strategy, symbol, startDate, endDate, limit, offset],
    queryFn: () => fetchApi(`/api/trades?${params.toString()}`, mockData),
  });
}
