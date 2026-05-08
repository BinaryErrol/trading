"""Pydantic settings models for the IBKR Trading Bot.

Loads configuration from YAML file with environment variable overrides.
Environment variables use the TRADING_BOT_ prefix with __ as nested delimiter.
Example: TRADING_BOT_DATABASE__URL overrides database.url
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict, YamlConfigSettingsSource

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


class ConnectionConfig(BaseModel):
    """IBKR TWS or IB Gateway connection settings."""

    mode: Literal["gateway", "tws"] = Field(
        description="Connection mode: 'gateway' (IB Gateway) or 'tws' (Trader Workstation)"
    )
    host: str = Field(description="Host where TWS/Gateway is running")
    port: int = Field(description="Port for TWS/Gateway (4001=live, 4002=paper, 7496/7497 for TWS)")
    client_id: int = Field(description="Unique client ID for this bot instance")
    timeout: int = Field(default=30, description="Connection timeout in seconds")
    readonly: bool = Field(default=False, description="Read-only mode prevents order submission")

    @field_validator("port")
    @classmethod
    def port_must_be_valid(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"port must be between 1 and 65535, got {v}")
        return v


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


class StrategyConfig(BaseModel):
    """Configuration for a single trading strategy."""

    enabled: bool = Field(description="Whether the strategy runs at startup")
    frequency: str = Field(
        description="Evaluation interval (tick, 1min, 5min, 15min, 1hour, daily, weekly)"
    )
    symbols: list[str] = Field(description="List of instruments to trade")
    asset_classes: list[str] = Field(
        description="Allowed asset classes (equity, option, future, forex)"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Strategy-specific tuning parameters"
    )

    VALID_FREQUENCIES: frozenset[str] = frozenset(
        {"tick", "1min", "5min", "15min", "1hour", "daily", "weekly"}
    )

    @field_validator("frequency")
    @classmethod
    def frequency_must_be_valid(cls, v: str) -> str:
        valid = {"tick", "1min", "5min", "15min", "1hour", "daily", "weekly"}
        if v not in valid:
            raise ValueError(
                f"frequency must be one of {sorted(valid)}, got '{v}'"
            )
        return v


# ---------------------------------------------------------------------------
# Risk Management
# ---------------------------------------------------------------------------


class StopLossConfig(BaseModel):
    """Stop-loss configuration for position protection."""

    type: Literal["atr_trailing", "fixed_pct"] = Field(
        description="Stop type: 'atr_trailing' or 'fixed_pct'"
    )
    atr_multiplier: float = Field(
        default=2.0, description="ATR multiplier for trailing stop distance"
    )
    fixed_pct: float = Field(
        default=0.03, description="Fixed stop-loss percentage below entry"
    )

    @field_validator("atr_multiplier")
    @classmethod
    def atr_multiplier_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"atr_multiplier must be > 0, got {v}")
        return v

    @field_validator("fixed_pct")
    @classmethod
    def fixed_pct_must_be_valid(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"fixed_pct must be between 0 and 1, got {v}")
        return v


class RiskConfig(BaseModel):
    """Portfolio-level risk management settings."""

    max_position_pct: float = Field(
        default=0.05, description="Maximum position size per instrument as fraction of portfolio"
    )
    max_drawdown_pct: float = Field(
        default=0.10, description="Maximum portfolio drawdown from peak equity before halting"
    )
    max_daily_loss_pct: float = Field(
        default=0.02, description="Maximum daily loss as fraction of portfolio"
    )
    max_sector_concentration: float = Field(
        default=0.25, description="Maximum fraction of capital in any single sector"
    )
    max_correlation: float = Field(
        default=0.7, description="Correlation threshold for diversification enforcement"
    )
    var_confidence: float = Field(
        default=0.95, description="VaR confidence level (e.g. 0.95 = 95th percentile)"
    )
    var_lookback_days: int = Field(
        default=252, description="Number of historical trading days for VaR calculation"
    )
    stop_loss: StopLossConfig = Field(
        default_factory=lambda: StopLossConfig(type="atr_trailing"),
        description="Stop-loss configuration",
    )

    @field_validator(
        "max_position_pct",
        "max_drawdown_pct",
        "max_daily_loss_pct",
        "max_sector_concentration",
        "max_correlation",
        "var_confidence",
    )
    @classmethod
    def pct_fields_must_be_between_zero_and_one(cls, v: float, info) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(
                f"{info.field_name} must be between 0.0 and 1.0, got {v}"
            )
        return v


# ---------------------------------------------------------------------------
# Capital Allocation
# ---------------------------------------------------------------------------


class CapitalConfig(BaseModel):
    """Capital allocation across strategies."""

    total_capital: Decimal = Field(description="Total capital available for trading (USD)")
    allocation_mode: Literal["equal_weight", "fixed_amount", "percentage"] = Field(
        description="How capital is distributed across strategies"
    )
    allocations: dict[str, float] = Field(
        default_factory=dict, description="Per-strategy allocation values"
    )

    @field_validator("total_capital")
    @classmethod
    def total_capital_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"total_capital must be > 0, got {v}")
        return v

    @model_validator(mode="after")
    def allocations_must_not_exceed_one_in_percentage_mode(self) -> "CapitalConfig":
        if self.allocation_mode == "percentage" and self.allocations:
            total = sum(self.allocations.values())
            if total > 1.0:
                raise ValueError(
                    f"allocation values must sum to <= 1.0 in percentage mode, got {total}"
                )
        return self


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class SlackConfig(BaseModel):
    """Slack notification channel settings."""

    enabled: bool = Field(default=False, description="Enable Slack notifications")
    webhook_url: str = Field(default="", description="Slack incoming webhook URL")


class EmailConfig(BaseModel):
    """Email notification channel settings."""

    enabled: bool = Field(default=False, description="Enable email notifications")
    smtp_host: str = Field(default="", description="SMTP server host")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_user: str = Field(default="", description="SMTP username")
    smtp_password: str = Field(default="", description="SMTP password")
    from_email: str = Field(default="", description="Sender email address")
    to_emails: list[str] = Field(default_factory=list, description="Recipient email addresses")


class WebhookConfig(BaseModel):
    """Generic HTTP webhook notification settings."""

    enabled: bool = Field(default=False, description="Enable webhook notifications")
    url: str = Field(default="", description="HTTP webhook endpoint URL")


class AlertChannelsConfig(BaseModel):
    """All available notification channels."""

    slack: SlackConfig = Field(default_factory=SlackConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)


class AlertConfig(BaseModel):
    """Alert routing and channel configuration."""

    channels: AlertChannelsConfig = Field(default_factory=AlertChannelsConfig)
    routing: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Map event types to notification channel names",
    )


# ---------------------------------------------------------------------------
# Backtesting
# ---------------------------------------------------------------------------


class BacktestConfig(BaseModel):
    """Backtesting simulation parameters."""

    slippage_bps: float = Field(default=5, description="Slippage in basis points")
    commission_per_share: Decimal = Field(
        default=Decimal("0.005"), description="Commission cost per share (USD)"
    )
    market_impact_bps: float = Field(default=2, description="Market impact in basis points")
    data_source: Literal["ibkr", "csv"] = Field(
        default="ibkr", description="Historical data source"
    )
    csv_directory: str = Field(
        default="./data/historical", description="Directory for CSV historical data"
    )
    results_directory: str = Field(
        default="./data/backtest_results", description="Directory for backtest results"
    )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class DatabaseConfig(BaseModel):
    """PostgreSQL database connection settings."""

    url: str = Field(description="Async PostgreSQL connection URL")
    pool_size: int = Field(default=5, description="Connection pool size")
    max_overflow: int = Field(
        default=10, description="Maximum overflow connections beyond pool_size"
    )

    @field_validator("url")
    @classmethod
    def url_must_be_postgresql(cls, v: str) -> str:
        if not v.startswith("postgresql"):
            raise ValueError(
                f"database url must start with 'postgresql', got '{v[:30]}...'"
            )
        return v


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class DashboardConfig(BaseModel):
    """Web monitoring dashboard settings."""

    port: int = Field(default=8080, description="FastAPI dashboard server port")
    auth_token: str | None = Field(
        default=None, description="Authentication token for API access"
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Allowed CORS origins for the frontend",
    )

    @field_validator("port")
    @classmethod
    def port_must_be_valid(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"port must be between 1 and 65535, got {v}")
        return v


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class LoggingConfig(BaseModel):
    """Structured logging configuration."""

    level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")
    json_output: bool = Field(default=True, description="Output logs as JSON")
    log_file: str | None = Field(
        default=None, description="Log file path (None = stdout only)"
    )


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------


class RedisConfig(BaseModel):
    """Redis cache and pub/sub settings."""

    url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")


# ---------------------------------------------------------------------------
# Root Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Root configuration loaded from YAML + environment variables.

    Priority (highest to lowest):
    1. Init kwargs
    2. Environment variables (TRADING_BOT_ prefix, __ nested delimiter)
    3. YAML file (config.yaml)
    4. Field defaults
    """

    connection: ConnectionConfig
    strategies: dict[str, StrategyConfig]
    risk: RiskConfig = Field(default_factory=RiskConfig)
    capital: CapitalConfig
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    backtesting: BacktestConfig = Field(default_factory=BacktestConfig)
    database: DatabaseConfig
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)

    model_config = SettingsConfigDict(
        yaml_file="config.yaml",
        env_prefix="TRADING_BOT_",
        env_nested_delimiter="__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple:
        """Configure settings sources with YAML support.

        Order determines priority (first wins):
        1. init_settings — explicit constructor args
        2. env_settings — environment variables
        3. yaml — config.yaml file
        """
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache the application settings singleton.

    Returns the same Settings instance on repeated calls.
    Clear cache with get_settings.cache_clear() if reload is needed.
    """
    return Settings()


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from a specific YAML file path.

    Useful for tests and CLI tools that need to load config from
    a non-default location. Does not use the lru_cache — each call
    returns a fresh Settings instance.

    Args:
        config_path: Path to a YAML config file. If None, uses the
                     default config.yaml resolution from pydantic-settings.

    Returns:
        A fully validated Settings instance.
    """
    if config_path is None:
        return Settings()

    # Create a subclass that points yaml_file at the given path
    class _Settings(Settings):
        model_config = SettingsConfigDict(
            yaml_file=str(config_path),
            env_prefix="TRADING_BOT_",
            env_nested_delimiter="__",
        )

    return _Settings()


def validate_config(config_path: Path | None = None) -> Settings | None:
    """Load and validate configuration, formatting errors for humans.

    Wraps load_settings with user-friendly error formatting. On validation
    failure, logs each error with its field path and message, then raises
    SystemExit so the bot refuses to start with bad config.

    Args:
        config_path: Path to YAML config file. None uses default resolution.

    Returns:
        A validated Settings instance on success.

    Raises:
        SystemExit: If validation fails, with descriptive error output.
    """
    try:
        return load_settings(config_path)
    except PydanticValidationError as exc:
        errors = exc.errors()
        formatted_lines = []
        for err in errors:
            field_path = " -> ".join(str(loc) for loc in err["loc"])
            msg = err["msg"]
            formatted_lines.append(f"  {field_path}: {msg}")

        error_report = "\n".join(formatted_lines)
        logger.error(
            "configuration_validation_failed",
            error_count=len(errors),
            errors=formatted_lines,
        )
        raise SystemExit(
            f"Invalid configuration ({len(errors)} error(s)):\n{error_report}"
        ) from exc
