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
import type { EquityPoint } from '../types';

const STRATEGY_COLORS: Record<string, string> = {
  momentum: '#22c55e',
  trend_following: '#3b82f6',
  mean_reversion: '#a855f7',
  ma_crossover: '#f59e0b',
  breakout: '#ef4444',
  pairs_trading: '#06b6d4',
  wheel: '#ec4899',
};

interface EquityCurveOverlayProps {
  curves: Record<string, EquityPoint[]>;
}

export function EquityCurveOverlay({ curves }: EquityCurveOverlayProps) {
  const chartData = useMemo(() => {
    const dateMap = new Map<string, Record<string, number>>();

    for (const [name, points] of Object.entries(curves)) {
      for (const point of points) {
        const existing = dateMap.get(point.date) || {};
        existing[name] = point.equity;
        dateMap.set(point.date, existing);
      }
    }

    return Array.from(dateMap.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, values]) => ({ date, ...values }));
  }, [curves]);

  const strategyNames = Object.keys(curves);

  if (strategyNames.length === 0) {
    return null;
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-4">Equity Curves</h2>
      <ResponsiveContainer width="100%" height={350}>
        <LineChart data={chartData}>
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
            formatter={(value: number, name: string) => [`$${value.toLocaleString()}`, name]}
          />
          <Legend />
          {strategyNames.map((name) => (
            <Line
              key={name}
              type="monotone"
              dataKey={name}
              stroke={STRATEGY_COLORS[name] || '#6b7280'}
              strokeWidth={1.5}
              dot={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
