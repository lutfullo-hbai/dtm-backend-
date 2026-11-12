"""Read what the application logged: the real JSON lines written to stdout."""

import json
from typing import Any

import pytest

LogLine = dict[str, Any]


class LogReader:
    """Parses every stdout line captured so far; each one must be a JSON object."""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self._capsys = capsys
        self._lines: list[LogLine] = []

    def lines(self) -> list[LogLine]:
        """All lines logged since the fixture started (accumulates across calls)."""
        out = self._capsys.readouterr().out
        self._lines.extend(json.loads(line) for line in out.splitlines())
        return list(self._lines)

    def events(self, event: str) -> list[LogLine]:
        """Lines whose `event` is `event`."""
        return [line for line in self.lines() if line.get("event") == event]
