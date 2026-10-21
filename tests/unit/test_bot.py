"""Unit tests for Telegram bot router, handlers, and authorization (plan, Phase 6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message, User

from cliphunter.bot.telegram import (
    create_bot_router,
    is_user_allowed,
)
from cliphunter.config import Settings
from cliphunter.db.session import Database
from cliphunter.worker.tasks import JobTaskManager


def test_is_user_allowed() -> None:
    # Empty whitelist allows all
    assert is_user_allowed(12345, []) is True
    # Explicit whitelist
    assert is_user_allowed(12345, [12345, 67890]) is True
    assert is_user_allowed(99999, [12345, 67890]) is False


def test_create_bot_router_without_token() -> None:
    settings = Settings(telegram_token="")
    db = MagicMock(spec=Database)
    task_mgr = MagicMock(spec=JobTaskManager)

    router, bot = create_bot_router(settings, db, task_mgr)
    assert router is not None
    assert bot is None  # no token provided


def test_create_bot_router_with_token() -> None:
    settings = Settings(telegram_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    db = MagicMock(spec=Database)
    task_mgr = MagicMock(spec=JobTaskManager)

    router, bot = create_bot_router(settings, db, task_mgr)
    assert router is not None
    assert bot is not None


@pytest.mark.asyncio
async def test_bot_cmd_start_whitelist_rejected() -> None:
    settings = Settings(
        telegram_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        telegram_allowed_ids=[99999],
    )
    db = MagicMock(spec=Database)
    task_mgr = MagicMock(spec=JobTaskManager)
    router, _ = create_bot_router(settings, db, task_mgr)

    # Find the start handler
    start_handler = None
    for endpoint in router.message.handlers:
        if endpoint.callback.__name__ == "cmd_start":
            start_handler = endpoint.callback
            break
    assert start_handler is not None

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=11111, is_bot=False, first_name="Test")
    msg.reply = AsyncMock()

    await start_handler(msg)
    msg.reply.assert_called_once()
    assert "Доступ запрещён" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_bot_cmd_start_authorized() -> None:
    settings = Settings(
        telegram_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        telegram_allowed_ids=[11111],
    )
    db = MagicMock(spec=Database)
    task_mgr = MagicMock(spec=JobTaskManager)
    router, _ = create_bot_router(settings, db, task_mgr)

    start_handler = None
    for endpoint in router.message.handlers:
        if endpoint.callback.__name__ == "cmd_start":
            start_handler = endpoint.callback
            break
    assert start_handler is not None

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=11111, is_bot=False, first_name="Test")
    msg.reply = AsyncMock()

    await start_handler(msg)
    msg.reply.assert_called_once()
    assert "ClipHunter Bot" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_bot_cmd_cancel() -> None:
    settings = Settings(
        telegram_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        telegram_allowed_ids=[],
    )
    db = MagicMock(spec=Database)
    task_mgr = MagicMock(spec=JobTaskManager)
    task_mgr.cancel_job.return_value = True

    router, _ = create_bot_router(settings, db, task_mgr)

    cancel_handler = None
    for endpoint in router.message.handlers:
        if endpoint.callback.__name__ == "cmd_cancel":
            cancel_handler = endpoint.callback
            break
    assert cancel_handler is not None

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=11111, is_bot=False, first_name="Test")
    msg.text = "/cancel job_12345"
    msg.reply = AsyncMock()

    await cancel_handler(msg)
    task_mgr.cancel_job.assert_called_once_with("job_12345")
    assert "отменена" in msg.reply.call_args[0][0]
