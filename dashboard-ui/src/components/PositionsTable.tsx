import { usePositions } from '../hooks/useApi';

export function PositionsTable() {
  const { data: positions, isLoading } = usePositions();

  if (isLoading || !positions) {
    return (
      <div className="bg-gray-800 rounded-lg p-6 animate-pulse">
        <div className="h-6 bg-gray-700 rounded w-1/4 mb-4" />
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-8 bg-gray-700 rounded" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-4">Positions</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              <th className="text-left py-2 px-3">Symbol</th>
              <th className="text-right py-2 px-3">Qty</th>
              <th className="text-right py-2 px-3">Entry</th>
              <th className="text-right py-2 px-3">Current</th>
              <th className="text-right py-2 px-3">P&L</th>
              <th className="text-left py-2 px-3">Strategy</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((pos) => {
              const pnlColor = pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400';
              const pnlSign = pos.unrealized_pnl >= 0 ? '+' : '';
              return (
                <tr key={`${pos.symbol}-${pos.strategy_name}`} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="py-2 px-3 font-medium">{pos.symbol}</td>
                  <td className={`text-right py-2 px-3 ${pos.quantity < 0 ? 'text-red-300' : ''}`}>
                    {pos.quantity}
                  </td>
                  <td className="text-right py-2 px-3">${pos.avg_entry_price.toFixed(2)}</td>
                  <td className="text-right py-2 px-3">${pos.current_price.toFixed(2)}</td>
                  <td className={`text-right py-2 px-3 font-medium ${pnlColor}`}>
                    {pnlSign}${pos.unrealized_pnl.toFixed(0)}
                  </td>
                  <td className="py-2 px-3">
                    <span className="bg-gray-700 text-gray-300 px-2 py-0.5 rounded text-xs">
                      {pos.strategy_name}
                    </span>
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
