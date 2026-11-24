"""Refuse to continue unless DATABASE_URL points at this machine (exit 2 otherwise).

Usage: python -m scripts.require_local_database

Guards destructive targets (`make migrations-roundtrip` runs `downgrade base`): with
APP_ENV=test exported by mistake, a shell pointed at a remote database still cannot
wipe it. The URL is never printed; it may carry a password.
"""

import os
import sys

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
REFUSED = 2


def database_host(url: str) -> str | None:
    """The host of a database URL, or None when it cannot be parsed or has none."""
    try:
        return make_url(url).host
    except (ArgumentError, ValueError):
        return None


def main() -> int:
    """Exit 0 for a local host; otherwise explain on stderr and exit 2."""
    if database_host(os.environ.get("DATABASE_URL", "")) in LOCAL_HOSTS:
        return 0
    sys.stderr.write(
        "refusing to run: DATABASE_URL must point at 127.0.0.1, localhost or ::1\n"
        "(this target destroys data; use a disposable local database).\n"
    )
    return REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
