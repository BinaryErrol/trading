import { useMemo } from 'react';
import { ComparisonTable } from '../components/ComparisonTable';
import { EquityCurveOverlay } from '../components/EquityCurveOverlay';
import { useStrategyComparison } from '../hooks/useApi';
import { generateStrategyEquityCurve } from '../mockData';
import type { EquityPoint } from '../types';

export function StrategyComparisonPage() {
  const { data: strategies, isLoading } = useStrategyComparison();

  const equityCurves = useMemo<Record<string, EquityPoint[]>>(() => {
    if (!strategies) return {};
    const curves: Record<string, EquityPoint[]> = {};
    for (const s of strategies) {
      curves[s.name] = generateStrategyEquityCurve(s.name);
    }
    return curves;
  }, [strategies]);

  if (isLoading || !strategies) {
    return (
      <main className="p-6 max-w-screen-2xl mx-auto">
        <div className="animate-pulse space-y-6">
          <div className="h-8 bg-gray-700 rounded w-1/4" />
          <div className="h-64 bg-gray-800 rounded-lg" />
          <div className="h-96 bg-gray-800 rounded-lg" />
        </div>
      </main>
    );
  }

  return (
    <main className="p-6 space-y-6 max-w-screen-2xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-200">Strategy Comparison</h1>
      <ComparisonTable strategies={strategies} />
      <EquityCurveOverlay curves={equityCurves} />
    </main>
  );
}
