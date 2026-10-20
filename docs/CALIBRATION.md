# CALIBRATION

> **Статус: измерений пока нет.** Калибровочный прогон не выполнялся (нет стрима и эталонных моментов, платные вызовы запрещены). Таблицы ниже заполнит `python scripts/calibrate.py --config calibration/calibration.yaml --live`.

## 0. Сверка документации

Сверено с ai.google.dev 2026-10-20 (страницы video-understanding, media-resolution, thinking,
structured-output, tokens, pricing). Это сверка документации, не измерение.

| Пункт плана | Что в документации |
| --- | --- |
| Режим API и SDK | Interactions API: `client.interactions.create(model, input, ...)`, SDK `google-genai` 2.31.0. Ответ: `interaction.output_text`. |
| Идентификатор модели | `gemini-3.8-flash` присутствует в документации (владелец подтвердил). |
| Видео-часть запроса | `{"type":"video","uri":...,"mime_type":...,"processing":...,"resolution":...}`; текст-промпт ставится после видео. |
| YouTube по ссылке | Видео-часть только с `uri` ссылки. |
| Offsets | `processing: {"type":"static","start_offset":сек,"end_offset":сек}`. Работают только в static-режиме. |
| fps | `processing: {"type":"static","fps":0.5}`. Только static. По умолчанию 1 кадр/с. |
| Агентный режим | `processing: "agentic"`; поддерживается 3.8 Flash. Модель сама подгружает **транскрипт**, кадры и аудио. Расход считается как thought и tool-use токены. По плану выключен (`AGENTIC_MODE=false`). |
| media_resolution | Уровни: `unspecified`, `low`, `medium`, `high`; задаётся на каждую часть полем `resolution` (только Gemini 3). |
| Токены видео (static) | Кадр: 66 ток. при `low`, иначе 258; аудио 32 ток./с. Итого около 100 ток./с (low) и около 300 ток./с (high). |
| Контекст | 1M токенов: до 3 часов при low, до 1 часа при high. |
| Мышление | `generation_config: {"thinking_level": ...}`. Для 3.8 Flash: low, medium, high; по умолчанию medium. |
| Структурированный ответ | `response_format: {"type":"text","mime_type":"application/json","schema": <JSON schema>}`. |
| Учёт токенов | `interaction.usage`: `total_input_tokens`, `total_output_tokens`, `total_thought_tokens`, `total_tool_use_tokens`, `total_cached_tokens`, `total_tokens`. |
| Цены, Standard, за 1M | Вход 0.75 USD, выход (включая мышление) 3.75 USD до 2026-12-31. С 2027-01-01: 1.50 и 7.50. Batch и Flex вдвое дешевле. |
| Files API | Загрузка `client.files.upload(file=...)`, опрос `files.get`, пока состояние `PROCESSING`. |

**Не сверено (отметка «сверить» осталась):** допустимые значения safety settings, лимиты RPM/TPM
для вашего тарифа, срок хранения файлов в Files API, максимум видео в одном запросе
(`JUDGE_BATCH` ограничен 10 по плану), лимит Bot API Telegram.

Следствие для плана: на разрешении `high` кусок 45 минут даёт около 810k токенов (81% контекста),
что выше порога 60% из S2. Для прохода 1 это неважно (используется `low`, около 270k токенов).

## 1-7. Измерения

1. Токены и стоимость url-mode с offsets и без, окно 5 минут: **не измерено**
2. Относительно чего возвращаются таймкоды в url-mode: **не измерено**
3. Токены file-mode на куске 10 минут при low и обычном разрешении: **не измерено**
4. Качество low и обычного разрешения на известных моментах: **не измерено**
5. Статический и агентный режимы на одном куске: **не измерено**
6. Влияние уровня мышления на качество и цену: **не измерено**
7. Точность таймкодов (средняя ошибка в секундах): **не измерено**

## Решения

| Параметр | Значение по итогам калибровки |
| --- | --- |
| `URL_OFFSETS_OK` | не определено (в `.env.example` значение по умолчанию) |
| `URL_TS_RELATIVE_TO_WINDOW` | не определено (в `.env.example` значение по умолчанию) |

Решение «включать ли url-mode с offsets»: **не принято**, нет измерений. До калибровки используется file-mode (`URL_OFFSETS_OK=false`).
