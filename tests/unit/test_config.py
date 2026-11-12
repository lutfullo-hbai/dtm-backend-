import pytest

from app.core.config import ConfigurationError, Settings, get_settings, load_settings

_SECRET_PASSWORD = "s3cr3t-password"
_OPTIONAL_VARIABLES = (
    "LOG_LEVEL",
    "LOG_FORMAT",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_POOL_TIMEOUT_SECONDS",
    "DB_STATEMENT_TIMEOUT_MS",
    "SLOW_QUERY_MS",
    "READINESS_TIMEOUT_SECONDS",
    "REDIS_SOCKET_TIMEOUT_SECONDS",
)


@pytest.fixture
def valid_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _OPTIONAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL", f"postgresql+asyncpg://dtm:{_SECRET_PASSWORD}@localhost:5432/dtm"
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    return monkeypatch


@pytest.mark.usefixtures("valid_env")
def test_loads_required_values_and_defaults() -> None:
    settings = load_settings()

    assert settings.app_env == "test"
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 5
    assert settings.db_pool_timeout_seconds == 5.0
    assert settings.db_statement_timeout_ms == 5000
    assert settings.slow_query_ms == 100
    assert settings.readiness_timeout_seconds == 2.0
    assert settings.redis_socket_timeout_seconds == 2.0
    assert settings.redis_max_connections == 20


@pytest.mark.usefixtures("valid_env")
def test_secrets_are_masked_in_repr() -> None:
    assert _SECRET_PASSWORD not in repr(load_settings())


def test_missing_database_url_fails_with_clear_message(valid_env: pytest.MonkeyPatch) -> None:
    valid_env.delenv("DATABASE_URL")

    with pytest.raises(ConfigurationError, match="DATABASE_URL: Field required"):
        load_settings()


def test_wrong_database_scheme_fails_without_echoing_the_value(
    valid_env: pytest.MonkeyPatch,
) -> None:
    valid_env.setenv("DATABASE_URL", f"mysql://dtm:{_SECRET_PASSWORD}@localhost/dtm")

    with pytest.raises(ConfigurationError, match="DATABASE_URL") as raised:
        load_settings()

    assert _SECRET_PASSWORD not in str(raised.value)


def test_wrong_redis_scheme_fails(valid_env: pytest.MonkeyPatch) -> None:
    valid_env.setenv("REDIS_URL", "http://localhost:6379")

    with pytest.raises(ConfigurationError, match="REDIS_URL"):
        load_settings()


# A password-like marker: it must never appear in any configuration error.
_MARKER = "Leak3dPwMarker"


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql+asyncpg://dtm:{_MARKER}@/dtm",  # empty host
        f"postgresql+asyncpg://dtm:{_MARKER}@localhost:5o32/dtm",  # non-numeric port
        f"postgresql+asyncpg://dtm:{_MARKER}@localhost:70000/dtm",  # port out of range
        f"postgresql+asyncpg://dtm:{_MARKER}@[bad/dtm",  # broken IPv6 literal
        f"postgresql+asyncpg://dtm:{_MARKER}@localhost:5432/",  # no database name
        f"postgresql+asyncpg://dtm:{_MARKER}@localhost:5432",  # no database name
        f"postgresql://dtm:{_MARKER}@localhost:5432/dtm",  # sync driver
        f"{_MARKER}",  # not a URL at all
    ],
)
def test_malformed_database_url_fails_without_echoing_it(
    valid_env: pytest.MonkeyPatch, url: str
) -> None:
    valid_env.setenv("DATABASE_URL", url)

    with pytest.raises(ConfigurationError, match="DATABASE_URL: ") as raised:
        load_settings()

    assert _MARKER not in str(raised.value)


@pytest.mark.parametrize(
    "url",
    [
        f"redis://:Leak#{_MARKER}@localhost:6379/0",  # unencoded '#' in the password
        f"redis://:Le/ak{_MARKER}@localhost:6379/0",  # unencoded '/'
        f"redis://:Le?ak{_MARKER}@localhost:6379/0",  # unencoded '?'
        f"redis://:{_MARKER}@:6379/0",  # empty host
        f"redis://:{_MARKER}@localhost:63a9/0",  # non-numeric port
        f"redis://:{_MARKER}@localhost:0/0",  # port out of range
        f"redis://:{_MARKER}@[bad",  # broken IPv6 literal
        f"redis://:{_MARKER}@localhost:6379/x",  # non-numeric database
        f"redis://:{_MARKER}@localhost:6379/-1",  # negative database
        f"redis://:{_MARKER}@localhost:6379/0?db=abc",  # bad db query option
        f"unix://:{_MARKER}@/tmp/redis.sock",  # unsupported scheme
    ],
)
def test_malformed_redis_url_fails_without_echoing_it(
    valid_env: pytest.MonkeyPatch, url: str
) -> None:
    valid_env.setenv("REDIS_URL", url)

    with pytest.raises(ConfigurationError, match="REDIS_URL: ") as raised:
        load_settings()

    assert _MARKER not in str(raised.value)


@pytest.mark.parametrize(
    ("variable", "url"),
    [
        ("DATABASE_URL", f"postgresql+asyncpg://dtm:dtm@db/dtm?password={_MARKER}"),
        ("DATABASE_URL", f"postgresql+asyncpg://dtm:dtm@db/dtm?{_MARKER}=1"),
        ("DATABASE_URL", f"postgresql+asyncpg://dtm:{_MARKER}@db/dtm?sslmode=require"),
        ("DATABASE_URL", f"postgresql+asyncpg://dtm:{_MARKER}@db/dtm?command_timeout=0"),
        ("DATABASE_URL", f"postgresql+asyncpg://dtm:dtm@db/dtm?ssl={_MARKER}"),
        ("DATABASE_URL", "postgresql+asyncpg://dtm:dtm@db/dtm?ssl=require&ssl=disable"),
        ("REDIS_URL", f"redis://:{_MARKER}@cache:6379/0?socket_timeout=0"),
        ("REDIS_URL", f"redis://cache:6379/0?password={_MARKER}"),
        ("REDIS_URL", "rediss://cache:6379/0?ssl_cert_reqs=none"),
    ],
)
def test_query_parameters_outside_the_allow_list_fail_without_echoing_them(
    valid_env: pytest.MonkeyPatch, variable: str, url: str
) -> None:
    valid_env.setenv(variable, url)

    with pytest.raises(ConfigurationError, match=f"{variable}: ") as raised:
        load_settings()

    assert _MARKER not in str(raised.value)


@pytest.mark.parametrize("mode", ["disable", "require", "verify-ca", "verify-full"])
def test_database_ssl_mode_is_allowed(valid_env: pytest.MonkeyPatch, mode: str) -> None:
    valid_env.setenv("DATABASE_URL", f"postgresql+asyncpg://dtm:dtm@db/dtm?ssl={mode}")

    assert load_settings().database_url.get_secret_value().endswith(f"?ssl={mode}")


@pytest.mark.parametrize(
    ("database_url", "redis_url"),
    [
        ("postgresql+asyncpg://dtm:p%23w%2Fd%3F@db:5432/dtm", "redis://:p%23w%2Fd@cache:6379/0"),
        ("postgresql+asyncpg://dtm@127.0.0.1/dtm", "rediss://127.0.0.1"),
        ("postgresql+asyncpg://dtm:dtm@[::1]:5432/dtm?ssl=require", "redis://[::1]:6379/"),
        ("postgresql+asyncpg://dtm:a#b@dtm_db/dtm", "redis://dtm_cache/15"),
    ],
)
def test_well_formed_urls_are_accepted(
    valid_env: pytest.MonkeyPatch, database_url: str, redis_url: str
) -> None:
    valid_env.setenv("DATABASE_URL", database_url)
    valid_env.setenv("REDIS_URL", redis_url)

    settings = load_settings()

    assert settings.database_url.get_secret_value() == database_url


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ENV", "dev"),
        ("LOG_LEVEL", "TRACE"),
        ("LOG_FORMAT", "xml"),
        ("DB_POOL_SIZE", "0"),
        ("DB_POOL_SIZE", "51"),
        ("DB_MAX_OVERFLOW", "-1"),
        ("DB_POOL_TIMEOUT_SECONDS", "0"),
        ("DB_STATEMENT_TIMEOUT_MS", "0"),
        ("SLOW_QUERY_MS", "0"),
        ("READINESS_TIMEOUT_SECONDS", "-1"),
        ("REDIS_SOCKET_TIMEOUT_SECONDS", "0"),
        ("REDIS_SOCKET_TIMEOUT_SECONDS", "-1"),
        ("READINESS_TIMEOUT_SECONDS", "inf"),
        ("DB_POOL_TIMEOUT_SECONDS", "inf"),
        ("REDIS_SOCKET_TIMEOUT_SECONDS", "Infinity"),
        ("READINESS_TIMEOUT_SECONDS", "nan"),
        ("REDIS_MAX_CONNECTIONS", "0"),
        ("REDIS_MAX_CONNECTIONS", "201"),
        ("REDIS_MAX_CONNECTIONS", "1.5"),
    ],
)
def test_out_of_range_values_fail(valid_env: pytest.MonkeyPatch, name: str, value: str) -> None:
    valid_env.setenv(name, value)

    with pytest.raises(ConfigurationError, match=name):
        load_settings()


@pytest.mark.parametrize(("pool_size", "overflow"), [("1", "0"), ("50", "50")])
def test_pool_bounds_are_inclusive(
    valid_env: pytest.MonkeyPatch, pool_size: str, overflow: str
) -> None:
    valid_env.setenv("DB_POOL_SIZE", pool_size)
    valid_env.setenv("DB_MAX_OVERFLOW", overflow)

    settings = load_settings()

    assert settings.db_pool_size == int(pool_size)
    assert settings.db_max_overflow == int(overflow)


@pytest.mark.parametrize("value", ["1", "200"])
def test_redis_max_connections_bounds_are_inclusive(
    valid_env: pytest.MonkeyPatch, value: str
) -> None:
    valid_env.setenv("REDIS_MAX_CONNECTIONS", value)

    assert load_settings().redis_max_connections == int(value)


@pytest.mark.usefixtures("valid_env")
def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        first = get_settings()
        assert get_settings() is first
        assert isinstance(first, Settings)
    finally:
        get_settings.cache_clear()
