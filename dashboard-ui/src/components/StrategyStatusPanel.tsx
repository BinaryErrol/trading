import { useStrategies } from '../hooks/useApi';

function StateIndicator({ state }: { state: string }) {
  const colors: Record<string, string> = {
    running: 'bg-green-500',
    paused: 'bg-yellow-500',
    halted: 'bg-red-500',
  };

  return (
    <span className="flex items-center gap-2">
      <span className={`w-2 h-2 rounded-full ${colors[state] || 'bg-gray-500'}`} />
      <span className="capitalize text-sm">{state}</span>
    </span>
  );
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

export function StrategyStatusPanel() {
  const { data: strategies, isLoading } = useStrategies();

  if (isLoading || !strategies) {
    return (
      <div className="bg-gray-800 rounded-lg p-6 animate-pulse">
        <div className="h-6 bg-gray-700 rounded w-1/4 mb-4" />
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-12 bg-gray-700 rounded" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-4">Strategy Status</h2>
      <div className="space-y-3">
        {strategies.map((strategy) => {
          const returnColor = strategy.total_return >= 0 ? 'text-green-400' : 'text-red-400';
          const returnSign = strategy.total_return >= 0 ? '+' : '';
          return (
            <div
              key={strategy.name}
              className="flex items-center justify-between p-3 bg-gray-700/40 rounded-lg"
            >
              <div className="flex items-center gap-4">
                <StateIndicator state={strategy.state} />
                <span className="font-medium">{strategy.name}</span>
              </div>
              <div className="flex items-center gap-6 text-sm">
                <div className="text-right">
                  <p className="text-gray-400">Allocation</p>
                  <p>{formatCurrency(strategy.allocation)}</p>
                </div>
                <div className="text-right">
                  <p className="text-gray-400">Return</p>
                  <p className={returnColor}>
                    {returnSign}{strategy.total_return.toFixed(1)}%
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-gray-400">Sharpe</p>
                  <p>{strategy.sharpe_ratio.toFixed(2)}</p>
                </div>
                <div className="text-right">
                  <p className="text-gray-400">Win Rate</p>
                  <p>{(strategy.win_rate * 100).toFixed(0)}%</p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
