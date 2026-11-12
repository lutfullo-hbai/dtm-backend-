import importlib
from pathlib import Path

import pytest

from scripts import export_openapi


def test_export_ignores_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = export_openapi.render_openapi()
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "abc")
    monkeypatch.setenv("DATABASE_URL", "not-a-url")
    monkeypatch.setenv("APP_ENV", "nonsense")

    # Re-import too: nothing may read the environment at import time either.
    reloaded = importlib.reload(export_openapi)

    assert reloaded.render_openapi() == baseline


def test_check_passes_for_fresh_file_and_fails_for_stale(tmp_path: Path) -> None:
    output = tmp_path / "openapi.json"

    assert export_openapi.main(["--output", str(output)]) == 0
    assert export_openapi.main(["--output", str(output), "--check"]) == 0

    output.write_text("{}\n", encoding="utf-8")
    assert export_openapi.main(["--output", str(output), "--check"]) == 1
