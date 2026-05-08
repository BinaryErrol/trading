"""Tests for stop-loss monitoring — fixed percentage and ATR trailing stops."""

from decimal import Decimal

import pytest

from src.config.settings import StopLossConfig
from src.risk.stops import StopMonitor
from src.strategies.signals import SignalDirection


@pytest.fixture
def fixed_config() -> StopLossConfig:
    """StopLossConfig for fixed percentage stops (3%)."""
    return StopLossConfig(type="fixed_pct", fixed_pct=0.03, atr_multiplier=2.0)


@pytest.fixture
def trailing_config() -> StopLossConfig:
    """StopLossConfig for ATR trailing stops."""
    return StopLossConfig(type="atr_trailing", atr_multiplier=2.0, fixed_pct=0.03)


class TestFixedStop:
    """Tests for fixed percentage stop-loss."""

    def test_fixed_stop_price_calculation(self, fixed_config: StopLossConfig) -> None:
        """Fixed stop: stop_price = entry * (1 - pct)."""
        monitor = StopMonitor(fixed_config)
        stop = monitor.add_position(
            symbol="AAPL",
            entry_price=Decimal("100.00"),
            strategy_name="momentum",
        )

        # 100 * (1 - 0.03) = 97.00
        assert stop.stop_price == Decimal("97.00")
        assert stop.stop_type == "fixed_pct"

    def test_fixed_stop_triggers_close_signal(self, fixed_config: StopLossConfig) -> None:
        """When price <= stop_price, a CLOSE signal is generated."""
        monitor = StopMonitor(fixed_config)
        monitor.add_position(
            symbol="AAPL",
            entry_price=Decimal("100.00"),
            strategy_name="momentum",
        )

        # Price drops to exactly the stop level
        signals = monitor.monitor_stops({"AAPL": Decimal("97.00")})
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.CLOSE
        assert signals[0].symbol == "AAPL"
        assert signals[0].strategy_name == "momentum"
        assert signals[0].metadata["reason"] == "stop_loss_triggered"

    def test_fixed_stop_triggers_below_stop(self, fixed_config: StopLossConfig) -> None:
        """When price drops below stop_price, CLOSE signal is generated."""
        monitor = StopMonitor(fixed_config)
        monitor.add_position(
            symbol="AAPL",
            entry_price=Decimal("100.00"),
            strategy_name="momentum",
        )

        signals = monitor.monitor_stops({"AAPL": Decimal("95.00")})
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.CLOSE

    def test_fixed_stop_no_trigger_above_stop(self, fixed_config: StopLossConfig) -> None:
        """No signal when price is above stop_price."""
        monitor = StopMonitor(fixed_config)
        monitor.add_position(
            symbol="AAPL",
            entry_price=Decimal("100.00"),
            strategy_name="momentum",
        )

        signals = monitor.monitor_stops({"AAPL": Decimal("99.00")})
        assert len(signals) == 0

    def test_fixed_stop_does_not_trail(self, fixed_config: StopLossConfig) -> None:
        """Fixed stop does not move even when price increases."""
        monitor = StopMonitor(fixed_config)
        monitor.add_position(
            symbol="AAPL",
            entry_price=Decimal("100.00"),
            strategy_name="momentum",
        )

        # Price goes up
        monitor.update_price("AAPL", Decimal("110.00"))

        # Stop should still be at 97.00 (not trailing)
        stop = monitor.stops["AAPL"]
        assert stop.stop_price == Decimal("97.00")
        assert stop.highest_price == Decimal("110.00")


class TestTrailingStop:
    """Tests for ATR-based trailing stop."""

    def test_trailing_stop_initial_calculation(self, trailing_config: StopLossConfig) -> None:
        """Trailing stop: initial stop = entry - N * ATR."""
        monitor = StopMonitor(trailing_config)
        stop = monitor.add_position(
            symbol="AAPL",
            entry_price=Decimal("100.00"),
            strategy_name="trend",
            atr=Decimal("2.50"),
        )

        # 100 - 2.0 * 2.50 = 95.00
        assert stop.stop_price == Decimal("95.00")
        assert stop.stop_type == "atr_trailing"

    def test_trailing_stop_moves_up(self, trailing_config: StopLossConfig) -> None:
        """Trailing stop moves up when price makes new high."""
        monitor = StopMonitor(trailing_config)
        monitor.add_position(
            symbol="AAPL",
            entry_price=Decimal("100.00"),
            strategy_name="trend",
            atr=Decimal("2.50"),
        )

        # Price moves up to 105, new stop = 105 - 2*2.5 = 100
        monitor.update_price("AAPL", Decimal("105.00"), atr=Decimal("2.50"))
        stop = monitor.stops["AAPL"]
        assert stop.stop_price == Decimal("100.00")
        assert stop.highest_price == Decimal("105.00")

    def test_trailing_stop_never_moves_down(self, trailing_config: StopLossConfig) -> None:
        """Trailing stop never decreases even if price drops."""
        monitor = StopMonitor(trailing_config)
        monitor.add_position(
            symbol="AAPL",
            entry_price=Decimal("100.00"),
            strategy_name="trend",
            atr=Decimal("2.50"),
        )

        # Move up first
        monitor.update_price("AAPL", Decimal("105.00"), atr=Decimal("2.50"))
        # Stop is now 100.00

        # Price drops — stop should NOT move down
        monitor.update_price("AAPL", Decimal("101.00"), atr=Decimal("2.50"))
        stop = monitor.stops["AAPL"]
        assert stop.stop_price == Decimal("100.00")  # Unchanged
        assert stop.highest_price == Decimal("105.00")  # Unchanged

    def test_trailing_stop_triggers_close(self, trailing_config: StopLossConfig) -> None:
        """Trailing stop triggers CLOSE when price hits stop level."""
        monitor = StopMonitor(trailing_config)
        monitor.add_position(
            symbol="AAPL",
            entry_price=Decimal("100.00"),
            strategy_name="trend",
            atr=Decimal("2.50"),
        )

        # Initial stop is 95.00
        signals = monitor.monitor_stops({"AAPL": Decimal("94.50")})
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.CLOSE
        assert signals[0].metadata["stop_type"] == "atr_trailing"

    def test_trailing_stop_requires_atr(self, trailing_config: StopLossConfig) -> None:
        """ATR trailing stop raises ValueError if ATR not provided."""
        monitor = StopMonitor(trailing_config)
        with pytest.raises(ValueError, match="ATR value required"):
            monitor.add_position(
                symbol="AAPL",
                entry_price=Decimal("100.00"),
                strategy_name="trend",
            )


class TestStopMonitorMultiplePositions:
    """Tests for monitoring multiple positions simultaneously."""

    def test_multiple_positions_independent(self, fixed_config: StopLossConfig) -> None:
        """Each position has its own independent stop level."""
        monitor = StopMonitor(fixed_config)
        monitor.add_position("AAPL", Decimal("100.00"), "strat_a")
        monitor.add_position("MSFT", Decimal("200.00"), "strat_b")

        # Only AAPL hits stop (97.00), MSFT stop is 194.00
        signals = monitor.monitor_stops({
            "AAPL": Decimal("96.00"),
            "MSFT": Decimal("195.00"),
        })
        assert len(signals) == 1
        assert signals[0].symbol == "AAPL"

    def test_remove_position(self, fixed_config: StopLossConfig) -> None:
        """Removed positions are no longer monitored."""
        monitor = StopMonitor(fixed_config)
        monitor.add_position("AAPL", Decimal("100.00"), "strat_a")
        monitor.remove_position("AAPL")

        signals = monitor.monitor_stops({"AAPL": Decimal("90.00")})
        assert len(signals) == 0

    def test_missing_price_skipped(self, fixed_config: StopLossConfig) -> None:
        """Positions without a current price in the dict are skipped."""
        monitor = StopMonitor(fixed_config)
        monitor.add_position("AAPL", Decimal("100.00"), "strat_a")

        # No price for AAPL in the dict
        signals = monitor.monitor_stops({"MSFT": Decimal("200.00")})
        assert len(signals) == 0
