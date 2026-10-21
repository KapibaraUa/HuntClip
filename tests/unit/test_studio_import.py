"""Unit tests for Google AI Studio JSON parser (src/cliphunter/ingest/studio.py)."""

from __future__ import annotations

import pytest

from cliphunter.ingest.studio import parse_studio_json


def test_parse_standard_scan_response() -> None:
    raw = """
    {
      "notes": "Good stream highlights",
      "clips": [
        {
          "start": "01:15",
          "end": "01:45",
          "category": "game_moment",
          "working_title": "Эпичный клатч",
          "visual_hook": "Взрыв бомбы",
          "what_happens": "Стример выигрывает раунд 1 в 3",
          "why_viral": "Высокая динамика",
          "score": 9
        }
      ]
    }
    """
    candidates = parse_studio_json(raw)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.start_s == 75.0
    assert c.end_s == 105.0
    assert c.duration_s == 30.0
    assert c.working_title == "Эпичный клатч"
    assert c.category == "game_moment"
    assert c.score == 9
    assert c.source_pass == "studio_import"
    assert c.status == "accepted"


def test_parse_markdown_fenced_json() -> None:
    raw = """
    Here is the response from Gemini in Google AI Studio:

    ```json
    {
      "moments": [
        {
          "start": "00:05",
          "end": "00:25",
          "title": "Смешной момент с чатом",
          "category": "funny",
          "score": 8
        },
        {
          "start": "01:05:10",
          "end": "01:05:40",
          "title": "Финальный босс повержен",
          "category": "gameplay",
          "score": 10
        }
      ]
    }
    ```

    Hope this helps!
    """
    candidates = parse_studio_json(raw)
    assert len(candidates) == 2
    assert candidates[0].start_s == 5.0
    assert candidates[0].end_s == 25.0
    assert candidates[0].category == "funny"
    assert candidates[0].score == 8

    # HH:MM:SS -> 1*3600 + 5*60 + 10 = 3910s
    assert candidates[1].start_s == 3910.0
    assert candidates[1].end_s == 3940.0
    assert candidates[1].category == "game_moment"  # mapped from gameplay
    assert candidates[1].score == 10


def test_parse_raw_list_of_moments_with_numeric_seconds() -> None:
    raw = """
    [
      {
        "start_s": 12.5,
        "end_s": 42.5,
        "name": "Реакция на донат",
        "why_viral": "Истеричный смех"
      }
    ]
    """
    candidates = parse_studio_json(raw)
    assert len(candidates) == 1
    assert candidates[0].start_s == 12.5
    assert candidates[0].end_s == 42.5
    assert candidates[0].working_title == "Реакция на донат"
    assert candidates[0].score == 8  # default score


def test_parse_single_dict_moment() -> None:
    raw = '{"start": "0:30", "end": "1:15", "title": "Одиночный клип"}'
    candidates = parse_studio_json(raw)
    assert len(candidates) == 1
    assert candidates[0].start_s == 30.0
    assert candidates[0].end_s == 75.0
    assert candidates[0].working_title == "Одиночный клип"


def test_parse_invalid_or_empty_json() -> None:
    with pytest.raises(ValueError, match="No JSON content found"):
        parse_studio_json("   ")

    with pytest.raises(ValueError, match="Failed to decode JSON"):
        parse_studio_json("{ broken json }")

    with pytest.raises(ValueError, match="No clips/moments found"):
        parse_studio_json('{"foo": "bar"}')
