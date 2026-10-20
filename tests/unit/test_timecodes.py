import pytest

from cliphunter.pipeline.timecodes import format_ts, parse_ts, to_global, within_chunk


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00:00", 0.0),
        ("01:15", 75.0),
        ("1:15", 75.0),
        ("75:30", 4530.0),
        ("01:02:03", 3723.0),
        ("1:02:03", 3723.0),
        ("00:10.5", 10.5),
        (" 02:00 ", 120.0),
    ],
)
def test_parse_ts_valid(raw: str, expected: float) -> None:
    assert parse_ts(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "abc", "12", "01:60", "01:75:00", "1:2", "-1:00", "01:02:03:04"]
)
def test_parse_ts_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_ts(raw)


def test_format_roundtrip() -> None:
    for s in (0, 59, 60, 3599, 3600, 7325):
        assert parse_ts(format_ts(s)) == s
    assert format_ts(75) == "01:15"
    assert format_ts(3723) == "01:02:03"


def test_to_global() -> None:
    assert to_global(30.0, 2580.0) == 2610.0


def test_within_chunk() -> None:
    assert within_chunk(0, 10, 600)
    assert within_chunk(590, 600.5, 600)
    assert not within_chunk(-1, 10, 600)
    assert not within_chunk(10, 10, 600)
    assert not within_chunk(590, 700, 600)
