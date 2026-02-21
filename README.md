# Dancehall Telegram Bot

Telegram-бот для каталога dancehall-видео на **aiogram v3** + SQLite.

## Что добавлено

- Верификация пользователя по `user_id` через ENV (`ALLOWED_USER_ID` или `ALLOWED_USER_IDS`).
- Vault-канал (склад-канал) для хранения «актуальной» копии видео в Telegram.
- Идемпотентная миграция SQLite для полей:
  - `storage_chat_id INTEGER`
  - `storage_message_id INTEGER`
  - `needs_refresh INTEGER NOT NULL DEFAULT 0`
- Обновление `file_id` из постов в vault-канале (`channel_post` с видео).
- Обработка «битого» `file_id` при скачивании с пометкой `needs_refresh=1`.

## ENV-конфиг

Создайте `.env`:

```env
BOT_TOKEN=ваш_токен_бота

# ID одного пользователя (рекомендуется)
ALLOWED_USER_ID=123456789

# или список пользователей через запятую
# ALLOWED_USER_IDS=123456789,987654321

# ID склад-канала (отрицательный, обычно начинается с -100...)
STORAGE_CHAT_ID=-1001234567890
```

## Быстрый старт

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Как работает Vault

1. Пользователь присылает видео в обычном чате с ботом (через сценарий добавления).
2. Бот делает upsert записи в `videos` по `file_unique_id`.
3. Бот копирует исходное сообщение с видео в `STORAGE_CHAT_ID` через `copy_message`.
4. В `videos` сохраняются `storage_chat_id` и `storage_message_id`.
5. Когда в vault-канале появляется `channel_post` с видео, бот обновляет `videos.file_id`:
   - сначала по `storage_message_id`,
   - если не найдено — fallback по `file_unique_id`.

## Инструкция: приватный канал + STORAGE_CHAT_ID

1. В Telegram нажмите **New Channel**.
2. Создайте канал, выберите **Private Channel**.
3. Добавьте бота в канал:
   - Откройте канал → **Administrators** → **Add admin**.
   - Выберите вашего бота.
   - Дайте права на публикацию сообщений.
4. Получите `STORAGE_CHAT_ID`:
   - Временно отправьте пост в канал.
   - Запустите бота с включёнными логами (`INFO`) и посмотрите `message.chat.id` через отладку/логирование update, либо
   - используйте сторонний бот/утилиту для просмотра `chat_id` канала.
5. Вставьте ID в `.env` как `STORAGE_CHAT_ID=-100...`.

## Инструкция: user id одного пользователя

1. Напишите боту `/start`.
2. Узнайте ваш `user_id` (через @userinfobot или временный лог в коде).
3. Добавьте в `.env`:

```env
ALLOWED_USER_ID=ваш_user_id
```

Если указан `ALLOWED_USER_ID`/`ALLOWED_USER_IDS`, остальные пользователи получат отказ в доступе.

## Структура

- `bot.py` — aiogram v3 хендлеры и FSM.
- `storage.py` — SQLite слой, миграции, CRUD.
- `dancehall.db` — база.

## Совместимость

Текущая реализация использует **aiogram v3** (`Dispatcher`, `F`, `@dp.channel_post(...)`).
