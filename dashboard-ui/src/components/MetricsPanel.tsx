interface MetricsPanelProps {
  totalReturn: number;
  sharpeRatio: number;
  sortinoRatio: number;
  maxDrawdown: number;
  winRate: number;
  profitFactor: number;
}

export function MetricsPanel({
  totalReturn,
  sharpeRatio,
  sortinoRatio,
  maxDrawdown,
  winRate,
  profitFactor,
}: MetricsPanelProps) {
  const metrics = [
    {
      label: 'Total Return',
      value: `${totalReturn > 0 ? '+' : ''}${totalReturn.toFixed(1)}%`,
      color: totalReturn > 0 ? 'text-green-400' : totalReturn < 0 ? 'text-red-400' : 'text-gray-300',
    },
    {
      label: 'Sharpe Ratio',
      value: sharpeRatio.toFixed(2),
      color: sharpeRatio > 1 ? 'text-green-400' : sharpeRatio < 0 ? 'text-red-400' : 'text-gray-300',
    },
    {
      label: 'Sortino Ratio',
      value: sortinoRatio.toFixed(2),
      color: sortinoRatio > 1 ? 'text-green-400' : sortinoRatio < 0 ? 'text-red-400' : 'text-gray-300',
    },
    {
      label: 'Max Drawdown',
      value: `-${maxDrawdown.toFixed(1)}%`,
      color: 'text-red-400',
    },
    {
      label: 'Win Rate',
      value: `${(winRate * 100).toFixed(0)}%`,
      color: winRate > 0.5 ? 'text-green-400' : 'text-gray-300',
    },
    {
      label: 'Profit Factor',
      value: profitFactor.toFixed(2),
      color: profitFactor > 1 ? 'text-green-400' : profitFactor < 1 ? 'text-red-400' : 'text-gray-300',
    },
  ];

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-4">Key Metrics</h2>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        {metrics.map((metric) => (
          <div key={metric.label} className="bg-gray-700/50 rounded-lg p-4">
            <p className="text-gray-400 text-xs uppercase tracking-wide">{metric.label}</p>
            <p className={`text-xl font-bold mt-1 ${metric.color}`}>{metric.value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
