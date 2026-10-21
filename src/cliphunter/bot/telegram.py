"""Telegram bot implementation using aiogram 3 (plan, Phase 6)."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from cliphunter.config import Settings, get_settings
from cliphunter.db.repo import (
    create_job,
    get_clips_for_job,
    get_job,
    list_jobs,
    list_profiles,
    upsert_source,
)
from cliphunter.db.session import Database, get_db_manager
from cliphunter.ingest.probe import probe_source
from cliphunter.llm.budget import estimate_video_cost
from cliphunter.models import Job, JobParams, Profile
from cliphunter.worker.tasks import JobTaskManager
from cliphunter.worker.tasks import task_manager as default_task_manager

log = logging.getLogger(__name__)

# URL pattern for YouTube, Twitch, Kick, direct video links
_URL_REGEX = re.compile(r"https?://[^\s<>\"']+")

# In-memory store for pending launch requests: req_id -> dict
_pending_launches: dict[str, dict[str, Any]] = {}

# In-memory user active profiles: user_id -> profile_id
_user_profiles: dict[int, str] = {}


def is_user_allowed(user_id: int, allowed_ids: list[int]) -> bool:
    """Verifies whether a telegram user is in the configured whitelist."""
    if not allowed_ids:
        return True
    return user_id in allowed_ids


def create_bot_router(
    settings: Settings,
    db: Database,
    task_mgr: JobTaskManager,
) -> tuple[Router, Bot | None]:
    token = settings.telegram_token.get_secret_value()
    bot = Bot(token=token) if token else None
    router = Router(name="cliphunter_bot")

    # Access check helper
    async def check_access(message: Message) -> bool:
        user_id = message.from_user.id if message.from_user else 0
        if not is_user_allowed(user_id, settings.telegram_allowed_ids):
            await message.reply(
                f"⛔ Доступ запрещён.\nВаш User ID: `{user_id}`.\n"
                f"Для доступа добавьте ваш ID в TELEGRAM_ALLOWED_IDS.",
                parse_mode="Markdown",
            )
            return False
        return True

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        if not await check_access(message):
            return
        welcome = (
            "👋 **Привет! Я ClipHunter Bot** — автономный ИИ-помощник для поиска вирусных клипов в стримах.\n\n"
            "📌 **Как пользоваться:**\n"
            "• Отправьте ссылку на YouTube или Twitch (стрим, запись, VOD)\n"
            "• Или загрузите видеофайл прямо в чат\n"
            "• Бот рассчитает токеноёмкость и стоимость по Gemini\n"
            "• После подтверждения смонтирует вертикальные клипы 9:16 и пришлёт их сюда!\n\n"
            "⚙️ **Команды:**\n"
            "/jobs — список недавних задач\n"
            "/profiles — выбор активного профиля стримера\n"
            "/cancel `<job_id>` — отменить выполнение задачи\n"
            "/help — подробная справка"
        )
        await message.reply(welcome, parse_mode="Markdown")

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        if not await check_access(message):
            return
        help_text = (
            "📖 **Справка по ClipHunter Bot**\n\n"
            "**Поддерживаемые источники:**\n"
            "• YouTube (видео, записи стримов, Shorts)\n"
            "• Twitch (VOD, клипы, трансляции)\n"
            "• Локальные mp4/mkv видеофайлы (до 50 МБ через Telegram)\n\n"
            "**Команды:**\n"
            "• `/jobs` — просмотр последних 10 задач и их статуса\n"
            "• `/cancel <job_id>` — прерывание выполнения задачи\n"
            "• `/profiles` — переключение пресета (default, evelone2004, gaming_action, just_chatting)\n\n"
            "**Лимиты Telegram:**\n"
            "Файлы до 50 МБ отправляются прямо видеосообщением в чат. "
            "Если клип превышает 50 МБ, бот присылает путь к файлу на сервере."
        )
        await message.reply(help_text, parse_mode="Markdown")

    @router.message(Command("jobs"))
    async def cmd_jobs(message: Message) -> None:
        if not await check_access(message):
            return
        async with db.session_factory() as session:
            jobs = await list_jobs(session, limit=10)

        if not jobs:
            await message.reply("📭 Задач пока нет.")
            return

        lines = ["📋 **Последние задачи:**\n"]
        for j in jobs:
            status_icon = (
                "⏳" if j.status == "running" else ("✅" if j.status == "completed" else "⚠️")
            )
            lines.append(
                f"{status_icon} `{j.id}` — {j.status} ({j.stage})\n"
                f"   Стоимость: ${j.cost_usd:.3f} | Создано: {j.created_at.strftime('%H:%M:%S') if j.created_at else ''}"
            )

        await message.reply("\n".join(lines), parse_mode="Markdown")

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message) -> None:
        if not await check_access(message):
            return
        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.reply("Использование: `/cancel <job_id>`", parse_mode="Markdown")
            return
        job_id = parts[1].strip()
        cancelled = task_mgr.cancel_job(job_id)
        if cancelled:
            await message.reply(f"🛑 Задача `{job_id}` отменена.", parse_mode="Markdown")
        else:
            await message.reply(
                f"⚠️ Задача `{job_id}` не найдена или не запущена.", parse_mode="Markdown"
            )

    @router.message(Command("profiles"))
    async def cmd_profiles(message: Message) -> None:
        if not await check_access(message):
            return
        async with db.session_factory() as session:
            profiles = await list_profiles(session)

        user_id = message.from_user.id if message.from_user else 0
        current_prof = _user_profiles.get(user_id, "default")

        keyboard_buttons = []
        for p in profiles:
            mark = "✓ " if p.name == current_prof else ""
            keyboard_buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{mark}{p.name}", callback_data=f"set_profile:{p.name}"
                    )
                ]
            )

        kb = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        await message.reply(
            f"👤 **Текущий профиль:** `{current_prof}`\nВыберите профиль стримера для поиска клипов:",
            reply_markup=kb,
            parse_mode="Markdown",
        )

    @router.callback_query(F.data.startswith("set_profile:"))
    async def cb_set_profile(callback: CallbackQuery) -> None:
        if not callback.data or not isinstance(callback.message, Message):
            return
        prof_name = callback.data.split(":", 1)[1]
        user_id = callback.from_user.id
        _user_profiles[user_id] = prof_name
        await callback.answer(f"Профиль изменён на {prof_name}")
        await callback.message.edit_text(
            f"✅ Выбран профиль: `{prof_name}`",
            parse_mode="Markdown",
        )

    @router.message(F.video | F.document)
    async def handle_video_file(message: Message) -> None:
        if not await check_access(message):
            return

        doc = message.video or message.document
        if not doc:
            return

        file_name = getattr(doc, "file_name", None) or f"video_{doc.file_id[:8]}.mp4"
        if not file_name.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".avi")):
            return

        status_msg = await message.reply("⏳ Загружаю видеофайл на сервер...")
        upload_dir = Path(settings.data_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        local_path = upload_dir / f"{doc.file_id}_{file_name}"

        assert message.bot is not None
        file_info = await message.bot.get_file(doc.file_id)
        assert file_info.file_path is not None
        await message.bot.download_file(file_info.file_path, local_path)

        await status_msg.delete()
        await _prompt_launch_confirmation(message, str(local_path), settings, db)

    @router.message(F.text)
    async def handle_text_url(message: Message) -> None:
        if not await check_access(message):
            return

        text = message.text or ""
        urls = _URL_REGEX.findall(text)
        if not urls:
            await message.reply("❓ Пришлите ссылку на YouTube/Twitch или отправьте видеофайл.")
            return

        target_url = urls[0]
        await _prompt_launch_confirmation(message, target_url, settings, db)

    @router.callback_query(F.data.startswith("launch:"))
    async def cb_launch_job(callback: CallbackQuery) -> None:
        if not callback.data or not isinstance(callback.message, Message):
            return

        req_id = callback.data.split(":", 1)[1]
        launch_data = _pending_launches.pop(req_id, None)
        if not launch_data:
            await callback.answer("⚠️ Срок действия запроса истёк.", show_alert=True)
            return

        await callback.answer("🚀 Запуск пайплайна...")
        target = launch_data["target"]
        profile_id = launch_data["profile_id"]

        try:
            source_meta = await asyncio.to_thread(probe_source, target)
        except Exception as exc:
            await callback.message.edit_text(f"❌ Ошибка получения источника: {exc}")
            return

        job_id = f"job_{uuid.uuid4().hex[:10]}"
        job = Job(
            id=job_id,
            source=source_meta,
            profile=Profile(name=profile_id),
            params=JobParams(
                clip_min_s=settings.clip_min_s,
                clip_max_s=settings.clip_max_s,
                max_cost_usd=settings.max_cost_usd_per_job,
                auto_yes=True,  # In Telegram bot, we auto-render confirmed jobs
            ),
        )

        async with db.session_factory() as session:
            await upsert_source(session, source_meta)
            await create_job(session, job)
            await session.commit()

        # Start job
        task_mgr.start_job(job_id)

        chat_id = callback.message.chat.id
        msg_id = callback.message.message_id
        assert callback.bot is not None

        await callback.message.edit_text(
            f"🚀 **Задача запущена: `{job_id}`**\n📹 {source_meta.title}\n⏳ Этап: S0 Ingest...",
            parse_mode="Markdown",
        )

        # Track progress and deliver clips
        asyncio.create_task(
            track_job_and_deliver(
                bot=callback.bot,
                chat_id=chat_id,
                message_id=msg_id,
                job_id=job_id,
                db=db,
            )
        )

    @router.callback_query(F.data.startswith("cancel_launch:"))
    async def cb_cancel_launch(callback: CallbackQuery) -> None:
        if not callback.data or not isinstance(callback.message, Message):
            return
        req_id = callback.data.split(":", 1)[1]
        _pending_launches.pop(req_id, None)
        await callback.answer("Отменено.")
        await callback.message.edit_text("❌ Запуск отменён.")

    return router, bot


async def _prompt_launch_confirmation(
    message: Message,
    target: str,
    settings: Settings,
    db: Database,
) -> None:
    """Probes the source, estimates Gemini cost, and displays confirmation buttons."""
    user_id = message.from_user.id if message.from_user else 0
    profile_id = _user_profiles.get(user_id, "default")

    status_msg = await message.reply("🔍 Анализирую источник и рассчитываю стоимость...")

    try:
        source_meta = await asyncio.to_thread(probe_source, target)
    except Exception as exc:
        await status_msg.edit_text(f"❌ Не удалось проанализировать источник: {exc}")
        return

    dur = source_meta.duration_s
    est_cost = estimate_video_cost(dur, settings.media_res_scan, settings) if dur > 0 else 0.5
    dur_min = round(dur / 60.0, 1) if dur > 0 else "Live"

    req_id = uuid.uuid4().hex[:8]
    _pending_launches[req_id] = {
        "target": target,
        "profile_id": profile_id,
    }

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🚀 Запустить (${est_cost:.2f})",
                    callback_data=f"launch:{req_id}",
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_launch:{req_id}"),
            ]
        ]
    )

    info_card = (
        f"📹 **{source_meta.title}**\n"
        f"👤 Канал: {source_meta.channel}\n"
        f"⏱ Длительность: {dur_min} мин\n"
        f"⚙️ Профиль: `{profile_id}`\n"
        f"💰 Оценка стоимости Gemini: **${est_cost:.2f}**\n\n"
        f"Запустить автономный поиск и нарезку клипов?"
    )

    await status_msg.edit_text(info_card, reply_markup=kb, parse_mode="Markdown")


async def track_job_and_deliver(
    bot: Bot,
    chat_id: int,
    message_id: int,
    job_id: str,
    db: Database,
) -> None:
    """Polls/listens to job progress, updates Telegram status message, and delivers rendered clips."""
    last_text = ""
    last_update_time = 0.0

    async def update_status(text: str) -> None:
        nonlocal last_text, last_update_time
        now = asyncio.get_event_loop().time()
        if text != last_text and (now - last_update_time >= 1.5):
            last_text = text
            last_update_time = now
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="Markdown",
                )
            except Exception as exc:
                log.debug("Failed to edit telegram progress message: %s", exc)

    # Listen to broadcaster events or poll DB
    while True:
        await asyncio.sleep(2.0)
        async with db.session_factory() as session:
            job_orm = await get_job(session, job_id)

        if not job_orm:
            break

        status = job_orm.status
        stage = job_orm.stage
        cost = job_orm.cost_usd

        if status == "running":
            await update_status(
                f"⚡ **Задача `{job_id}` выполняется**\n"
                f"Этап: `{stage}`\n"
                f"Текущие расходы: ${cost:.3f}\n"
                f"⏳ Идёт ИИ-анализ..."
            )
        elif status == "awaiting_review":
            await update_status(
                f"⏸ **Задача `{job_id}` ожидает ревью**\n"
                f"Найдено кандидатов: {len(job_orm.candidates) if job_orm.candidates else 0}\n"
                f"Затрачено: ${cost:.3f}\n"
                f"Перейдите в веб-интерфейс для выбора клипов."
            )
            break
        elif status == "completed":
            await update_status(
                f"✅ **Задача `{job_id}` успешно завершена!**\n"
                f"Итоговые затраты: ${cost:.3f}\n"
                f"📦 Отправляю готовые клипы..."
            )

            # Send rendered clips
            async with db.session_factory() as session:
                clips = await get_clips_for_job(session, job_id)

            if not clips:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"ℹ️ По задаче `{job_id}` не было сформировано клипов.",
                    parse_mode="Markdown",
                )
                break

            await bot.send_message(
                chat_id=chat_id,
                text=f"🎬 **Найдено и смонтировано клипов: {len(clips)}**",
                parse_mode="Markdown",
            )

            for c in clips:
                clip_path = Path(c.path)
                manifest = c.manifest or {}
                title = manifest.get("title") or "Клип"
                score = manifest.get("score") or "?"
                dur = c.duration_s

                if not clip_path.exists():
                    continue

                size_bytes = clip_path.stat().st_size
                size_mb = size_bytes / (1024 * 1024)

                # Telegram Bot API limit: 50 MB
                if size_mb < 50.0:
                    try:
                        await bot.send_video(
                            chat_id=chat_id,
                            video=FSInputFile(clip_path),
                            caption=(
                                f"🔥 **{title}**\n"
                                f"⭐ Оценка ИИ: {score}/10\n"
                                f"⏱ Длительность: {dur:.1f}с\n"
                                f"📐 Макет: {c.layout}"
                            ),
                            parse_mode="Markdown",
                        )
                    except Exception as exc:
                        log.error("Failed to send video file %s: %s", clip_path, exc)
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"🎬 **{title}**\nФайл сохранён на сервере: `{clip_path}`",
                            parse_mode="Markdown",
                        )
                else:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🎬 **{title}** ({size_mb:.1f} MB)\n"
                            f"⚠️ Размер файла превышает лимит Telegram Bot API (50 МБ).\n"
                            f"Файл доступен локально: `{clip_path}`"
                        ),
                        parse_mode="Markdown",
                    )
            break
        elif status in ("failed", "cancelled"):
            err_msg = job_orm.error or "Неизвестная ошибка"
            await update_status(f"❌ **Задача `{job_id}` {status}!**\nОшибка: {err_msg}")
            break


def create_bot_app(
    settings: Settings | None = None,
    token: str | None = None,
    db: Database | None = None,
    task_mgr: JobTaskManager | None = None,
) -> tuple[Bot, Dispatcher]:
    """Creates configured Bot and Dispatcher instances."""
    app_settings = settings or get_settings()
    if token:
        app_settings.telegram_token = token  # type: ignore[assignment]

    actual_db = db or get_db_manager()
    actual_task_mgr = task_mgr or default_task_manager

    router, bot = create_bot_router(app_settings, actual_db, actual_task_mgr)
    dp = Dispatcher()
    dp.include_router(router)

    if bot is None:
        raise ValueError("TELEGRAM_TOKEN is not configured in settings or environment")

    return bot, dp


async def run_bot(token: str | None = None) -> None:
    """Entrypoint to launch the Telegram bot polling worker."""
    settings = get_settings()
    bot, dp = create_bot_app(settings=settings, token=token)
    log.info("Starting ClipHunter Telegram Bot...")
    await dp.start_polling(bot)
