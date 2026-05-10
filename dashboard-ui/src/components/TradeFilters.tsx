import { mockStrategyComparisons } from '../mockData';

interface TradeFiltersProps {
  strategy: string;
  symbol: string;
  startDate: string;
  endDate: string;
  onStrategyChange: (value: string) => void;
  onSymbolChange: (value: string) => void;
  onStartDateChange: (value: string) => void;
  onEndDateChange: (value: string) => void;
}

export function TradeFilters({
  strategy,
  symbol,
  startDate,
  endDate,
  onStrategyChange,
  onSymbolChange,
  onStartDateChange,
  onEndDateChange,
}: TradeFiltersProps) {
  const strategyNames = mockStrategyComparisons.map((s) => s.name);

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <div className="flex flex-wrap gap-4 items-end">
        <div className="flex flex-col gap-1">
          <label htmlFor="filter-strategy" className="text-gray-400 text-xs uppercase">
            Strategy
          </label>
          <select
            id="filter-strategy"
            value={strategy}
            onChange={(e) => onStrategyChange(e.target.value)}
            className="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
          >
            <option value="">All Strategies</option>
            {strategyNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="filter-symbol" className="text-gray-400 text-xs uppercase">
            Symbol
          </label>
          <input
            id="filter-symbol"
            type="text"
            value={symbol}
            onChange={(e) => onSymbolChange(e.target.value)}
            placeholder="e.g. AAPL"
            className="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 w-28"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="filter-start" className="text-gray-400 text-xs uppercase">
            From
          </label>
          <input
            id="filter-start"
            type="date"
            value={startDate}
            onChange={(e) => onStartDateChange(e.target.value)}
            className="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="filter-end" className="text-gray-400 text-xs uppercase">
            To
          </label>
          <input
            id="filter-end"
            type="date"
            value={endDate}
            onChange={(e) => onEndDateChange(e.target.value)}
            className="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
    </div>
  );
}
