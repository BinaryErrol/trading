"""Tests for configuration system — YAML loading, env var overrides, validation."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from src.config.settings import (
    CapitalConfig,
    ConnectionConfig,
    DatabaseConfig,
    DashboardConfig,
    RiskConfig,
    Settings,
    StopLossConfig,
    StrategyConfig,
    load_settings,
    validate_config,
)
from src.config.watcher import ConfigWatcher


class TestEnvVarOverrides:
    """Verify environment variables override YAML values using TRADING_BOT_ prefix."""

    def test_connection_port_override(self, tmp_config_file: Path):
        """TRADING_BOT_CONNECTION__PORT=9999 overrides connection.port from YAML."""
        env = {"TRADING_BOT_CONNECTION__PORT": "9999"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(tmp_config_file)
        assert settings.connection.port == 9999

    def test_database_url_override(self, tmp_config_file: Path):
        """TRADING_BOT_DATABASE__URL overrides database.url from YAML."""
        override_url = "postgresql+asyncpg://test:test@db:5432/test"
        env = {"TRADING_BOT_DATABASE__URL": override_url}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(tmp_config_file)
        assert settings.database.url == override_url

    def test_logging_level_override(self, tmp_config_file: Path):
        """TRADING_BOT_LOGGING__LEVEL=DEBUG overrides logging.level from YAML."""
        env = {"TRADING_BOT_LOGGING__LEVEL": "DEBUG"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(tmp_config_file)
        assert settings.logging.level == "DEBUG"

    def test_redis_url_override(self, tmp_config_file: Path):
        """TRADING_BOT_REDIS__URL overrides redis.url from YAML."""
        env = {"TRADING_BOT_REDIS__URL": "redis://custom-host:6380/2"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(tmp_config_file)
        assert settings.redis.url == "redis://custom-host:6380/2"

    def test_dashboard_port_override(self, tmp_config_file: Path):
        """TRADING_BOT_DASHBOARD__PORT overrides dashboard.port from YAML."""
        env = {"TRADING_BOT_DASHBOARD__PORT": "9090"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(tmp_config_file)
        assert settings.dashboard.port == 9090

    def test_deeply_nested_override(self, tmp_config_file: Path):
        """TRADING_BOT_RISK__STOP_LOSS__ATR_MULTIPLIER overrides nested risk.stop_loss.atr_multiplier."""
        env = {"TRADING_BOT_RISK__STOP_LOSS__ATR_MULTIPLIER": "3.5"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(tmp_config_file)
        assert settings.risk.stop_loss.atr_multiplier == 3.5

    def test_env_takes_priority_over_yaml(self, tmp_config_file: Path):
        """Env vars have higher priority than YAML values (configured in settings_customise_sources)."""
        # YAML has port=4002, env overrides to 7777
        env = {"TRADING_BOT_CONNECTION__PORT": "7777"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(tmp_config_file)
        assert settings.connection.port == 7777

    def test_multiple_env_overrides_simultaneously(self, tmp_config_file: Path):
        """Multiple env vars can override different nested values at once."""
        env = {
            "TRADING_BOT_CONNECTION__PORT": "5555",
            "TRADING_BOT_LOGGING__LEVEL": "ERROR",
            "TRADING_BOT_DATABASE__POOL_SIZE": "20",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(tmp_config_file)
        assert settings.connection.port == 5555
        assert settings.logging.level == "ERROR"
        assert settings.database.pool_size == 20

    def test_connection_host_override(self, tmp_config_file: Path):
        """TRADING_BOT_CONNECTION__HOST overrides connection.host from YAML."""
        env = {"TRADING_BOT_CONNECTION__HOST": "192.168.1.100"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(tmp_config_file)
        assert settings.connection.host == "192.168.1.100"


class TestYamlLoading:
    """Verify YAML config loads correctly."""

    def test_loads_from_yaml_file(self, tmp_config_file: Path):
        """Settings loads all values from a YAML file."""
        settings = load_settings(tmp_config_file)
        assert settings.connection.mode == "gateway"
        assert settings.connection.port == 4002
        assert settings.database.pool_size == 5

    def test_missing_required_field_raises(self, tmp_path: Path):
        """Missing required fields (e.g. connection) raise ValidationError."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"logging": {"level": "INFO"}}))
        with pytest.raises(Exception):
            load_settings(config_path)

    def test_invalid_connection_mode_raises(self, sample_config: dict, tmp_path: Path):
        """Invalid enum value for connection.mode raises ValidationError."""
        sample_config["connection"]["mode"] = "invalid_mode"
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception):
            load_settings(config_path)


class TestConnectionPortValidation:
    """Verify connection port must be between 1 and 65535."""

    def test_port_zero_raises(self, sample_config: dict, tmp_path: Path):
        """Port 0 is invalid and raises ValidationError."""
        sample_config["connection"]["port"] = 0
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="port must be between 1 and 65535"):
            load_settings(config_path)

    def test_port_above_65535_raises(self, sample_config: dict, tmp_path: Path):
        """Port 99999 is invalid and raises ValidationError."""
        sample_config["connection"]["port"] = 99999
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="port must be between 1 and 65535"):
            load_settings(config_path)

    def test_valid_port_accepted(self, sample_config: dict, tmp_path: Path):
        """Valid port (4002) loads without error."""
        sample_config["connection"]["port"] = 4002
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        settings = load_settings(config_path)
        assert settings.connection.port == 4002


class TestRiskPercentageValidation:
    """Verify risk percentage fields must be between 0.0 and 1.0."""

    def test_max_position_pct_above_one_raises(self, sample_config: dict, tmp_path: Path):
        """max_position_pct > 1.0 raises ValidationError."""
        sample_config["risk"]["max_position_pct"] = 1.5
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="must be between 0.0 and 1.0"):
            load_settings(config_path)

    def test_max_drawdown_pct_above_one_raises(self, sample_config: dict, tmp_path: Path):
        """max_drawdown_pct > 1.0 raises ValidationError."""
        sample_config["risk"]["max_drawdown_pct"] = 2.0
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="must be between 0.0 and 1.0"):
            load_settings(config_path)

    def test_negative_risk_pct_raises(self, sample_config: dict, tmp_path: Path):
        """Negative max_daily_loss_pct raises ValidationError."""
        sample_config["risk"]["max_daily_loss_pct"] = -0.01
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="must be between 0.0 and 1.0"):
            load_settings(config_path)

    def test_var_confidence_above_one_raises(self, sample_config: dict, tmp_path: Path):
        """var_confidence > 1.0 raises ValidationError."""
        sample_config["risk"]["var_confidence"] = 1.1
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="must be between 0.0 and 1.0"):
            load_settings(config_path)


class TestCapitalValidation:
    """Verify capital config validation rules."""

    def test_negative_total_capital_raises(self, sample_config: dict, tmp_path: Path):
        """Negative total_capital raises ValidationError."""
        sample_config["capital"]["total_capital"] = -1000
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="total_capital must be > 0"):
            load_settings(config_path)

    def test_zero_total_capital_raises(self, sample_config: dict, tmp_path: Path):
        """Zero total_capital raises ValidationError."""
        sample_config["capital"]["total_capital"] = 0
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="total_capital must be > 0"):
            load_settings(config_path)

    def test_percentage_allocations_exceeding_one_raises(
        self, sample_config: dict, tmp_path: Path
    ):
        """Percentage allocations summing > 1.0 raises ValidationError."""
        sample_config["capital"]["allocation_mode"] = "percentage"
        sample_config["capital"]["allocations"] = {
            "momentum": 0.6,
            "mean_reversion": 0.5,
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="must sum to <= 1.0"):
            load_settings(config_path)

    def test_percentage_allocations_at_one_accepted(
        self, sample_config: dict, tmp_path: Path
    ):
        """Percentage allocations summing to exactly 1.0 are valid."""
        sample_config["capital"]["allocation_mode"] = "percentage"
        sample_config["capital"]["allocations"] = {
            "momentum": 0.6,
            "mean_reversion": 0.4,
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        settings = load_settings(config_path)
        assert settings.capital.allocation_mode == "percentage"


class TestStrategyFrequencyValidation:
    """Verify strategy frequency must be a valid value."""

    def test_invalid_frequency_raises(self, sample_config: dict, tmp_path: Path):
        """Invalid frequency '2min' raises ValidationError."""
        sample_config["strategies"]["momentum"]["frequency"] = "2min"
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="frequency must be one of"):
            load_settings(config_path)

    def test_valid_frequencies_accepted(self, sample_config: dict, tmp_path: Path):
        """All valid frequencies load without error."""
        for freq in ("tick", "1min", "5min", "15min", "1hour", "daily", "weekly"):
            sample_config["strategies"]["momentum"]["frequency"] = freq
            config_path = tmp_path / "config.yaml"
            config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
            settings = load_settings(config_path)
            assert settings.strategies["momentum"].frequency == freq


class TestMissingRequiredFields:
    """Verify missing required fields produce descriptive errors."""

    def test_missing_connection_raises_descriptive_error(self, tmp_path: Path):
        """Missing 'connection' field raises with field name in message."""
        config = {
            "strategies": {"m": {"enabled": True, "frequency": "daily", "symbols": ["AAPL"], "asset_classes": ["equity"]}},
            "capital": {"total_capital": 100000, "allocation_mode": "equal_weight"},
            "database": {"url": "postgresql+asyncpg://bot:bot@localhost/db"},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config, default_flow_style=False))
        with pytest.raises(SystemExit, match="connection"):
            validate_config(config_path)

    def test_missing_database_raises_descriptive_error(self, tmp_path: Path):
        """Missing 'database' field raises with field name in message."""
        config = {
            "connection": {"mode": "gateway", "host": "127.0.0.1", "port": 4002, "client_id": 1},
            "strategies": {"m": {"enabled": True, "frequency": "daily", "symbols": ["AAPL"], "asset_classes": ["equity"]}},
            "capital": {"total_capital": 100000, "allocation_mode": "equal_weight"},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config, default_flow_style=False))
        with pytest.raises(SystemExit, match="database"):
            validate_config(config_path)

    def test_validate_config_returns_settings_on_success(self, tmp_config_file: Path):
        """validate_config returns a Settings instance when config is valid."""
        settings = validate_config(tmp_config_file)
        assert settings is not None
        assert settings.connection.port == 4002


class TestDatabaseUrlValidation:
    """Verify database URL must start with 'postgresql'."""

    def test_non_postgresql_url_raises(self, sample_config: dict, tmp_path: Path):
        """Database URL starting with 'mysql' raises ValidationError."""
        sample_config["database"]["url"] = "mysql://user:pass@localhost/db"
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="must start with 'postgresql'"):
            load_settings(config_path)


class TestDashboardPortValidation:
    """Verify dashboard port must be between 1 and 65535."""

    def test_dashboard_port_zero_raises(self, sample_config: dict, tmp_path: Path):
        """Dashboard port 0 raises ValidationError."""
        sample_config["dashboard"]["port"] = 0
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="port must be between 1 and 65535"):
            load_settings(config_path)


class TestStopLossValidation:
    """Verify stop-loss config validation."""

    def test_negative_atr_multiplier_raises(self, sample_config: dict, tmp_path: Path):
        """Negative atr_multiplier raises ValidationError."""
        sample_config["risk"]["stop_loss"]["atr_multiplier"] = -1.0
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="atr_multiplier must be > 0"):
            load_settings(config_path)

    def test_fixed_pct_above_one_raises(self, sample_config: dict, tmp_path: Path):
        """fixed_pct > 1.0 raises ValidationError."""
        sample_config["risk"]["stop_loss"]["fixed_pct"] = 1.5
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(sample_config, default_flow_style=False))
        with pytest.raises(Exception, match="fixed_pct must be between 0 and 1"):
            load_settings(config_path)


class TestConfigWatcher:
    """Verify ConfigWatcher start/stop and callback on change."""

    def test_start_creates_task(self, tmp_config_file: Path):
        """start() returns an asyncio.Task and stores it internally."""

        async def _run():
            callback = AsyncMock()
            watcher = ConfigWatcher(tmp_config_file, on_change=callback)
            task = watcher.start()
            assert isinstance(task, asyncio.Task)
            assert watcher._task is task
            # Clean up
            watcher.stop()
            # Allow cancellation to propagate
            await asyncio.sleep(0.01)

        asyncio.run(_run())

    def test_stop_cancels_task(self, tmp_config_file: Path):
        """stop() cancels the running watch task and sets _task to None."""

        async def _run():
            callback = AsyncMock()
            watcher = ConfigWatcher(tmp_config_file, on_change=callback)
            watcher.start()
            watcher.stop()
            assert watcher._task is None

        asyncio.run(_run())

    def test_stop_when_not_started_is_noop(self, tmp_config_file: Path):
        """stop() does nothing if watcher was never started."""
        callback = AsyncMock()
        watcher = ConfigWatcher(tmp_config_file, on_change=callback)
        # Should not raise
        watcher.stop()
        assert watcher._task is None

    def test_callback_invoked_on_valid_change(self, sample_config: dict, tmp_path: Path):
        """on_change callback is called with new Settings when config file changes."""

        async def _run():
            config_path = tmp_path / "config.yaml"
            config_path.write_text(yaml.dump(sample_config, default_flow_style=False))

            callback = AsyncMock()
            watcher = ConfigWatcher(config_path, on_change=callback)

            # Patch awatch to yield one change event then stop
            async def fake_awatch(*args, **kwargs):
                yield {("modified", str(config_path))}

            with patch("src.config.watcher.awatch", fake_awatch):
                await watcher.watch()

            callback.assert_called_once()
            # The argument should be a Settings instance
            settings_arg = callback.call_args[0][0]
            assert settings_arg.connection.port == 4002

        asyncio.run(_run())

    def test_invalid_config_change_does_not_invoke_callback(
        self, sample_config: dict, tmp_path: Path
    ):
        """Invalid config on reload logs error but does not call on_change."""

        async def _run():
            config_path = tmp_path / "config.yaml"
            # Start with valid config
            config_path.write_text(yaml.dump(sample_config, default_flow_style=False))

            callback = AsyncMock()
            watcher = ConfigWatcher(config_path, on_change=callback)

            # Simulate a change that makes config invalid
            async def fake_awatch(*args, **kwargs):
                # Write invalid config before yielding
                config_path.write_text(yaml.dump({"logging": {"level": "INFO"}}, default_flow_style=False))
                yield {("modified", str(config_path))}

            with patch("src.config.watcher.awatch", fake_awatch):
                await watcher.watch()

            callback.assert_not_called()

        asyncio.run(_run())
