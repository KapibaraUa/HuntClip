"""Prompt files: first line is `version: N`, the rest is the body."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    body: str


def load_prompt(name: str, directory: Path = PROMPTS_DIR) -> Prompt:
    text = (directory / f"{name}.md").read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    m = re.fullmatch(r"version:\s*(\d+)\s*", first.strip())
    if not m:
        raise ValueError(f"prompts/{name}.md must start with 'version: N'")
    return Prompt(name, int(m.group(1)), rest.strip())


def scan_user_message(
    *,
    channel: str,
    game: str,
    language: str,
    clip_min_s: float,
    clip_max_s: float,
    max_candidates: int,
    prompt_addon: str = "",
) -> str:
    """Per-chunk user message from plan Appendix A.1."""
    return (
        f"Фрагмент: стрим {channel}, игра или тема: {game}\n"
        f"Язык стрима: {language}\n"
        f"Длительность клипа: от {clip_min_s:g} до {clip_max_s:g} секунд\n"
        f"Максимум кандидатов в этом фрагменте: {max_candidates}\n"
        f"Особые пожелания профиля: {prompt_addon}"
    )
