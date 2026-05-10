import { Link } from 'react-router-dom';
import type { StrategyComparison } from '../types';

interface ComparisonTableProps {
  strategies: StrategyComparison[];
}

export function ComparisonTable({ strategies }: ComparisonTableProps) {
  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-4">Strategy Comparison</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              <th className="text-left py-2 px-3">Strategy</th>
              <th className="text-right py-2 px-3">Return</th>
              <th className="text-right py-2 px-3">Sharpe</th>
              <th className="text-right py-2 px-3">Sortino</th>
              <th className="text-right py-2 px-3">Max DD</th>
              <th className="text-right py-2 px-3">Win Rate</th>
              <th className="text-right py-2 px-3">Profit Factor</th>
              <th className="text-right py-2 px-3">Trades</th>
              <th className="text-right py-2 px-3">Realized P&L</th>
              <th className="text-right py-2 px-3">Unrealized P&L</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map((s) => {
              const returnColor = s.total_return > 0 ? 'text-green-400' : s.total_return < 0 ? 'text-red-400' : 'text-gray-300';
              const pnlColor = (val: number) => val > 0 ? 'text-green-400' : val < 0 ? 'text-red-400' : 'text-gray-300';

              return (
                <tr key={s.name} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="py-2 px-3">
                    <Link
                      to={`/strategies/${s.name}`}
                      className="text-blue-400 hover:text-blue-300 font-medium"
                    >
                      {s.name}
                    </Link>
                  </td>
                  <td className={`text-right py-2 px-3 font-medium ${returnColor}`}>
                    {s.total_return > 0 ? '+' : ''}{s.total_return.toFixed(1)}%
                  </td>
                  <td className="text-right py-2 px-3">{s.sharpe_ratio.toFixed(2)}</td>
                  <td className="text-right py-2 px-3">{s.sortino_ratio.toFixed(2)}</td>
                  <td className="text-right py-2 px-3 text-red-400">-{s.max_drawdown.toFixed(1)}%</td>
                  <td className="text-right py-2 px-3">{(s.win_rate * 100).toFixed(0)}%</td>
                  <td className="text-right py-2 px-3">{s.profit_factor.toFixed(2)}</td>
                  <td className="text-right py-2 px-3">{s.total_trades}</td>
                  <td className={`text-right py-2 px-3 ${pnlColor(s.realized_pnl)}`}>
                    ${s.realized_pnl.toLocaleString()}
                  </td>
                  <td className={`text-right py-2 px-3 ${pnlColor(s.unrealized_pnl)}`}>
                    ${s.unrealized_pnl.toLocaleString()}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
