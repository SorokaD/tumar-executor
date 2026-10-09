"""
Типизированные настройки из окружения (pydantic-settings).

Имена переменных согласованы с `.env.example`; секреты не логируются.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class RuntimeMode(str, Enum):
    """Режим работы процесса (влияет на выбор адаптеров в bootstrap)."""

    LIVE = "live"
    PAPER = "paper"
    REPLAY = "replay"


class StrategyMode(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class StrategyRuntimeConfig(BaseModel):
    """Runtime-конфиг одной стратегии для strategy manager."""

    strategy_name: str
    inst_id: str
    mode: StrategyMode = StrategyMode.ENABLED


class Settings(BaseSettings):
    """Глобальные настройки исполнителя."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: str = Field(default="development", validation_alias="OKX_HFT_ENV")
    runtime_mode: RuntimeMode = Field(
        default=RuntimeMode.PAPER,
        validation_alias="OKX_HFT_RUNTIME_MODE",
    )
    log_level: str = Field(
        default="INFO",
        validation_alias="OKX_HFT_LOG_LEVEL",
    )

    okx_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OKX_API_KEY",
    )
    okx_api_secret: SecretStr | None = Field(
        default=None,
        validation_alias="OKX_API_SECRET",
    )
    okx_api_passphrase: SecretStr | None = Field(
        default=None,
        validation_alias="OKX_API_PASSPHRASE",
    )
    okx_flag_demo: bool = Field(default=True, validation_alias="OKX_FLAG_DEMO")
    okx_account_label: str | None = Field(
        default=None,
        validation_alias="OKX_ACCOUNT_LABEL",
        description="Метка субсчёта OKX (например sub-random-baseline); пишется в executor_runs.",
    )
    okx_base_url: str = Field(
        default="https://www.okx.com",
        validation_alias="OKX_BASE_URL",
    )
    okx_http_timeout_sec: float = Field(
        default=25.0,
        validation_alias="OKX_HTTP_TIMEOUT_SEC",
    )

    strategies_config_path: str = Field(
        default="config/strategies.yaml",
        validation_alias="OKX_HFT_STRATEGIES_CONFIG",
    )

    control_host: str = Field(
        default="127.0.0.1",
        validation_alias="OKX_HFT_CONTROL_HOST",
    )
    control_port: int = Field(default=8080, validation_alias="OKX_HFT_CONTROL_PORT")
    control_api_token: SecretStr | None = Field(
        default=None,
        validation_alias="OKX_HFT_CONTROL_API_TOKEN",
    )

    metrics_enabled: bool = Field(
        default=False,
        validation_alias="OKX_HFT_METRICS_ENABLED",
    )
    sqlite_path: str = Field(
        default="data/baseline_mvp.sqlite3",
        validation_alias="OKX_SQLITE_PATH",
    )
    loop_sleep_sec: float = Field(default=1.0, validation_alias="OKX_LOOP_SLEEP_SEC")
    safe_mode: bool = Field(default=False, validation_alias="OKX_HFT_SAFE_MODE")
    enable_real_okx_in_paper: bool = Field(
        default=False,
        validation_alias="OKX_ENABLE_REAL_OKX_IN_PAPER",
    )

    postgres_enabled: bool = Field(
        default=True,
        validation_alias="OKX_HFT_POSTGRES_ENABLED",
    )
    database_url: SecretStr | None = Field(
        default=None,
        validation_alias="DATABASE_URL",
    )
    postgres_user: str | None = Field(default=None, validation_alias="POSTGRES_USER")
    postgres_password: SecretStr | None = Field(
        default=None,
        validation_alias="POSTGRES_PASSWORD",
    )
    postgres_db: str | None = Field(default=None, validation_alias="POSTGRES_DB")
    postgres_host: str | None = Field(default=None, validation_alias="POSTGRES_LINK")
    postgres_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    postgres_schema: str = Field(default="okx_exec", validation_alias="OKX_HFT_POSTGRES_SCHEMA")
    postgres_queue_size: int = Field(
        default=10_000,
        validation_alias="OKX_HFT_POSTGRES_QUEUE_SIZE",
    )

    def get_database_url(self) -> str | None:
        """Строка подключения к PostgreSQL или None, если PG не настроен."""
        if self.database_url is not None:
            return self.database_url.get_secret_value()
        if (
            self.postgres_user
            and self.postgres_password is not None
            and self.postgres_host
            and self.postgres_db
        ):
            password = self.postgres_password.get_secret_value()
            return (
                f"postgresql://{self.postgres_user}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return None

    def postgres_is_configured(self) -> bool:
        return self.postgres_enabled and self.get_database_url() is not None

    def get_strategy_runtime_configs(self) -> list[StrategyRuntimeConfig]:
        """Список стратегий из config/strategies.yaml."""
        from config.strategy_config import get_strategies_config

        return get_strategies_config(self).runtime_configs()


@lru_cache
def get_settings() -> Settings:
    """Кэшированный singleton настроек процесса."""
    return Settings()
