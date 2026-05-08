import { useCallback, useState } from 'react';
import { PortfolioSummaryPanel } from './components/PortfolioSummary';
import { PositionsTable } from './components/PositionsTable';
import { StrategyStatusPanel } from './components/StrategyStatusPanel';
import { PerformanceCharts } from './components/PerformanceCharts';
import { RiskGauges } from './components/RiskGauges';
import { OrderHistory } from './components/OrderHistory';
import { ConnectionStatus } from './components/ConnectionStatus';
import { useWebSocket } from './hooks/useWebSocket';
import type { WebSocketMessage } from './types';

function App() {
  const [_lastUpdate, setLastUpdate] = useState<WebSocketMessage | null>(null);

  const handleMessage = useCallback((message: WebSocketMessage) => {
    setLastUpdate(message);
  }, []);

  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${wsProtocol}//${window.location.host}/ws/live`;

  const { isConnected } = useWebSocket({
    url: wsUrl,
    onMessage: handleMessage,
  });

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold">IBKR Trading Bot</h1>
            <span className="text-gray-500 text-sm">Dashboard</span>
          </div>
          <ConnectionStatus isConnected={isConnected} />
        </div>
      </header>

      {/* Main Content */}
      <main className="p-6 space-y-6 max-w-screen-2xl mx-auto">
        {/* Portfolio Summary */}
        <PortfolioSummaryPanel />

        {/* Two-column layout: Positions + Risk */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <PositionsTable />
          </div>
          <div>
            <RiskGauges />
          </div>
        </div>

        {/* Strategy Status */}
        <StrategyStatusPanel />

        {/* Performance Charts */}
        <PerformanceCharts />

        {/* Order History */}
        <OrderHistory />
      </main>
    </div>
  );
}

export default App;
