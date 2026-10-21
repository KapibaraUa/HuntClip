"""Profile management endpoints for streamer presets, regions, and prompt addons."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cliphunter.db.models import ProfileORM
from cliphunter.db.repo import get_profile, list_profiles, upsert_profile
from cliphunter.db.session import get_db
from cliphunter.models import Profile

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

DEFAULT_PROFILES: list[Profile] = [
    Profile(
        name="default",
        language="ru",
        game="",
        clip_min_s=20.0,
        clip_max_s=90.0,
        prompt_addon="Универсальный профиль для русскоязычных стримов.",
    ),
    Profile(
        name="evelone2004",
        language="ru",
        game="CS2 / Just Chatting",
        facecam_rect={"x": 0.72, "y": 0.05, "w": 0.25, "h": 0.3},
        gameplay_rect={"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        clip_min_s=15.0,
        clip_max_s=75.0,
        prompt_addon="Фокус на эмоциональных реакциях, смехе, шутках над тиммейтами, клатчах и общении со стримерами.",
    ),
    Profile(
        name="gaming_action",
        language="ru",
        game="Shooter / Action",
        facecam_rect={"x": 0.75, "y": 0.02, "w": 0.23, "h": 0.28},
        gameplay_rect={"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        clip_min_s=20.0,
        clip_max_s=60.0,
        prompt_addon="Фокус на хайлайтах: эйсы, клатчи, нелепые смерти, баги, крики стримера.",
    ),
    Profile(
        name="just_chatting",
        language="ru",
        game="Just Chatting",
        facecam_rect=None,
        gameplay_rect=None,
        clip_min_s=30.0,
        clip_max_s=90.0,
        prompt_addon="Фокус на историях из жизни, смешных донатах, обсуждениях новостей и горячих спорах.",
    ),
]


class ProfilePayload(BaseModel):
    name: str = Field(..., min_length=1)
    language: str = "ru"
    game: str = ""
    facecam_rect: dict[str, float] | None = None
    gameplay_rect: dict[str, float] | None = None
    clip_min_s: float = 20.0
    clip_max_s: float = 90.0
    prompt_addon: str = ""
    template: str | None = None
    watermark: str | None = None


def _profile_to_dict(p: ProfileORM | Profile) -> dict[str, Any]:
    return {
        "name": p.name,
        "language": p.language,
        "game": p.game,
        "facecam_rect": p.facecam_rect,
        "gameplay_rect": p.gameplay_rect,
        "clip_min_s": p.clip_min_s,
        "clip_max_s": p.clip_max_s,
        "prompt_addon": p.prompt_addon,
    }


@router.get("")
async def list_available_profiles(
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Lists all configured streamer profiles."""
    db_profiles = await list_profiles(session)
    if not db_profiles:
        # Seed default profiles into database
        for default_p in DEFAULT_PROFILES:
            await upsert_profile(session, default_p)
        await session.commit()
        db_profiles = await list_profiles(session)

    return [_profile_to_dict(p) for p in db_profiles]


@router.get("/{name}")
async def get_profile_by_name(
    name: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieves profile settings by name."""
    profile = await get_profile(session, name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _profile_to_dict(profile)


@router.put("/{name}")
async def update_or_create_profile(
    name: str,
    payload: ProfilePayload,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Creates or updates a streamer profile."""
    prof_model = Profile(
        name=name,
        language=payload.language,
        game=payload.game,
        facecam_rect=payload.facecam_rect,
        gameplay_rect=payload.gameplay_rect,
        clip_min_s=payload.clip_min_s,
        clip_max_s=payload.clip_max_s,
        prompt_addon=payload.prompt_addon,
        template=payload.template,
        watermark=payload.watermark,
    )
    orm = await upsert_profile(session, prof_model)
    await session.commit()
    return _profile_to_dict(orm)
