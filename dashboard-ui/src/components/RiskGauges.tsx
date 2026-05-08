import { useRiskMetrics } from '../hooks/useApi';

interface GaugeProps {
  label: string;
  value: number;
  max: number;
  unit?: string;
  warningThreshold?: number;
  dangerThreshold?: number;
}

function Gauge({ label, value, max, unit = '%', warningThreshold = 60, dangerThreshold = 80 }: GaugeProps) {
  const percentage = Math.min((value / max) * 100, 100);
  let barColor = 'bg-green-500';
  if (percentage >= dangerThreshold) {
    barColor = 'bg-red-500';
  } else if (percentage >= warningThreshold) {
    barColor = 'bg-yellow-500';
  }

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="text-gray-400">{label}</span>
        <span className="font-medium">
          {value.toFixed(1)}{unit} / {max}{unit}
        </span>
      </div>
      <div className="w-full bg-gray-700 rounded-full h-3">
        <div
          className={`h-3 rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  );
}

export function RiskGauges() {
  const { data: risk, isLoading } = useRiskMetrics();

  if (isLoading || !risk) {
    return (
      <div className="bg-gray-800 rounded-lg p-6 animate-pulse">
        <div className="h-6 bg-gray-700 rounded w-1/4 mb-4" />
        <div className="space-y-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-8 bg-gray-700 rounded" />
          ))}
        </div>
      </div>
    );
  }

  // Risk limits from design doc defaults
  const maxPositions = 20;
  const maxDrawdownPct = 10;
  const maxDailyLossPct = 2;
  const maxSectorConcentration = 25;

  // Simulated daily loss (mock)
  const dailyLossPct = 0.35;
  // Simulated sector concentration (mock)
  const sectorConcentration = 18.5;

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-4">Risk Utilization</h2>
      <div className="space-y-4">
        <Gauge
          label="Position Count"
          value={risk.position_count}
          max={maxPositions}
          unit=""
          warningThreshold={70}
          dangerThreshold={90}
        />
        <Gauge
          label="Drawdown"
          value={risk.drawdown_pct}
          max={maxDrawdownPct}
          warningThreshold={50}
          dangerThreshold={80}
        />
        <Gauge
          label="Daily Loss"
          value={dailyLossPct}
          max={maxDailyLossPct}
          warningThreshold={50}
          dangerThreshold={75}
        />
        <Gauge
          label="Sector Concentration"
          value={sectorConcentration}
          max={maxSectorConcentration}
          warningThreshold={60}
          dangerThreshold={80}
        />
      </div>
    </div>
  );
}
