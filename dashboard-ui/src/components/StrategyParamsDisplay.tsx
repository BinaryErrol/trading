interface StrategyParamsDisplayProps {
  strategyName: string;
}

// Mock strategy parameters — will be replaced with API data
const STRATEGY_PARAMS: Record<string, Record<string, string | number>> = {
  momentum: {
    lookback_period: 20,
    entry_threshold: 0.02,
    exit_threshold: -0.01,
    max_positions: 5,
  },
  trend_following: {
    fast_ma: 10,
    slow_ma: 50,
    atr_multiplier: 2.0,
    max_positions: 4,
  },
  mean_reversion: {
    lookback_period: 20,
    z_score_entry: 2.0,
    z_score_exit: 0.5,
    max_positions: 6,
  },
  breakout: {
    channel_period: 20,
    volume_threshold: 1.5,
    atr_stop: 2.0,
    max_positions: 3,
  },
  ma_crossover: {
    fast_period: 9,
    slow_period: 21,
    signal_period: 5,
    max_positions: 5,
  },
  wheel: {
    target_delta: 0.30,
    min_dte: 30,
    max_dte: 45,
    roll_dte_threshold: 7,
    vix_high_threshold: 30.0,
    vix_reentry_threshold: 25.0,
    max_positions_per_symbol: 1,
  },
};

export function StrategyParamsDisplay({ strategyName }: StrategyParamsDisplayProps) {
  const params = STRATEGY_PARAMS[strategyName];

  if (!params) {
    return (
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Configuration</h2>
        <p className="text-gray-400">No configuration parameters available.</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-4">Configuration</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {Object.entries(params).map(([key, value]) => (
          <div key={key} className="flex justify-between items-center bg-gray-700/30 rounded px-3 py-2">
            <span className="text-gray-400 text-sm">{key.replace(/_/g, ' ')}</span>
            <span className="text-white font-mono text-sm">{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
