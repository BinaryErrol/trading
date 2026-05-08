"""Tests for strategy configurable parameters via StrategyConfig.

Verifies that:
1. Each strategy reads parameters from StrategyConfig.parameters
2. Sensible defaults are used when config values are not provided
3. Parameters can be changed at runtime via update_parameters (hot-reload)
"""

from __future__ import annotations

from src.strategies.implementations.bollinger import BollingerStrategy
from src.strategies.implementations.breakout import BreakoutStrategy
from src.strategies.implementations.ma_crossover import MACrossoverStrategy
from src.strategies.implementations.market_making import MarketMakingStrategy
from src.strategies.implementations.mean_reversion import MeanReversionStrategy
from src.strategies.implementations.momentum import MomentumStrategy
from src.strategies.implementations.pairs_trading import PairsTradingStrategy
from src.strategies.implementations.rsi_divergence import RSIDivergenceStrategy
from src.strategies.implementations.trend_following import TrendFollowingStrategy
from src.strategies.implementations.vwap import VWAPStrategy

from .conftest import FakeDataHub, make_strategy_config


class TestMomentumConfig:
    """Test Momentum strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(
            parameters={"lookback_period": 20, "momentum_threshold": 0.05}
        )
        strategy = MomentumStrategy(config, FakeDataHub())
        assert strategy.lookback_period == 20
        assert strategy.momentum_threshold == 0.05

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = MomentumStrategy(config, FakeDataHub())
        assert strategy.lookback_period == 14
        assert strategy.momentum_threshold == 0.02

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(
            parameters={"lookback_period": 14, "momentum_threshold": 0.02}
        )
        strategy = MomentumStrategy(config, FakeDataHub())
        strategy.update_parameters(
            {"lookback_period": 30, "momentum_threshold": 0.05}
        )
        assert strategy.lookback_period == 30
        assert strategy.momentum_threshold == 0.05


class TestMACrossoverConfig:
    """Test MA Crossover strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(
            parameters={"fast_period": 5, "slow_period": 20, "ma_type": "ema"}
        )
        strategy = MACrossoverStrategy(config, FakeDataHub())
        assert strategy.fast_period == 5
        assert strategy.slow_period == 20
        assert strategy.ma_type == "ema"

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = MACrossoverStrategy(config, FakeDataHub())
        assert strategy.fast_period == 10
        assert strategy.slow_period == 30
        assert strategy.ma_type == "sma"

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(parameters={})
        strategy = MACrossoverStrategy(config, FakeDataHub())
        strategy.update_parameters({"fast_period": 8, "slow_period": 50, "ma_type": "ema"})
        assert strategy.fast_period == 8
        assert strategy.slow_period == 50
        assert strategy.ma_type == "ema"


class TestTrendFollowingConfig:
    """Test Trend Following strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(parameters={"fast_ma": 5, "slow_ma": 20, "atr_filter": 0.02})
        strategy = TrendFollowingStrategy(config, FakeDataHub())
        assert strategy.fast_ma == 5
        assert strategy.slow_ma == 20
        assert strategy.atr_filter == 0.02

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = TrendFollowingStrategy(config, FakeDataHub())
        assert strategy.fast_ma == 10
        assert strategy.slow_ma == 30
        assert strategy.atr_filter == 0.01

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(parameters={})
        strategy = TrendFollowingStrategy(config, FakeDataHub())
        strategy.update_parameters({"fast_ma": 15, "slow_ma": 50, "atr_filter": 0.03})
        assert strategy.fast_ma == 15
        assert strategy.slow_ma == 50
        assert strategy.atr_filter == 0.03


class TestBreakoutConfig:
    """Test Breakout strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(
            parameters={
                "consolidation_period": 30,
                "breakout_atr_multiple": 2.0,
            }
        )
        strategy = BreakoutStrategy(config, FakeDataHub())
        assert strategy.consolidation_period == 30
        assert strategy.breakout_atr_multiple == 2.0

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = BreakoutStrategy(config, FakeDataHub())
        assert strategy.consolidation_period == 20
        assert strategy.breakout_atr_multiple == 1.5

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(parameters={})
        strategy = BreakoutStrategy(config, FakeDataHub())
        strategy.update_parameters({"consolidation_period": 40, "breakout_atr_multiple": 3.0})
        assert strategy.consolidation_period == 40
        assert strategy.breakout_atr_multiple == 3.0


class TestMeanReversionConfig:
    """Test Mean Reversion strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(parameters={"lookback_period": 30, "z_score_threshold": 2.5})
        strategy = MeanReversionStrategy(config, FakeDataHub())
        assert strategy.lookback_period == 30
        assert strategy.z_score_threshold == 2.5

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = MeanReversionStrategy(config, FakeDataHub())
        assert strategy.lookback_period == 20
        assert strategy.z_score_threshold == 2.0

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(parameters={})
        strategy = MeanReversionStrategy(config, FakeDataHub())
        strategy.update_parameters({"lookback_period": 50, "z_score_threshold": 3.0})
        assert strategy.lookback_period == 50
        assert strategy.z_score_threshold == 3.0


class TestBollingerConfig:
    """Test Bollinger Band strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(
            parameters={"bb_period": 30, "bb_std": 2.5, "entry_band": "lower"}
        )
        strategy = BollingerStrategy(config, FakeDataHub())
        assert strategy.bb_period == 30
        assert strategy.bb_std == 2.5
        assert strategy.entry_band == "lower"

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = BollingerStrategy(config, FakeDataHub())
        assert strategy.bb_period == 20
        assert strategy.bb_std == 2.0
        assert strategy.entry_band == "both"

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(parameters={})
        strategy = BollingerStrategy(config, FakeDataHub())
        strategy.update_parameters({"bb_period": 40, "bb_std": 3.0, "entry_band": "upper"})
        assert strategy.bb_period == 40
        assert strategy.bb_std == 3.0
        assert strategy.entry_band == "upper"


class TestRSIDivergenceConfig:
    """Test RSI Divergence strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(
            parameters={"rsi_period": 21, "overbought": 80, "oversold": 20}
        )
        strategy = RSIDivergenceStrategy(config, FakeDataHub())
        assert strategy.rsi_period == 21
        assert strategy.overbought == 80
        assert strategy.oversold == 20

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = RSIDivergenceStrategy(config, FakeDataHub())
        assert strategy.rsi_period == 14
        assert strategy.overbought == 70
        assert strategy.oversold == 30

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(parameters={})
        strategy = RSIDivergenceStrategy(config, FakeDataHub())
        strategy.update_parameters({"rsi_period": 21, "overbought": 80, "oversold": 20})
        assert strategy.rsi_period == 21
        assert strategy.overbought == 80
        assert strategy.oversold == 20


class TestVWAPConfig:
    """Test VWAP strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(
            parameters={"deviation_threshold": 0.03, "session_type": "full"}
        )
        strategy = VWAPStrategy(config, FakeDataHub())
        assert strategy.deviation_threshold == 0.03
        assert strategy.session_type == "full"

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = VWAPStrategy(config, FakeDataHub())
        assert strategy.deviation_threshold == 0.02
        assert strategy.session_type == "regular"

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(parameters={})
        strategy = VWAPStrategy(config, FakeDataHub())
        strategy.update_parameters({"deviation_threshold": 0.05, "session_type": "full"})
        assert strategy.deviation_threshold == 0.05
        assert strategy.session_type == "full"


class TestPairsTradingConfig:
    """Test Pairs Trading strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(
            parameters={
                "pair_symbols": ["AAPL", "MSFT"],
                "cointegration_window": 50,
                "entry_z": 2.5,
                "exit_z": 0.3,
            }
        )
        strategy = PairsTradingStrategy(config, FakeDataHub())
        assert strategy.pair_symbols == ["AAPL", "MSFT"]
        assert strategy.cointegration_window == 50
        assert strategy.entry_z == 2.5
        assert strategy.exit_z == 0.3

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = PairsTradingStrategy(config, FakeDataHub())
        assert strategy.pair_symbols == []
        assert strategy.cointegration_window == 30
        assert strategy.entry_z == 2.0
        assert strategy.exit_z == 0.5

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(parameters={})
        strategy = PairsTradingStrategy(config, FakeDataHub())
        strategy.update_parameters({
            "pair_symbols": ["GOOG", "META"],
            "cointegration_window": 60,
            "entry_z": 3.0,
            "exit_z": 0.2,
        })
        assert strategy.pair_symbols == ["GOOG", "META"]
        assert strategy.cointegration_window == 60
        assert strategy.entry_z == 3.0
        assert strategy.exit_z == 0.2


class TestMarketMakingConfig:
    """Test Market Making strategy configurable parameters."""

    def test_reads_parameters_from_config(self):
        config = make_strategy_config(
            parameters={
                "spread_bps": 15.0,
                "inventory_limit": 200,
                "skew_factor": 0.7,
                "atr_period": 20,
            }
        )
        strategy = MarketMakingStrategy(config, FakeDataHub())
        assert strategy.spread_bps == 15.0
        assert strategy.inventory_limit == 200
        assert strategy.skew_factor == 0.7
        assert strategy.atr_period == 20

    def test_uses_defaults_when_no_parameters(self):
        config = make_strategy_config(parameters={})
        strategy = MarketMakingStrategy(config, FakeDataHub())
        assert strategy.spread_bps == 10.0
        assert strategy.inventory_limit == 100
        assert strategy.skew_factor == 0.5
        assert strategy.atr_period == 14

    def test_update_parameters_at_runtime(self):
        config = make_strategy_config(parameters={})
        strategy = MarketMakingStrategy(config, FakeDataHub())
        strategy.update_parameters({
            "spread_bps": 20.0,
            "inventory_limit": 300,
            "skew_factor": 0.8,
            "atr_period": 21,
        })
        assert strategy.spread_bps == 20.0
        assert strategy.inventory_limit == 300
        assert strategy.skew_factor == 0.8
        assert strategy.atr_period == 21


class TestUpdateParametersPreservesUnchanged:
    """Test that update_parameters preserves values not in the new dict."""

    def test_partial_update_preserves_existing(self):
        """Only the parameters present in the update dict should change."""
        config = make_strategy_config(
            parameters={"lookback_period": 14, "momentum_threshold": 0.02}
        )
        strategy = MomentumStrategy(config, FakeDataHub())
        # Only update lookback_period, momentum_threshold should stay
        strategy.update_parameters({"lookback_period": 30})
        assert strategy.lookback_period == 30
        assert strategy.momentum_threshold == 0.02

    def test_update_parameters_updates_config_dict(self):
        """The underlying config.parameters dict should be updated."""
        config = make_strategy_config(parameters={"fast_period": 10})
        strategy = MACrossoverStrategy(config, FakeDataHub())
        new_params = {"fast_period": 20, "slow_period": 60, "ma_type": "ema"}
        strategy.update_parameters(new_params)
        assert strategy.config.parameters == new_params
