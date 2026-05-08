import { usePortfolio } from '../hooks/useApi';

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

export function PortfolioSummaryPanel() {
  const { data: portfolio, isLoading } = usePortfolio();

  if (isLoading || !portfolio) {
    return (
      <div className="bg-gray-800 rounded-lg p-6 animate-pulse">
        <div className="h-6 bg-gray-700 rounded w-1/3 mb-4" />
        <div className="h-10 bg-gray-700 rounded w-2/3" />
      </div>
    );
  }

  const pnlColor = portfolio.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400';
  const pnlSign = portfolio.unrealized_pnl >= 0 ? '+' : '';

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div className="bg-gray-800 rounded-lg p-6">
        <p className="text-gray-400 text-sm uppercase tracking-wide">Total Value</p>
        <p className="text-3xl font-bold mt-1">
          {formatCurrency(portfolio.total_value)}
        </p>
        <p className="text-gray-500 text-sm mt-1">
          Peak: {formatCurrency(portfolio.peak_equity)}
        </p>
      </div>

      <div className="bg-gray-800 rounded-lg p-6">
        <p className="text-gray-400 text-sm uppercase tracking-wide">Unrealized P&L</p>
        <p className={`text-3xl font-bold mt-1 ${pnlColor}`}>
          {pnlSign}{formatCurrency(portfolio.unrealized_pnl)}
        </p>
        <p className="text-gray-500 text-sm mt-1">
          {pnlSign}{((portfolio.unrealized_pnl / portfolio.total_value) * 100).toFixed(2)}%
        </p>
      </div>

      <div className="bg-gray-800 rounded-lg p-6">
        <p className="text-gray-400 text-sm uppercase tracking-wide">Drawdown</p>
        <p className={`text-3xl font-bold mt-1 ${portfolio.drawdown_pct > 5 ? 'text-red-400' : portfolio.drawdown_pct > 2 ? 'text-yellow-400' : 'text-green-400'}`}>
          -{portfolio.drawdown_pct.toFixed(2)}%
        </p>
        <p className="text-gray-500 text-sm mt-1">
          From peak equity
        </p>
      </div>
    </div>
  );
}
