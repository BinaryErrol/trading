"""Shared test fixtures for the IBKR Trading Bot test suite."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml


@pytest.fixture
def sample_config() -> dict:
    """Return a minimal valid config dict matching config.example.yaml structure."""
    return {
        "connection": {
            "mode": "gateway",
            "host": "127.0.0.1",
            "port": 4002,
            "client_id": 1,
            "timeout": 30,
            "readonly": False,
        },
        "strategies": {
            "momentum": {
                "enabled": True,
                "frequency": "15min",
                "symbols": ["AAPL", "MSFT"],
                "asset_classes": ["equity"],
                "parameters": {
                    "lookback_period": 20,
                    "momentum_threshold": 0.02,
                },
            }
        },
        "risk": {
            "max_position_pct": 0.05,
            "max_drawdown_pct": 0.10,
            "max_daily_loss_pct": 0.02,
            "max_sector_concentration": 0.25,
            "max_correlation": 0.7,
            "var_confidence": 0.95,
            "var_lookback_days": 252,
            "stop_loss": {
                "type": "atr_trailing",
                "atr_multiplier": 2.0,
                "fixed_pct": 0.03,
            },
        },
        "capital": {
            "total_capital": 100000.00,
            "allocation_mode": "equal_weight",
            "allocations": {"momentum": 1.0},
        },
        "alerts": {
            "channels": {
                "slack": {"enabled": False, "webhook_url": "https://hooks.slack.com/test"},
                "email": {"enabled": False},
                "webhook": {"enabled": False},
            },
            "routing": {
                "trade_executed": ["slack"],
                "risk_breach": ["slack", "email"],
                "connection_lost": ["slack", "email"],
                "error": ["slack", "email"],
                "daily_report": ["email"],
            },
        },
        "backtesting": {
            "slippage_bps": 5,
            "commission_per_share": 0.005,
            "market_impact_bps": 2,
            "data_source": "ibkr",
            "csv_directory": "./data/historical",
            "results_directory": "./data/backtest_results",
        },
        "database": {
            "url": "postgresql+asyncpg://bot:bot@localhost:5432/trading_test",
            "pool_size": 5,
            "max_overflow": 10,
        },
        "dashboard": {
            "port": 8080,
            "auth_token": None,
            "cors_origins": ["http://localhost:3000"],
        },
        "logging": {
            "level": "DEBUG",
            "json_output": False,
            "log_file": None,
        },
        "redis": {
            "url": "redis://localhost:6379/1",
        },
    }


@pytest.fixture
def tmp_config_file(sample_config: dict, tmp_path: Path) -> Path:
    """Write sample_config to a temporary YAML file and return the path."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
    return config_path


@pytest.fixture
def mock_redis() -> MagicMock:
    """Placeholder mock for Redis connection."""
    mock = MagicMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.publish = AsyncMock(return_value=1)
    mock.subscribe = AsyncMock()
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def mock_ib_connection() -> MagicMock:
    """Placeholder mock for IB Gateway/TWS connection."""
    mock = MagicMock()
    mock.connectAsync = AsyncMock(return_value=mock)
    mock.disconnect = MagicMock()
    mock.isConnected = MagicMock(return_value=True)
    mock.reqPositions = AsyncMock(return_value=[])
    mock.reqAccountSummary = AsyncMock(return_value=[])
    mock.placeOrder = AsyncMock()
    mock.cancelOrder = AsyncMock()
    return mock
