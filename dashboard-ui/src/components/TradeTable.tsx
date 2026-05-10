import type { TradeDetail } from '../types';

interface TradeTableProps {
  trades: TradeDetail[];
}

export function TradeTable({ trades }: TradeTableProps) {
  if (trades.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-6">
        <p className="text-gray-400">No trades match the current filters.</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              <th className="text-left py-2 px-3">Strategy</th>
              <th className="text-left py-2 px-3">Symbol</th>
              <th className="text-left py-2 px-3">Direction</th>
              <th className="text-right py-2 px-3">Entry</th>
              <th className="text-right py-2 px-3">Exit</th>
              <th className="text-right py-2 px-3">Qty</th>
              <th className="text-right py-2 px-3">Realized P&L</th>
              <th className="text-left py-2 px-3">Date</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade) => (
              <tr key={trade.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                <td className="py-2 px-3">
                  <span className="bg-gray-700 text-gray-300 px-2 py-0.5 rounded text-xs">
                    {trade.strategy_name}
                  </span>
                </td>
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
    </div>
  );
}
