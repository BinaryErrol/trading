"""Tests for Value at Risk (VaR) calculation using historical simulation."""

import numpy as np
import pytest

from src.risk.var import calculate_var, check_var_limit


class TestCalculateVar:
    """Tests for the calculate_var function."""

    def test_var_with_known_uniform_data(self) -> None:
        """VaR at 95% confidence on uniform [-0.10, 0.10] returns ~0.09."""
        # 100 evenly spaced returns from -0.10 to 0.10
        returns = np.linspace(-0.10, 0.10, 100).tolist()

        var = calculate_var(returns, confidence=0.95)

        # 5th percentile of [-0.10, 0.10] uniform ≈ -0.09 → VaR ≈ 0.09
        assert var == pytest.approx(0.09, abs=0.01)

    def test_var_with_known_sorted_data(self) -> None:
        """VaR with simple sorted data where 5th percentile is known."""
        # 20 returns: -0.10, -0.09, ..., 0.09
        returns = [i / 100 for i in range(-10, 10)]

        var = calculate_var(returns, confidence=0.95)

        # 5th percentile of 20 values from -0.10 to 0.09
        # numpy percentile interpolates: at 5th percentile ≈ -0.0905
        assert var == pytest.approx(0.0905, abs=0.005)

    def test_var_all_negative_returns(self) -> None:
        """VaR when all returns are negative (high risk)."""
        returns = [-0.05, -0.04, -0.03, -0.02, -0.01]

        var = calculate_var(returns, confidence=0.95)

        # 5th percentile is close to -0.05, so VaR ≈ 0.049
        assert var > 0.04

    def test_var_all_positive_returns(self) -> None:
        """VaR when all returns are positive (low risk)."""
        returns = [0.01, 0.02, 0.03, 0.04, 0.05]

        var = calculate_var(returns, confidence=0.95)

        # 5th percentile is close to 0.01, so VaR is negative loss → ~-0.01
        # Since VaR = -percentile_return, and percentile is positive, VaR < 0
        assert var < 0  # No loss expected

    def test_var_with_portfolio_value(self) -> None:
        """VaR scaled by portfolio value returns dollar amount."""
        returns = np.linspace(-0.10, 0.10, 100).tolist()

        var = calculate_var(returns, confidence=0.95, portfolio_value=1_000_000)

        # ~0.09 * 1,000,000 = ~90,000
        assert var == pytest.approx(90_000, rel=0.1)

    def test_var_90_confidence(self) -> None:
        """VaR at 90% confidence uses 10th percentile."""
        returns = np.linspace(-0.10, 0.10, 100).tolist()

        var = calculate_var(returns, confidence=0.90)

        # 10th percentile of [-0.10, 0.10] ≈ -0.08 → VaR ≈ 0.08
        assert var == pytest.approx(0.08, abs=0.01)

    def test_var_empty_returns_raises(self) -> None:
        """Empty returns array raises ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            calculate_var([], confidence=0.95)

    def test_var_invalid_confidence_raises(self) -> None:
        """Confidence outside (0, 1) raises ValueError."""
        with pytest.raises(ValueError, match="confidence must be between"):
            calculate_var([0.01, -0.01], confidence=1.0)

        with pytest.raises(ValueError, match="confidence must be between"):
            calculate_var([0.01, -0.01], confidence=0.0)


class TestCheckVarLimit:
    """Tests for the check_var_limit function."""

    def test_within_limit(self) -> None:
        """Returns True when VaR is within the limit."""
        returns = np.linspace(-0.05, 0.05, 100).tolist()

        within, var_value = check_var_limit(
            returns=returns,
            confidence=0.95,
            portfolio_value=100_000,
            var_limit=10_000,  # Generous limit
        )

        assert within is True
        assert var_value < 10_000

    def test_exceeds_limit(self) -> None:
        """Returns False when VaR exceeds the limit."""
        returns = np.linspace(-0.10, 0.10, 100).tolist()

        within, var_value = check_var_limit(
            returns=returns,
            confidence=0.95,
            portfolio_value=1_000_000,
            var_limit=50_000,  # Tight limit, VaR ≈ 90k
        )

        assert within is False
        assert var_value > 50_000

    def test_returns_var_value(self) -> None:
        """check_var_limit returns the computed VaR dollar amount."""
        returns = [-0.02, -0.01, 0.0, 0.01, 0.02] * 50

        _, var_value = check_var_limit(
            returns=returns,
            confidence=0.95,
            portfolio_value=500_000,
            var_limit=100_000,
        )

        assert var_value > 0
