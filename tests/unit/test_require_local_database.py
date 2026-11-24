"""The guard in front of destructive make targets accepts only local database hosts."""

import pytest

from scripts import require_local_database

_SECRET_PASSWORD = "s3cret-pw"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://dtm:dtm@127.0.0.1:5432/dtm_ci",
        "postgresql+asyncpg://dtm:dtm@localhost/dtm",
        "postgresql+asyncpg://dtm:dtm@[::1]:5432/dtm",
    ],
)
def test_local_database_is_accepted(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", url)

    assert require_local_database.main() == 0


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql+asyncpg://dtm:{_SECRET_PASSWORD}@db.example.com:5432/dtm",
        f"postgresql+asyncpg://dtm:{_SECRET_PASSWORD}@10.0.0.5/dtm",
        f"postgresql+asyncpg://dtm:{_SECRET_PASSWORD}@127.0.0.1.example.com/dtm",
        "postgresql+asyncpg:///dtm",  # no host: the driver would pick a socket or default
        "not a url",
        "",
    ],
)
def test_other_hosts_are_refused_without_echoing_the_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)

    assert require_local_database.main() == 2

    err = capsys.readouterr().err
    assert "refusing to run" in err
    assert _SECRET_PASSWORD not in err


def test_missing_database_url_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert require_local_database.main() == 2
