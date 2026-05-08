import { useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { generateEquityCurve, generateStrategyPerformance } from '../mockData';

const STRATEGY_COLORS: Record<string, string> = {
  momentum: '#22c55e',
  trend_following: '#3b82f6',
  mean_reversion: '#a855f7',
  ma_crossover: '#f59e0b',
  breakout: '#ef4444',
  pairs_trading: '#06b6d4',
};

export function PerformanceCharts() {
  const equityCurve = useMemo(() => generateEquityCurve(), []);
  const strategyPerformance = useMemo(() => generateStrategyPerformance(), []);

  const strategies = useMemo(() => {
    if (strategyPerformance.length === 0) return [];
    return Object.keys(strategyPerformance[0]).filter((k) => k !== 'date');
  }, [strategyPerformance]);

  return (
    <div className="space-y-6">
      {/* Equity Curve */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Equity Curve</h2>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={equityCurve}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey="date"
              stroke="#9ca3af"
              tick={{ fontSize: 11 }}
              tickFormatter={(v: string) => v.slice(5)}
            />
            <YAxis
              stroke="#9ca3af"
              tick={{ fontSize: 11 }}
              tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
            />
            <Tooltip
              contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151' }}
              labelStyle={{ color: '#9ca3af' }}
              formatter={(value: number) => [`$${value.toLocaleString()}`, 'Equity']}
            />
            <Line
              type="monotone"
              dataKey="equity"
              stroke="#22c55e"
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Strategy Comparison */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Strategy Comparison (% Return)</h2>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={strategyPerformance}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey="date"
              stroke="#9ca3af"
              tick={{ fontSize: 11 }}
              tickFormatter={(v: string) => v.slice(5)}
            />
            <YAxis
              stroke="#9ca3af"
              tick={{ fontSize: 11 }}
              tickFormatter={(v: number) => `${v.toFixed(0)}%`}
            />
            <Tooltip
              contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151' }}
              labelStyle={{ color: '#9ca3af' }}
              formatter={(value: number, name: string) => [`${value.toFixed(2)}%`, name]}
            />
            <Legend />
            {strategies.map((strategy) => (
              <Line
                key={strategy}
                type="monotone"
                dataKey={strategy}
                stroke={STRATEGY_COLORS[strategy] || '#6b7280'}
                strokeWidth={1.5}
                dot={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
