import { useParams, Link } from 'react-router-dom';
import { EquityCurveChart } from '../components/EquityCurveChart';
import { MetricsPanel } from '../components/MetricsPanel';
import { StrategyParamsDisplay } from '../components/StrategyParamsDisplay';
import { useStrategyPnL, useStrategyHistory, useStrategyTrades } from '../hooks/useApi';
import { mockStrategyComparisons } from '../mockData';
import { mockPositions } from '../mockData';
import type { TradeDetail } from '../types';

export function StrategyDetailPage() {
  const { name } = useParams<{ name: string }>();
  const strategyName = name || '';

  const { data: pnl, isLoading: pnlLoading } = useStrategyPnL(strategyName);
  const { data: history, isLoading: historyLoading } = useStrategyHistory(strategyName);
  const { data: tradesData, isLoading: tradesLoading } = useStrategyTrades(strategyName);

  const strategyMetrics = mockStrategyComparisons.find((s) => s.name === strategyName);
  const openPositions = mockPositions.filter((p) => p.strategy_name === strategyName);

  const isLoading = pnlLoading || historyLoading || tradesLoading;

  if (isLoading) {
    return (
      <main className="p-6 max-w-screen-2xl mx-auto">
        <div className="animate-pulse space-y-6">
          <div className="h-8 bg-gray-700 rounded w-1/3" />
          <div className="h-48 bg-gray-800 rounded-lg" />
          <div className="h-64 bg-gray-800 rounded-lg" />
        </div>
      </main>
    );
  }

  return (
    <main className="p-6 space-y-6 max-w-screen-2xl mx-auto">
      <div className="flex items-center gap-3">
        <Link to="/strategies" className="text-gray-400 hover:text-gray-200 text-sm">
          ← Strategies
        </Link>
        <h1 className="text-2xl font-bold text-gray-200">{strategyName}</h1>
      </div>

      {/* P&L Summary */}
      {pnl && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-gray-800 rounded-lg p-4">
            <p className="text-gray-400 text-xs uppercase">Realized P&L</p>
            <p className={`text-xl font-bold mt-1 ${pnl.realized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              ${pnl.realized_pnl.toLocaleString()}
            </p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <p className="text-gray-400 text-xs uppercase">Unrealized P&L</p>
            <p className={`text-xl font-bold mt-1 ${pnl.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              ${pnl.unrealized_pnl.toLocaleString()}
            </p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <p className="text-gray-400 text-xs uppercase">Total P&L</p>
            <p className={`text-xl font-bold mt-1 ${pnl.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              ${pnl.total_pnl.toLocaleString()}
            </p>
          </div>
        </div>
      )}

      {/* Metrics Panel */}
      {strategyMetrics && (
        <MetricsPanel
          totalReturn={strategyMetrics.total_return}
          sharpeRatio={strategyMetrics.sharpe_ratio}
          sortinoRatio={strategyMetrics.sortino_ratio}
          maxDrawdown={strategyMetrics.max_drawdown}
          winRate={strategyMetrics.win_rate}
          profitFactor={strategyMetrics.profit_factor}
        />
      )}

      {/* Equity Curve */}
      {history && <EquityCurveChart data={history} />}

      {/* Strategy Parameters */}
      <StrategyParamsDisplay strategyName={strategyName} />

      {/* Open Positions */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Open Positions</h2>
        {openPositions.length === 0 ? (
          <p className="text-gray-400">No open positions.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 border-b border-gray-700">
                  <th className="text-left py-2 px-3">Symbol</th>
                  <th className="text-right py-2 px-3">Qty</th>
                  <th className="text-right py-2 px-3">Entry</th>
                  <th className="text-right py-2 px-3">Current</th>
                  <th className="text-right py-2 px-3">Unrealized P&L</th>
                </tr>
              </thead>
              <tbody>
                {openPositions.map((pos) => (
                  <tr key={pos.symbol} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                    <td className="py-2 px-3 font-medium">{pos.symbol}</td>
                    <td className="text-right py-2 px-3">{pos.quantity}</td>
                    <td className="text-right py-2 px-3">${pos.avg_entry_price.toFixed(2)}</td>
                    <td className="text-right py-2 px-3">${pos.current_price.toFixed(2)}</td>
                    <td className={`text-right py-2 px-3 font-medium ${pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent Trades */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Recent Trades</h2>
        {!tradesData || tradesData.items.length === 0 ? (
          <p className="text-gray-400">No recent trades.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 border-b border-gray-700">
                  <th className="text-left py-2 px-3">Symbol</th>
                  <th className="text-left py-2 px-3">Direction</th>
                  <th className="text-right py-2 px-3">Entry</th>
                  <th className="text-right py-2 px-3">Exit</th>
                  <th className="text-right py-2 px-3">Qty</th>
                  <th className="text-right py-2 px-3">P&L</th>
                  <th className="text-left py-2 px-3">Date</th>
                </tr>
              </thead>
              <tbody>
                {tradesData.items.map((trade: TradeDetail) => (
                  <tr key={trade.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                    <td className="py-2 px-3 font-medium">{trade.symbol}</td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-xs ${
                        trade.direction.includes('BUY') ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'
                      }`}>
                        {trade.direction}
                      </span>
                    </td>
                    <td className="text-right py-2 px-3">${trade.entry_price.toFixed(2)}</td>
                    <td className="text-right py-2 px-3">
                      {trade.exit_price != null ? `$${trade.exit_price.toFixed(2)}` : '—'}
                    </td>
                    <td className="text-right py-2 px-3">{trade.quantity}</td>
                    <td className={`text-right py-2 px-3 font-medium ${trade.realized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.realized_pnl >= 0 ? '+' : ''}${trade.realized_pnl.toFixed(0)}
                    </td>
                    <td className="py-2 px-3 text-gray-400 text-xs">
                      {trade.opened_at.split('T')[0]}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </main>
  );
}
