"""Reusable user/admin guards for aiogram handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access_control import UserRole, now_ts
from storage_users import UsersStorage

DENIED_MEMBER_TEXT = "Обратитесь к администратору @deeear_polly"


def resolve_role(user_id: int | None, admin_ids: set[int], users_storage: UsersStorage) -> UserRole:
    if user_id is None:
        return UserRole.UNKNOWN
    if user_id in admin_ids:
        return UserRole.ADMIN
    row = users_storage.get_user(user_id)
    if row is None:
        return UserRole.UNKNOWN
    if int(row["is_banned"]) == 1:
        return UserRole.BANNED
    expires_at = row["expires_at"]
    if expires_at is not None and int(expires_at) <= now_ts():
        return UserRole.EXPIRED
    return UserRole.MEMBER


def require_admin(handler: Callable[..., Awaitable[None]]):
    """Decorator that allows only ADMIN users."""

    @wraps(handler)
    async def wrapper(event, *args, **kwargs):
        admin_ids: set[int] = kwargs["admin_ids"]
        users_storage: UsersStorage = kwargs["users_storage"]
        state: FSMContext | None = kwargs.get("state")
        from_user = getattr(event, "from_user", None)
        role = resolve_role(from_user.id if from_user else None, admin_ids, users_storage)
        if role is not UserRole.ADMIN:
            if state:
                await state.clear()
            if isinstance(event, Message):
                await event.answer("Недостаточно прав")
            else:
                await event.answer("Недостаточно прав", show_alert=True)
            return
        return await handler(event, *args, **kwargs)

    return wrapper


def require_member(handler: Callable[..., Awaitable[None]]):
    """Decorator that allows MEMBER and ADMIN users only."""

    @wraps(handler)
    async def wrapper(event, *args, **kwargs):
        admin_ids: set[int] = kwargs["admin_ids"]
        users_storage: UsersStorage = kwargs["users_storage"]
        state: FSMContext | None = kwargs.get("state")
        from_user = getattr(event, "from_user", None)
        role = resolve_role(from_user.id if from_user else None, admin_ids, users_storage)
        if role in {UserRole.BANNED, UserRole.EXPIRED, UserRole.UNKNOWN}:
            if state:
                await state.clear()
            if isinstance(event, Message):
                await event.answer(DENIED_MEMBER_TEXT)
            elif isinstance(event, CallbackQuery):
                await event.answer(DENIED_MEMBER_TEXT, show_alert=True)
            return
        return await handler(event, *args, **kwargs)

    return wrapper
