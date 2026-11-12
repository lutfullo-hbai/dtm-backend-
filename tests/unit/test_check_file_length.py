from pathlib import Path

import pytest

from scripts.check_file_length import (
    SOURCE_LIMIT,
    TEST_LIMIT,
    Unreadable,
    Violation,
    count_lines,
    find_violations,
    main,
)


@pytest.fixture(autouse=True)
def _in_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Paths stay relative, as pre-commit and make pass them, so the absolute
    # location of the temporary directory cannot affect test-file detection.
    monkeypatch.chdir(tmp_path)


def _write_lines(path: Path, count: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * count, encoding="utf-8")
    return path


def test_source_file_at_limit_passes() -> None:
    path = _write_lines(Path("app/module.py"), SOURCE_LIMIT)

    assert find_violations([path]) == []


def test_source_file_over_limit_fails() -> None:
    path = _write_lines(Path("app/module.py"), SOURCE_LIMIT + 1)

    assert find_violations([path]) == [Violation(path, SOURCE_LIMIT + 1, SOURCE_LIMIT)]


def test_test_file_gets_the_higher_limit() -> None:
    at_limit = _write_lines(Path("tests/unit/test_ok.py"), TEST_LIMIT)
    over_limit = _write_lines(Path("tests/unit/test_long.py"), TEST_LIMIT + 1)

    assert find_violations([at_limit, over_limit]) == [
        Violation(over_limit, TEST_LIMIT + 1, TEST_LIMIT)
    ]


def test_file_named_tests_outside_a_tests_directory_is_source() -> None:
    path = _write_lines(Path("app/tests.py"), SOURCE_LIMIT + 1)

    assert find_violations([path]) == [Violation(path, SOURCE_LIMIT + 1, SOURCE_LIMIT)]


def test_directories_are_searched_recursively_for_python_only() -> None:
    long_module = _write_lines(Path("app/deep/module.py"), SOURCE_LIMIT + 1)
    _write_lines(Path("app/notes.txt"), SOURCE_LIMIT + 1)

    assert [violation.path for violation in find_violations([Path("app")])] == [long_module]


def test_trailing_newline_does_not_count_as_a_line() -> None:
    path = Path("module.py")
    path.write_text("a = 1\nb = 2\n", encoding="utf-8")

    assert count_lines(path) == 2


def test_main_reports_violations_and_exits_non_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_lines(Path("app/module.py"), SOURCE_LIMIT + 1)

    exit_code = main([str(path)])

    assert exit_code == 1
    assert capsys.readouterr().err == f"{path}: {SOURCE_LIMIT + 1} lines (limit {SOURCE_LIMIT})\n"


def test_main_exits_zero_when_all_files_fit() -> None:
    path = _write_lines(Path("app/module.py"), 1)

    assert main([str(path)]) == 0


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"", 0),
        (b"a = 1", 1),
        (b"a = 1\nb = 2", 2),
        (b"a = 1\r\nb = 2\r\n", 2),
        # Form feed, vertical tab, lone CR and U+2028 are not line ends.
        (b"a = 1\x0c\x0b\rb = 2\n", 1),
        ("a = '\u2028'\n".encode(), 1),
    ],
)
def test_only_line_feeds_end_lines(content: bytes, expected: int) -> None:
    path = Path("module.py")
    path.write_bytes(content)

    assert count_lines(path) == expected


def test_form_feeds_cannot_inflate_the_count_past_the_limit() -> None:
    path = Path("app/module.py")
    path.parent.mkdir()
    path.write_bytes(b"x = 1\x0c\n" * SOURCE_LIMIT)

    assert find_violations([path]) == []


def test_invalid_utf8_is_reported_unreadable(capsys: pytest.CaptureFixture[str]) -> None:
    path = Path("app/latin1.py")
    path.parent.mkdir()
    path.write_bytes(b"name = '\xe9'\n")

    exit_code = main([str(path)])

    assert exit_code == 1
    assert capsys.readouterr().err == f"{path}: unreadable\n"


def test_missing_file_is_reported_unreadable() -> None:
    path = Path("app/missing.py")

    assert find_violations([path]) == [Unreadable(path)]
    assert main([str(path)]) == 1


@pytest.mark.parametrize("hidden", [".venv", ".git", ".mypy_cache"])
def test_hidden_directories_are_not_walked(hidden: str) -> None:
    _write_lines(Path(f"project/{hidden}/lib/module.py"), SOURCE_LIMIT + 1)
    _write_lines(Path(f"project/{hidden}/broken.py"), 0).write_bytes(b"\xff")
    visible = _write_lines(Path("project/app/module.py"), SOURCE_LIMIT + 1)

    assert [problem.path for problem in find_violations([Path("project")])] == [visible]
