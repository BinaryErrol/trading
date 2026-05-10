import { useCallback, useState } from 'react';
import { PortfolioSummaryPanel } from '../components/PortfolioSummary';
import { PositionsTable } from '../components/PositionsTable';
import { StrategyStatusPanel } from '../components/StrategyStatusPanel';
import { PerformanceCharts } from '../components/PerformanceCharts';
import { RiskGauges } from '../components/RiskGauges';
import { OrderHistory } from '../components/OrderHistory';
import { useWebSocket } from '../hooks/useWebSocket';
import type { WebSocketMessage } from '../types';

export function OverviewPage() {
  const [_lastUpdate, setLastUpdate] = useState<WebSocketMessage | null>(null);

  const handleMessage = useCallback((message: WebSocketMessage) => {
    setLastUpdate(message);
  }, []);

  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${wsProtocol}//${window.location.host}/ws/live`;

  useWebSocket({
    url: wsUrl,
    onMessage: handleMessage,
  });

  return (
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
  );
}
