import { useState } from 'react';
import { TradeFilters } from '../components/TradeFilters';
import { TradeTable } from '../components/TradeTable';
import { Pagination } from '../components/Pagination';
import { useTradeHistory } from '../hooks/useApi';

export function TradeHistoryPage() {
  const [strategy, setStrategy] = useState('');
  const [symbol, setSymbol] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [limit, setLimit] = useState(25);
  const [offset, setOffset] = useState(0);

  const { data, isLoading } = useTradeHistory({
    strategy: strategy || undefined,
    symbol: symbol || undefined,
    startDate: startDate || undefined,
    endDate: endDate || undefined,
    limit,
    offset,
  });

  const handleStrategyChange = (value: string) => {
    setStrategy(value);
    setOffset(0);
  };

  const handleSymbolChange = (value: string) => {
    setSymbol(value);
    setOffset(0);
  };

  const handleStartDateChange = (value: string) => {
    setStartDate(value);
    setOffset(0);
  };

  const handleEndDateChange = (value: string) => {
    setEndDate(value);
    setOffset(0);
  };

  const handlePageSizeChange = (newLimit: number) => {
    setLimit(newLimit);
    setOffset(0);
  };

  return (
    <main className="p-6 space-y-6 max-w-screen-2xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-200">Trade History</h1>

      <TradeFilters
        strategy={strategy}
        symbol={symbol}
        startDate={startDate}
        endDate={endDate}
        onStrategyChange={handleStrategyChange}
        onSymbolChange={handleSymbolChange}
        onStartDateChange={handleStartDateChange}
        onEndDateChange={handleEndDateChange}
      />

      {isLoading ? (
        <div className="animate-pulse space-y-3">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-10 bg-gray-800 rounded" />
          ))}
        </div>
      ) : data ? (
        <>
          <TradeTable trades={data.items} />
          <Pagination
            total={data.total}
            limit={data.limit}
            offset={data.offset}
            onPageChange={setOffset}
            onPageSizeChange={handlePageSizeChange}
          />
        </>
      ) : (
        <div className="bg-gray-800 rounded-lg p-6">
          <p className="text-gray-400">No trade data available.</p>
        </div>
      )}
    </main>
  );
}
