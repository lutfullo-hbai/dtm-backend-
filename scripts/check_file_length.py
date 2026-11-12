"""Fail when a Python file exceeds the line limit: 300 lines, 500 for test files.

Usage: python -m scripts.check_file_length [PATH ...]
Directories are searched recursively for *.py files, skipping hidden directories
(including .venv); with no PATH, the default source directories that exist are
checked. A file that cannot be read as UTF-8 is reported as unreadable and fails.
"""

import argparse
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

SOURCE_LIMIT = 300
TEST_LIMIT = 500
DEFAULT_PATHS = (Path("app"), Path("tests"), Path("scripts"), Path("migrations"))


@dataclass(frozen=True, slots=True)
class Violation:
    """A file that is longer than its limit."""

    path: Path
    lines: int
    limit: int

    def describe(self) -> str:
        """Render as `path: N lines (limit L)`."""
        return f"{self.path}: {self.lines} lines (limit {self.limit})"


@dataclass(frozen=True, slots=True)
class Unreadable:
    """A file that is missing or not valid UTF-8, so it cannot be measured."""

    path: Path

    def describe(self) -> str:
        """Render as `path: unreadable`."""
        return f"{self.path}: unreadable"


def is_test_file(path: Path) -> bool:
    """Test files are those inside a `tests` directory."""
    return "tests" in path.parts[:-1]


def limit_for(path: Path) -> int:
    """Return the maximum allowed line count for a file."""
    return TEST_LIMIT if is_test_file(path) else SOURCE_LIMIT


def count_lines(path: Path) -> int:
    """Count lines the way an editor does; a final newline does not add a line.

    Only LF ends a line: form feed, a lone CR and Unicode line separators do not
    (str.splitlines would split on all of them). Bytes are decoded untranslated,
    so CRLF counts once.

    Raises:
        OSError: if the file cannot be read.
        UnicodeDecodeError: if the file is not valid UTF-8.
    """
    text = path.read_bytes().decode("utf-8")
    newlines = text.count("\n")
    return newlines + 1 if text and not text.endswith("\n") else newlines


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    """Yield *.py files from the given files and directories, in sorted order.

    Hidden directories (.venv, .git, .mypy_cache, ...) below a given directory
    are not entered.
    """
    for path in paths:
        if path.is_dir():
            found: list[Path] = []
            for directory, subdirectories, files in path.walk():
                subdirectories[:] = [name for name in subdirectories if not _is_hidden(name)]
                found.extend(directory / name for name in files if name.endswith(".py"))
            yield from sorted(found)
        elif path.suffix == ".py":
            yield path


def find_violations(paths: Iterable[Path]) -> list[Violation | Unreadable]:
    """Return every file in `paths` that exceeds its limit or cannot be read."""
    violations: list[Violation | Unreadable] = []
    for path in iter_python_files(paths):
        try:
            lines = count_lines(path)
        except (OSError, UnicodeDecodeError):
            violations.append(Unreadable(path))
            continue
        limit = limit_for(path)
        if lines > limit:
            violations.append(Violation(path=path, lines=lines, limit=limit))
    return violations


def main(argv: list[str] | None = None) -> int:
    """Check the given paths (or the defaults); exit 1 if any file fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    requested: list[Path] = args.paths
    paths = requested or [path for path in DEFAULT_PATHS if path.exists()]
    violations = find_violations(paths)
    for violation in violations:
        sys.stderr.write(violation.describe() + "\n")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
