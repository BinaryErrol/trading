import { useQuery } from '@tanstack/react-query';
import type {
  PortfolioSummary,
  Position,
  StrategyStatus,
  RiskMetrics,
  Order,
} from '../types';
import {
  mockPortfolio,
  mockPositions,
  mockStrategies,
  mockRisk,
  mockOrders,
} from '../mockData';

const USE_MOCK = true;

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
