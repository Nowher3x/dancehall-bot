"""Admin FSM flows for users management."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from math import ceil

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

from access_control import now_ts
from storage_users import LIST_PAGE_SIZE, SECONDS_IN_DAY, UsersStorage

ADMIN_MENU_BTN = "👥 Управление пользователями"


class AdminUserStates(StatesGroup):
    add_target = State()
    add_days = State()
    edit_target = State()
    edit_days = State()
    ban_target = State()
    delete_target = State()


def admin_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить"), KeyboardButton(text="✏️ Изменить срок")],
            [KeyboardButton(text="🚫 Бан / Разбан"), KeyboardButton(text="❌ Удалить")],
            [KeyboardButton(text="📋 Список активных"), KeyboardButton(text="⏳ Истекают скоро (<=7 дней)")],
            [KeyboardButton(text="🏠 В меню")],
        ],
        resize_keyboard=True,
    )


def duration_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(days), callback_data=f"{prefix}:days:{days}") for days in (7, 30, 90, 365)],
            [InlineKeyboardButton(text="Ввести вручную", callback_data=f"{prefix}:manual")],
        ]
    )


def _fmt_exp(value: int | None) -> str:
    if value is None:
        return "Бессрочно"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _target_from_message(message: Message):
    if message.forward_from:
        user = message.forward_from
        return user.id, user.username, user.full_name
    origin = getattr(message, "forward_origin", None)
    sender = getattr(origin, "sender_user", None)
    if sender:
        return sender.id, sender.username, sender.full_name
    text = (message.text or "").strip()
    if text.isdigit():
        return int(text), None, None
    return None


def _card(data: dict) -> str:
    return f"Имя: {data.get('full_name') or '—'}\n@username: @{data.get('username') or '—'}\nID: {data['telegram_id']}"


def build_router(main_menu_builder):
    router = Router(name="admin_users")

    async def resolve_target(message: Message):
        parsed = _target_from_message(message)
        if parsed:
            return parsed
        text = (message.text or "").strip()
        if text.startswith("@"):
            try:
                chat = await message.bot.get_chat(text)
            except TelegramBadRequest:
                return None
            return chat.id, chat.username, chat.full_name
        return None

    @router.message(F.text == ADMIN_MENU_BTN)
    async def open_admin_menu(message: Message, state: FSMContext, admin_ids: set[int], **kwargs):
        if message.from_user is None or message.from_user.id not in admin_ids:
            await message.answer("Недостаточно прав")
            return
        await state.clear()
        await message.answer("Управление пользователями", reply_markup=admin_menu_kb())

    @router.message(F.text == "➕ Добавить")
    async def add_user_start(message: Message, state: FSMContext, admin_ids: set[int], **kwargs):
        if message.from_user is None or message.from_user.id not in admin_ids:
            await message.answer("Недостаточно прав")
            return
        await state.set_state(AdminUserStates.add_target)
        await message.answer("Отправьте @username или перешлите сообщение пользователя")

    @router.message(AdminUserStates.add_target)
    async def add_user_target(message: Message, state: FSMContext, users_storage: UsersStorage, **kwargs):
        resolved = await resolve_target(message)
        if not resolved:
            await message.answer("Не удалось определить пользователя. Перешлите сообщение пользователя.")
            return
        telegram_id, username, full_name = resolved
        data = {"telegram_id": telegram_id, "username": username, "full_name": full_name}
        await state.update_data(target=data)
        await state.set_state(AdminUserStates.add_days)
        await message.answer(_card(data), reply_markup=duration_kb("add"))

    @router.callback_query(F.data.startswith("add:days:"), StateFilter(AdminUserStates.add_days))
    async def add_user_days(callback: CallbackQuery, state: FSMContext, users_storage: UsersStorage, **kwargs):
        days = int(callback.data.split(":")[-1])
        data = await state.get_data()
        target = data["target"]
        expires_at = now_ts() + days * SECONDS_IN_DAY
        users_storage.upsert_user(target["telegram_id"], target.get("username"), target.get("full_name"), expires_at, is_banned=0)
        logging.info("admin_action add_user admin_id=%s target_id=%s days=%s", callback.from_user.id if callback.from_user else None, target["telegram_id"], days)
        await state.clear()
        await callback.message.answer(f"Доступ активен до {_fmt_exp(expires_at)}", reply_markup=main_menu_builder(True))
        await callback.answer()

    @router.callback_query(F.data == "add:manual", StateFilter(AdminUserStates.add_days))
    async def add_user_manual(callback: CallbackQuery):
        await callback.message.answer("Введите срок в днях (число)")
        await callback.answer()

    @router.message(AdminUserStates.add_days)
    async def add_user_days_manual(message: Message, state: FSMContext, users_storage: UsersStorage, **kwargs):
        if not (message.text or "").strip().isdigit():
            await message.answer("Введите корректное число дней")
            return
        days = int(message.text.strip())
        data = await state.get_data()
        target = data["target"]
        expires_at = now_ts() + days * SECONDS_IN_DAY
        users_storage.upsert_user(target["telegram_id"], target.get("username"), target.get("full_name"), expires_at, is_banned=0)
        logging.info("admin_action add_user admin_id=%s target_id=%s days=%s", message.from_user.id if message.from_user else None, target["telegram_id"], days)
        await state.clear()
        await message.answer(f"Доступ активен до {_fmt_exp(expires_at)}", reply_markup=main_menu_builder(True))

    async def select_target(message: Message, state: FSMContext, next_state: State):
        resolved = await resolve_target(message)
        if not resolved:
            await message.answer("Не удалось определить пользователя. Перешлите сообщение пользователя.")
            return None
        telegram_id, username, full_name = resolved
        await state.update_data(target={"telegram_id": telegram_id, "username": username, "full_name": full_name})
        await state.set_state(next_state)
        return telegram_id

    @router.message(F.text == "✏️ Изменить срок")
    async def edit_start(message: Message, state: FSMContext, admin_ids: set[int], **kwargs):
        if message.from_user is None or message.from_user.id not in admin_ids:
            await message.answer("Недостаточно прав")
            return
        await state.set_state(AdminUserStates.edit_target)
        await message.answer("Отправьте @username или перешлите сообщение пользователя")

    @router.message(AdminUserStates.edit_target)
    async def edit_target(message: Message, state: FSMContext, users_storage: UsersStorage, **kwargs):
        user_id = await select_target(message, state, AdminUserStates.edit_days)
        if not user_id:
            return
        row = users_storage.get_user(user_id)
        current = _fmt_exp(int(row["expires_at"])) if row and row["expires_at"] is not None else "Бессрочно / отсутствует"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"Продлить на {d}", callback_data=f"edit:plus:{d}") for d in (7,30,90,365)], [InlineKeyboardButton(text="Установить новый срок", callback_data="edit:set")], [InlineKeyboardButton(text="Сделать бессрочно", callback_data="edit:forever")]])
        await message.answer(f"Текущий срок: {current}", reply_markup=kb)

    @router.callback_query(F.data.startswith("edit:plus:"), StateFilter(AdminUserStates.edit_days))
    async def edit_plus(callback: CallbackQuery, state: FSMContext, users_storage: UsersStorage, admin_ids: set[int], **kwargs):
        days = int(callback.data.split(":")[-1])
        target = (await state.get_data())["target"]
        if target["telegram_id"] in admin_ids:
            await callback.answer("Нельзя изменить ADMIN", show_alert=True)
            return
        row = users_storage.get_user(target["telegram_id"])
        base = now_ts() if row is None or row["expires_at"] is None or int(row["expires_at"]) < now_ts() else int(row["expires_at"])
        new_exp = base + days * SECONDS_IN_DAY
        if row:
            users_storage.update_expiration(target["telegram_id"], new_exp)
        else:
            users_storage.upsert_user(target["telegram_id"], target.get("username"), target.get("full_name"), new_exp)
        logging.info("admin_action extend_user admin_id=%s target_id=%s days=%s", callback.from_user.id if callback.from_user else None, target["telegram_id"], days)
        await state.clear()
        await callback.message.answer(f"Новый срок: {_fmt_exp(new_exp)}", reply_markup=main_menu_builder(True))
        await callback.answer()

    @router.callback_query(F.data == "edit:set", StateFilter(AdminUserStates.edit_days))
    async def edit_set(callback: CallbackQuery):
        await callback.message.answer("Введите новый срок в днях (число)")
        await callback.answer()

    @router.message(AdminUserStates.edit_days)
    async def edit_set_days(message: Message, state: FSMContext, users_storage: UsersStorage, admin_ids: set[int], **kwargs):
        if not (message.text or "").strip().isdigit():
            return
        target = (await state.get_data())["target"]
        if target["telegram_id"] in admin_ids:
            await message.answer("Нельзя изменить ADMIN")
            return
        days = int(message.text.strip())
        exp = now_ts() + days * SECONDS_IN_DAY
        row = users_storage.get_user(target["telegram_id"])
        if row:
            users_storage.update_expiration(target["telegram_id"], exp)
        else:
            users_storage.upsert_user(target["telegram_id"], target.get("username"), target.get("full_name"), exp)
        logging.info("admin_action set_expiration admin_id=%s target_id=%s days=%s", message.from_user.id if message.from_user else None, target["telegram_id"], days)
        await state.clear()
        await message.answer(f"Новый срок: {_fmt_exp(exp)}", reply_markup=main_menu_builder(True))

    @router.callback_query(F.data == "edit:forever", StateFilter(AdminUserStates.edit_days))
    async def edit_forever(callback: CallbackQuery, state: FSMContext, users_storage: UsersStorage, admin_ids: set[int], **kwargs):
        target = (await state.get_data())["target"]
        if target["telegram_id"] in admin_ids:
            await callback.answer("Нельзя изменить ADMIN", show_alert=True)
            return
        row = users_storage.get_user(target["telegram_id"])
        if row:
            users_storage.update_expiration(target["telegram_id"], None)
        else:
            users_storage.upsert_user(target["telegram_id"], target.get("username"), target.get("full_name"), None)
        logging.info("admin_action set_forever admin_id=%s target_id=%s", callback.from_user.id if callback.from_user else None, target["telegram_id"])
        await state.clear()
        await callback.message.answer("Срок: бессрочно", reply_markup=main_menu_builder(True))
        await callback.answer()

    @router.message(F.text == "🚫 Бан / Разбан")
    async def ban_start(message: Message, state: FSMContext, admin_ids: set[int], **kwargs):
        if message.from_user is None or message.from_user.id not in admin_ids:
            await message.answer("Недостаточно прав")
            return
        await state.set_state(AdminUserStates.ban_target)
        await message.answer("Отправьте @username или перешлите сообщение пользователя")

    @router.message(AdminUserStates.ban_target)
    async def ban_toggle(message: Message, state: FSMContext, users_storage: UsersStorage, admin_ids: set[int], **kwargs):
        resolved = await resolve_target(message)
        if not resolved:
            await message.answer("Не удалось определить пользователя")
            return
        telegram_id, username, full_name = resolved
        if telegram_id in admin_ids or (message.from_user and telegram_id == message.from_user.id):
            await message.answer("Этого пользователя нельзя банить")
            return
        row = users_storage.get_user(telegram_id)
        is_banned = not bool(row and int(row["is_banned"]) == 1)
        if row:
            users_storage.set_ban(telegram_id, is_banned)
        else:
            users_storage.upsert_user(telegram_id, username, full_name, None, is_banned=1 if is_banned else 0)
        logging.info("admin_action toggle_ban admin_id=%s target_id=%s is_banned=%s", message.from_user.id if message.from_user else None, telegram_id, is_banned)
        await state.clear()
        await message.answer("Пользователь забанен" if is_banned else "Пользователь разбанен", reply_markup=main_menu_builder(True))

    @router.message(F.text == "❌ Удалить")
    async def delete_start(message: Message, state: FSMContext, admin_ids: set[int], **kwargs):
        if message.from_user is None or message.from_user.id not in admin_ids:
            await message.answer("Недостаточно прав")
            return
        await state.set_state(AdminUserStates.delete_target)
        await message.answer("Отправьте @username или перешлите сообщение пользователя")

    @router.message(AdminUserStates.delete_target)
    async def delete_user(message: Message, state: FSMContext, users_storage: UsersStorage, admin_ids: set[int], **kwargs):
        resolved = await resolve_target(message)
        if not resolved:
            await message.answer("Не удалось определить пользователя")
            return
        telegram_id, _, _ = resolved
        if telegram_id in admin_ids or (message.from_user and telegram_id == message.from_user.id):
            await message.answer("Этого пользователя нельзя удалить")
            return
        users_storage.delete_user(telegram_id)
        logging.info("admin_action delete_user admin_id=%s target_id=%s", message.from_user.id if message.from_user else None, telegram_id)
        await state.clear()
        await message.answer("Пользователь удален", reply_markup=main_menu_builder(True))

    def list_kb(prefix: str, page: int, pages: int) -> InlineKeyboardMarkup:
        rows = []
        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:page:{page-1}"))
        if page < pages - 1:
            buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:page:{page+1}"))
        if buttons:
            rows.append(buttons)
        return InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(text="·", callback_data="noop")]])

    async def render_list(message: Message, users_storage: UsersStorage, expiring: bool, page: int) -> None:
        now = now_ts()
        if expiring:
            until = now + 7 * SECONDS_IN_DAY
            total = users_storage.count_expiring(now, until)
            rows = users_storage.list_expiring(now, until, page * LIST_PAGE_SIZE)
            prefix = "expiring"
            title = "Истекают скоро"
        else:
            total = users_storage.count_active(now)
            rows = users_storage.list_active(now, page * LIST_PAGE_SIZE)
            prefix = "active"
            title = "Активные"
        pages = ceil(total / LIST_PAGE_SIZE) if total else 1
        text_rows = [f"{title} (стр. {page+1}/{pages})"]
        for row in rows:
            text_rows.append(f"{row['full_name'] or '—'} (@{row['username'] or '—'})\nID: {row['telegram_id']}\nДо: {_fmt_exp(row['expires_at'])}")
        if len(rows) == 0:
            text_rows.append("Список пуст")
        await message.answer("\n\n".join(text_rows), reply_markup=list_kb(prefix, page, pages))

    @router.message(F.text == "📋 Список активных")
    async def list_active(message: Message, users_storage: UsersStorage, admin_ids: set[int], **kwargs):
        if message.from_user is None or message.from_user.id not in admin_ids:
            await message.answer("Недостаточно прав")
            return
        await render_list(message, users_storage, expiring=False, page=0)

    @router.message(F.text == "⏳ Истекают скоро (<=7 дней)")
    async def list_expiring(message: Message, users_storage: UsersStorage, admin_ids: set[int], **kwargs):
        if message.from_user is None or message.from_user.id not in admin_ids:
            await message.answer("Недостаточно прав")
            return
        await render_list(message, users_storage, expiring=True, page=0)

    @router.callback_query(F.data.startswith("active:page:"))
    async def paginate_active(callback: CallbackQuery, users_storage: UsersStorage, admin_ids: set[int], **kwargs):
        if callback.from_user is None or callback.from_user.id not in admin_ids:
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        page = int(callback.data.split(":")[-1])
        await render_list(callback.message, users_storage, expiring=False, page=page)
        await callback.answer()

    @router.callback_query(F.data.startswith("expiring:page:"))
    async def paginate_expiring(callback: CallbackQuery, users_storage: UsersStorage, admin_ids: set[int], **kwargs):
        if callback.from_user is None or callback.from_user.id not in admin_ids:
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        page = int(callback.data.split(":")[-1])
        await render_list(callback.message, users_storage, expiring=True, page=page)
        await callback.answer()

    @router.callback_query(F.data == "noop")
    async def noop(callback: CallbackQuery):
        await callback.answer()

    return router
