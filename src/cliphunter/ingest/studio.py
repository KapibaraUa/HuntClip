"""Parser for structured JSON exported from Google AI Studio (or other LLMs).

Enables full 0 USD offline clipping without calling the paid Gemini API.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from cliphunter.llm.schemas import Category
from cliphunter.models import Candidate, CandidateStatus
from cliphunter.pipeline.timecodes import format_ts, parse_ts

_VALID_CATEGORIES = {
    "emotion",
    "game_moment",
    "story",
    "funny",
    "opinion",
    "fact_advice",
    "conflict",
    "quote",
}

_CATEGORY_MAPPING = {
    "game": "game_moment",
    "gameplay": "game_moment",
    "action": "game_moment",
    "clutch": "game_moment",
    "humor": "funny",
    "joke": "funny",
    "laugh": "funny",
    "react": "emotion",
    "reaction": "emotion",
    "talk": "opinion",
    "chat": "opinion",
    "advice": "fact_advice",
    "tip": "fact_advice",
    "fact": "fact_advice",
}


def _strip_markdown_and_extract_json(raw: str) -> str:
    """Extracts JSON substring from raw text, removing markdown fences or commentary."""
    text = raw.strip()

    # If wrapped in markdown code fences ```json ... ``` or ``` ... ```
    fence_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    match = fence_pattern.search(text)
    if match:
        return match.group(1).strip()

    # Otherwise look for the outermost { ... } or [ ... ]
    start_bracket = text.find("[")
    start_brace = text.find("{")

    if start_bracket != -1 and (start_brace == -1 or start_bracket < start_brace):
        end_bracket = text.rfind("]")
        if end_bracket != -1 and end_bracket > start_bracket:
            return text[start_bracket : end_bracket + 1].strip()

    if start_brace != -1:
        end_brace = text.rfind("}")
        if end_brace != -1 and end_brace > start_brace:
            return text[start_brace : end_brace + 1].strip()

    return text


def _parse_timecode_value(val: Any) -> float:
    """Converts a time value (str 'MM:SS' / 'HH:MM:SS', float, int) into seconds."""
    if isinstance(val, (int, float)):
        return max(0.0, float(val))

    if not isinstance(val, str):
        raise ValueError(f"Cannot parse timecode from type {type(val)}: {val!r}")

    cleaned = val.strip()
    if not cleaned:
        raise ValueError("Empty timecode string")

    # If numeric string like "85" or "85.5"
    try:
        return max(0.0, float(cleaned))
    except ValueError:
        pass

    # Standard "1:23" or "01:23" -> normalize to "01:23" if missing leading zero
    parts = cleaned.split(":")
    if len(parts) == 2 and len(parts[0]) == 1:
        cleaned = f"0{parts[0]}:{parts[1]}"
    elif len(parts) == 3 and len(parts[0]) == 1:
        cleaned = f"0{parts[0]}:{parts[1]}:{parts[2]}"

    return parse_ts(cleaned)


def _normalize_category(cat: Any) -> Category:
    if not isinstance(cat, str):
        return "game_moment"

    normalized = cat.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _VALID_CATEGORIES:
        return normalized  # type: ignore[return-value]

    for key, mapped in _CATEGORY_MAPPING.items():
        if key in normalized:
            return mapped  # type: ignore[return-value]

    return "game_moment"


def parse_studio_json(raw_text: str, default_score: int = 8) -> list[Candidate]:
    """Parses raw text or JSON exported from Google AI Studio into normalized Candidates.

    Accepts:
    - Standard ScanResponse format: `{"notes": "...", "clips": [...]}`
    - Arbitrary dict with keys `clips`, `items`, `moments`, `candidates`
    - Direct JSON array of clip dictionaries `[...]`
    - Markdown-fenced responses ````json ... ````
    """
    clean_json_str = _strip_markdown_and_extract_json(raw_text)
    if not clean_json_str:
        raise ValueError("No JSON content found in the provided text.")

    try:
        data = json.loads(clean_json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to decode JSON from Google AI Studio output: {exc}") from exc

    raw_clips: list[dict[str, Any]] = []

    if isinstance(data, list):
        raw_clips = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        for candidate_key in ("clips", "items", "moments", "candidates", "data", "results"):
            val = data.get(candidate_key)
            if isinstance(val, list):
                raw_clips = [item for item in val if isinstance(item, dict)]
                break

        if not raw_clips and any(k in data for k in ("start", "start_s", "start_time", "from")):
            # The dict itself is a single moment with "start" and "end"
            raw_clips = [data]

    if not raw_clips:
        raise ValueError(
            "No clips/moments found in JSON. Expected an array of clips or a dict with a 'clips' key."
        )

    candidates: list[Candidate] = []

    for i, clip in enumerate(raw_clips, start=1):
        # 1. Resolve start time
        start_raw = (
            clip.get("start")
            or clip.get("start_s")
            or clip.get("start_time")
            or clip.get("from")
            or clip.get("time_start")
            or clip.get("timecode_start")
        )
        if start_raw is None:
            continue

        # 2. Resolve end time
        end_raw = (
            clip.get("end")
            or clip.get("end_s")
            or clip.get("end_time")
            or clip.get("to")
            or clip.get("time_end")
            or clip.get("timecode_end")
        )
        if end_raw is None:
            continue

        try:
            start_s = _parse_timecode_value(start_raw)
            end_s = _parse_timecode_value(end_raw)
        except Exception:
            continue

        if end_s <= start_s:
            continue

        # 3. Resolve title
        title = (
            clip.get("working_title")
            or clip.get("title")
            or clip.get("name")
            or clip.get("caption")
            or clip.get("label")
            or clip.get("hook_text")
            or f"Момент {i} ({format_ts(start_s)} - {format_ts(end_s)})"
        )
        title = str(title).strip()

        # 4. Resolve score
        score_raw = clip.get("score") or clip.get("rating") or clip.get("rank")
        try:
            score = int(round(float(score_raw))) if score_raw is not None else default_score
            score = max(1, min(10, score))
        except (ValueError, TypeError):
            score = default_score

        # 5. Resolve category
        category = _normalize_category(clip.get("category") or clip.get("type"))

        # 6. Additional metadata
        what_happens = str(
            clip.get("what_happens")
            or clip.get("description")
            or clip.get("summary")
            or clip.get("event")
            or ""
        ).strip()
        why_viral = str(
            clip.get("why_viral")
            or clip.get("reason")
            or clip.get("viral_reason")
            or clip.get("explanation")
            or ""
        ).strip()
        visual_hook = str(
            clip.get("visual_hook") or clip.get("visual_evidence") or clip.get("hook") or ""
        ).strip()
        audio_evidence = str(clip.get("audio_evidence") or clip.get("audio") or "").strip()
        hook_text = clip.get("hook_text")
        ending_text = clip.get("ending_text")

        title_opts = clip.get("title_options") or clip.get("titles")
        if isinstance(title_opts, list):
            title_options = [str(t) for t in title_opts if t]
        else:
            title_options = [title]

        cand_id = f"studio_cand_{uuid.uuid4().hex[:10]}"
        cand = Candidate(
            id=cand_id,
            chunk_id=None,
            start_s=start_s,
            end_s=end_s,
            category=category,
            working_title=title,
            score=score,
            status=CandidateStatus.ACCEPTED,
            source_pass="studio_import",
            prompt_version=1,
            visual_hook=visual_hook,
            hook_text=str(hook_text) if hook_text else None,
            what_happens=what_happens,
            visual_evidence=visual_hook,
            audio_evidence=audio_evidence,
            why_viral=why_viral,
            ending_text=str(ending_text) if ending_text else None,
            cuttable_inside=[],
            title_options=title_options,
            risks=[],
        )
        candidates.append(cand)

    if not candidates:
        raise ValueError(
            "Found candidate entries in JSON, but could not parse valid start/end timestamps."
        )

    return candidates
