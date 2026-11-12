"""Export the OpenAPI document deterministically.

Usage: python -m scripts.export_openapi [--output PATH] [--check]
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from app.core.config import Settings
from app.main import create_app

DEFAULT_OUTPUT = Path("docs/api/openapi.json")


class _PlaceholderSettings(Settings):
    """Settings built only from constructor arguments, never from the environment.

    The schema does not depend on configuration and the lifespan never runs here,
    so the export must give the same result whatever the shell has exported.
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del settings_cls, env_settings, dotenv_settings, file_secret_settings
        return (init_settings,)


def render_openapi() -> str:
    """Render the schema with sorted keys and a trailing newline."""
    settings = _PlaceholderSettings(
        app_env="local",
        database_url=SecretStr("postgresql+asyncpg://openapi:openapi@localhost:5432/openapi"),
        redis_url=SecretStr("redis://localhost:6379/0"),
    )
    schema = create_app(settings).openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the schema to --output, or with --check exit 1 if that file is stale."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args(argv)
    output: Path = args.output
    rendered = render_openapi()
    if args.check:
        current = output.read_text(encoding="utf-8") if output.exists() else ""
        if current != rendered:
            sys.stderr.write(f"{output} is out of date; run `make openapi`.\n")
            return 1
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
