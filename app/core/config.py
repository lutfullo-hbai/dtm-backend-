"""Application settings, read from the environment and validated at startup."""

import ipaddress
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio.connection import parse_url as parse_redis_url
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

AppEnv = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "console"]
# Durations must be finite: `inf` would silently disable a timeout.
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]

_DATABASE_DRIVER = "postgresql+asyncpg"
_REDIS_SCHEMES = ("redis", "rediss")
_HOSTNAME = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,251}[A-Za-z0-9_])?$")
_REDIS_DB_PATH = re.compile(r"^/?$|^/[0-9]{1,5}$")
_MAX_PORT = 65535
_UNPARSEABLE = "is not a valid URL (percent-encode reserved characters in credentials)"
# Query parameters reach the driver as connection options and could silently override
# what Settings configures (timeouts, pool size), so only listed ones are accepted.
# DATABASE_URL: `ssl` is the asyncpg TLS mode. REDIS_URL: none; TLS is the rediss://
# scheme, and timeouts and pool size have their own REDIS_* variables.
_DATABASE_SSL_MODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)
_DATABASE_QUERY_ALLOWED = frozenset({"ssl"})


class ConfigurationError(RuntimeError):
    """Raised when the environment does not describe a valid configuration."""


def _is_valid_host(host: str | None) -> bool:
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return _HOSTNAME.fullmatch(host) is not None
    return True


def _is_valid_port(port: int | None) -> bool:
    return port is None or 1 <= port <= _MAX_PORT


def _database_url_problem(raw: str) -> str | None:
    """Return why `raw` is not a usable database URL; never quotes the value."""
    try:
        url = make_url(raw)  # the parser SQLAlchemy itself applies
    except (ArgumentError, ValueError):
        return _UNPARSEABLE
    if url.drivername != _DATABASE_DRIVER:
        return f"scheme must be {_DATABASE_DRIVER}://"
    if not _is_valid_host(url.host):
        return "host is missing or invalid"
    if not _is_valid_port(url.port):
        return f"port must be a number in 1..{_MAX_PORT}"
    if not url.database:
        return "database name is missing"
    return _database_query_problem(url.query)


def _database_query_problem(query: Mapping[str, str | tuple[str, ...]]) -> str | None:
    if not _DATABASE_QUERY_ALLOWED.issuperset(query):
        return (
            f"only these query parameters are allowed: {', '.join(sorted(_DATABASE_QUERY_ALLOWED))}"
        )
    ssl = query.get("ssl")
    if ssl is not None and ssl not in _DATABASE_SSL_MODES:
        return f"ssl must be one of: {', '.join(sorted(_DATABASE_SSL_MODES))}"
    return None


def _redis_url_problem(raw: str) -> str | None:
    """Return why `raw` is not a usable Redis URL; never quotes the value."""
    try:
        parse_redis_url(raw)  # the parser redis-py itself applies
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return _UNPARSEABLE
    if parts.scheme not in _REDIS_SCHEMES or parts.fragment:
        return "must be a redis:// or rediss:// URL"
    if parts.query:
        return "query parameters are not allowed (use the REDIS_* variables)"
    return _redis_location_problem(parts.hostname, port, parts.path)


def _redis_location_problem(host: str | None, port: int | None, path: str) -> str | None:
    if not _is_valid_host(host):
        return "host is missing or invalid"
    if not _is_valid_port(port):
        return f"port must be a number in 1..{_MAX_PORT}"
    if not _REDIS_DB_PATH.fullmatch(path):
        return "database must be a non-negative number, e.g. /0"
    return None


class Settings(BaseSettings):
    """Process configuration (Twelve-Factor III).

    Values come only from environment variables; there is deliberately no `.env`
    loading here so that what runs is exactly what the environment says.
    """

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", frozen=True)

    app_env: AppEnv
    database_url: SecretStr
    redis_url: SecretStr
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"
    db_pool_size: Annotated[int, Field(ge=1, le=50)] = 5
    db_max_overflow: Annotated[int, Field(ge=0, le=50)] = 5
    db_pool_timeout_seconds: Seconds = 5.0
    db_statement_timeout_ms: Annotated[int, Field(gt=0)] = 5000
    slow_query_ms: Annotated[int, Field(gt=0)] = 100
    readiness_timeout_seconds: Seconds = 2.0
    redis_socket_timeout_seconds: Seconds = 2.0
    redis_max_connections: Annotated[int, Field(ge=1, le=200)] = 20

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: SecretStr) -> SecretStr:
        problem = _database_url_problem(value.get_secret_value())
        if problem is not None:
            raise ValueError(problem)
        return value

    @field_validator("redis_url")
    @classmethod
    def _check_redis_url(cls, value: SecretStr) -> SecretStr:
        problem = _redis_url_problem(value.get_secret_value())
        if problem is not None:
            raise ValueError(problem)
        return value


def load_settings() -> Settings:
    """Build settings from the environment.

    Raises:
        ConfigurationError: with one line per invalid variable. Input values are
            never included, because they may contain credentials.
    """
    try:
        return Settings()
    except ValidationError as error:
        problems = [
            f"{'.'.join(str(part) for part in item['loc']).upper()}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False, include_context=False)
        ]
        message = "Invalid configuration:\n  " + "\n  ".join(problems)
        raise ConfigurationError(message) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, validated once."""
    return load_settings()
