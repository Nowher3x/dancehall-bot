import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv

BACK = "⬅️ Назад"
CANCEL = "❌ Отмена"
MENU = "🏠 В меню"

class AddVideoStates(StatesGroup):
    wait_video = State()
    wait_title = State()
    wait_categories = State()
    confirm = State()


class SearchStates(StatesGroup):
    choose_filter = State()
    wait_query = State()


class EditStates(StatesGroup):
    wait_video = State()


class DeleteStates(StatesGroup):
    wait_video = State()


from storage import CATEGORY_OPTIONS, Storage, normalize_url

def edit_actions_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Название", callback_data="edit:title")],
            [InlineKeyboardButton(text="📁 Категории", callback_data="edit:categories")],
            [InlineKeyboardButton(text="🎬 Видео", callback_data="edit:video")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data="edit:delete")],
        ]
    )


def search_category_kb() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=category)] for category in CATEGORY_OPTIONS]
    rows.append([KeyboardButton(text=BACK), KeyboardButton(text=CANCEL), KeyboardButton(text=MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def category_choice_kb(selected: list[str] | None = None) -> ReplyKeyboardMarkup:
    selected_set = set(selected or [])
    rows = []
    for category in CATEGORY_OPTIONS:
        mark = "✅ " if category in selected_set else "▫️ "
        rows.append([KeyboardButton(text=f"{mark}{category}")])
    rows.extend(
        [
            [KeyboardButton(text="Готово")],
            [KeyboardButton(text=BACK), KeyboardButton(text=CANCEL), KeyboardButton(text=MENU)],
        ]
    )
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def main_menu_kb(can_edit: bool) -> ReplyKeyboardMarkup:
    first_row = [KeyboardButton(text="🔎 Поиск")]
    second_row = [KeyboardButton(text="⭐ Избранное"), KeyboardButton(text="📋 Список")]
    if can_edit:
        first_row.insert(0, KeyboardButton(text="➕ Добавить видео"))
        second_row.insert(1, KeyboardButton(text="✏️ Редактировать"))
        second_row.append(KeyboardButton(text="🗑 Удалить"))
    return ReplyKeyboardMarkup(
        keyboard=[first_row, second_row],
        resize_keyboard=True,
    )


def nav_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BACK), KeyboardButton(text=CANCEL), KeyboardButton(text=MENU)],
        ],
        resize_keyboard=True,
    )


def pagination_kb(prefix: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:page:{page-1}"))
    start = max(0, page - 1)
    end = min(total_pages, page + 2)
    for p in range(start, end):
        buttons.append(
            InlineKeyboardButton(
                text=f"[{p+1}]" if p == page else str(p + 1),
                callback_data=f"{prefix}:page:{p}",
            )
        )
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:page:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons] if buttons else [[]])


def video_card_text(storage: Storage, row) -> str:
    categories = ", ".join(storage.video_categories(row["id"])) or "—"
    return f"🔥 {row['title']}\nКатегории: {categories}"


def video_actions_kb(video_id: int, is_favorite: bool, can_edit: bool) -> InlineKeyboardMarkup:
    fav = "💔 Убрать из избранного" if is_favorite else "⭐ Добавить в избранное"
    rows = [
        [InlineKeyboardButton(text="▶️ Смотреть", callback_data=f"video:view:{video_id}")],
        [InlineKeyboardButton(text=fav, callback_data=f"video:fav:{video_id}")],
    ]
    if can_edit:
        rows.extend(
            [
                [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"video:edit:{video_id}")],
                [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"video:delete:{video_id}")],
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


load_dotenv()
STORAGE_CHAT_ID = int(os.getenv("STORAGE_CHAT_ID", "0"))
ALLOWED_USER_IDS = {
    int(v.strip())
    for v in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if v.strip().isdigit()
}
if os.getenv("ALLOWED_USER_ID", "").strip().isdigit():
    ALLOWED_USER_IDS.add(int(os.getenv("ALLOWED_USER_ID")))

storage = Storage()
storage.ensure_taxonomy()
dp = Dispatcher(storage=MemoryStorage())


async def ensure_user_allowed(message: Message, state: FSMContext | None = None) -> bool:
    _ = (message, state)
    return True


def can_manage_content(user_id: int | None) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS if user_id is not None else False


async def ensure_manage_access(message: Message, state: FSMContext | None = None) -> bool:
    user_id = message.from_user.id if message.from_user else None
    if can_manage_content(user_id):
        return True
    if state:
        await state.clear()
    await message.answer("⛔️ Только чтение: редактирование доступно только пользователям из ALLOWED_USER_ID(S).")
    return False




async def ensure_manage_callback_access(callback: CallbackQuery, state: FSMContext | None = None) -> bool:
    user_id = callback.from_user.id if callback.from_user else None
    if can_manage_content(user_id):
        return True
    if state:
        await state.clear()
    await callback.answer("Только чтение", show_alert=True)
    return False


async def copy_video_to_vault(bot: Bot, from_chat_id: int, message_id: int) -> tuple[int, int] | None:
    if not STORAGE_CHAT_ID:
        return None
    copied = await bot.copy_message(
        chat_id=STORAGE_CHAT_ID,
        from_chat_id=from_chat_id,
        message_id=message_id,
    )
    return STORAGE_CHAT_ID, copied.message_id


async def go_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Главное меню", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    if not await ensure_user_allowed(message, state):
        return
    await go_menu(message, state)


@dp.message(F.text == MENU)
async def menu_btn(message: Message, state: FSMContext) -> None:
    if not await ensure_user_allowed(message, state):
        return
    await go_menu(message, state)


@dp.message(F.text == "➕ Добавить видео")
async def add_video_start(message: Message, state: FSMContext) -> None:
    if not await ensure_manage_access(message, state):
        return
    await state.set_state(AddVideoStates.wait_video)
    await message.answer("Пришлите видеофайл Telegram или URL.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_video, F.text == BACK)
async def add_video_back_from_video(message: Message, state: FSMContext) -> None:
    await go_menu(message, state)


@dp.message(AddVideoStates.wait_video, F.text == CANCEL)
async def add_video_cancel_video(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))


@dp.message(AddVideoStates.wait_video)
async def add_video_video(message: Message, state: FSMContext) -> None:
    if not await ensure_user_allowed(message, state):
        return
    file_id = file_unique_id = source_url = None
    storage_chat_id = storage_message_id = None
    if message.video:
        file_id = message.video.file_id
        file_unique_id = message.video.file_unique_id

        if STORAGE_CHAT_ID:
            try:
                copied_meta = await copy_video_to_vault(message.bot, message.chat.id, message.message_id)
                if copied_meta:
                    storage_chat_id, storage_message_id = copied_meta
            except Exception as exc:
                logging.exception("Failed to copy source video to storage chat: %s", exc)
                await message.answer("⚠️ Не удалось скопировать видео в vault-канал. Проверьте права бота в канале.")

        existing = storage.find_video_by_file_uid(file_unique_id)
        if existing:
            if storage_chat_id and storage_message_id:
                storage.save_storage_message(existing["id"], storage_chat_id, storage_message_id)
            await message.answer("Такое видео уже есть, запись обновлена в vault.")
            await send_video_card(message, existing, message.from_user.id)
            await go_menu(message, state)
            return
    elif message.text and re.match(r"https?://", message.text.strip()):
        source_url = message.text.strip()
        existing = storage.find_video_by_url(normalize_url(source_url))
        if existing:
            await message.answer("Видео по этой ссылке уже есть, дубликат не создан.")
            await send_video_card(message, existing, message.from_user.id)
            await go_menu(message, state)
            return
    else:
        await message.answer("Нужно отправить видеофайл или URL. Попробуйте снова.")
        return

    await state.update_data(
        file_id=file_id,
        file_unique_id=file_unique_id,
        source_url=source_url,
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
        storage_chat_id=storage_chat_id,
        storage_message_id=storage_message_id,
    )
    await state.set_state(AddVideoStates.wait_title)
    await message.answer("Введите название видео.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_title, F.text == BACK)
async def add_video_title_back(message: Message, state: FSMContext) -> None:
    await state.set_state(AddVideoStates.wait_video)
    await message.answer("Шаг назад. Пришлите видеофайл или URL.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_title, F.text == CANCEL)
async def add_video_title_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))


@dp.message(AddVideoStates.wait_title)
async def add_video_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("Пустое название нельзя. Введите название.")
        return
    if len(title) > 120:
        await message.answer("Название слишком длинное (максимум 120 символов).")
        return

    existing = storage.find_video_by_title(title)
    await state.update_data(title=title)
    if existing:
        await state.update_data(duplicate_video_id=existing["id"])
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔁 Заменить существующее", callback_data="add:dup:replace")],
                [InlineKeyboardButton(text="🆕 Создать копию", callback_data="add:dup:copy")],
                [InlineKeyboardButton(text="✏️ Изменить название", callback_data="add:dup:rename")],
            ]
        )
        await message.answer("Название уже существует. Выберите действие:", reply_markup=kb)
        return

    await state.set_state(AddVideoStates.wait_categories)
    await state.update_data(categories=[])
    await message.answer("Выберите категории кнопками и нажмите «✅ Готово».", reply_markup=category_choice_kb())


@dp.callback_query(F.data.startswith("add:dup:"))
async def add_duplicate_choice(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_manage_callback_access(callback, state):
        return
    choice = callback.data.split(":")[-1]
    if choice == "rename":
        await callback.message.answer("Введите новое название.")
        await callback.answer()
        return
    await state.update_data(duplicate_choice=choice)
    await state.set_state(AddVideoStates.wait_categories)
    await state.update_data(categories=[])
    await callback.message.answer(
        "Выберите категории кнопками и нажмите «✅ Готово».",
        reply_markup=category_choice_kb(),
    )
    await callback.answer()


@dp.message(AddVideoStates.wait_categories, F.text == BACK)
async def add_categories_back(message: Message, state: FSMContext) -> None:
    await state.set_state(AddVideoStates.wait_title)
    await message.answer("Шаг назад. Введите название.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_categories, F.text == CANCEL)
async def add_categories_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))


@dp.message(AddVideoStates.wait_categories)
async def add_categories(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()

    if text in {"Готово", "✅ Готово"}:
        data = await state.get_data()
        categories = data.get("categories", [])
        if not categories:
            await message.answer("Нужно выбрать хотя бы одну категорию.", reply_markup=category_choice_kb())
            return

        preview = f"Предпросмотр:\n🔥 {data['title']}\nКатегории: {', '.join(categories)}"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Сохранить", callback_data="add:save")]]
        )
        await state.set_state(AddVideoStates.confirm)
        await message.answer(preview, reply_markup=kb)
        return

    category = None
    if text.startswith("✅ ") or text.startswith("▫️ "):
        category = text[2:].strip()
    elif text in CATEGORY_OPTIONS:
        category = text

    if category is not None:
        if category not in CATEGORY_OPTIONS:
            await message.answer("Выберите категорию кнопкой из списка.")
            return

        data = await state.get_data()
        current = data.get("categories", [])
        if category in current:
            current = [c for c in current if c != category]
        else:
            current.append(category)

        await state.update_data(categories=current)
        await message.answer(
            f"Выбрано: {', '.join(current) if current else 'ничего'}",
            reply_markup=category_choice_kb(current),
        )
        return

    await message.answer("Используйте кнопки категорий и кнопку «Готово».", reply_markup=category_choice_kb())


@dp.message(AddVideoStates.confirm, F.text == BACK)
async def add_confirm_back(message: Message, state: FSMContext) -> None:
    await state.set_state(AddVideoStates.wait_categories)
    data = await state.get_data()
    await message.answer("Шаг назад. Выберите категории.", reply_markup=category_choice_kb(data.get("categories", [])))


@dp.message(AddVideoStates.confirm, F.text == CANCEL)
async def add_confirm_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))


@dp.callback_query(F.data == "add:save")
async def add_save(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_manage_callback_access(callback, state):
        return
    data = await state.get_data()
    duplicate_choice = data.get("duplicate_choice")
    duplicate_video_id = data.get("duplicate_video_id")
    if duplicate_choice == "replace" and duplicate_video_id:
        storage.replace_video(
            duplicate_video_id,
            data["title"],
            data.get("file_id"),
            data.get("file_unique_id"),
            data.get("source_url"),
            data["categories"],
        )
        row = storage.get_video(duplicate_video_id)
        await callback.message.answer("Видео заменено.")
    else:
        if data.get("file_unique_id"):
            vid = storage.upsert_video_file(
                data["title"],
                data.get("file_id"),
                data.get("file_unique_id"),
                data.get("source_url"),
            )
            storage._set_categories(vid, data["categories"])
            storage.conn.commit()
        else:
            vid = storage.create_video(
                data["title"],
                data.get("file_id"),
                data.get("file_unique_id"),
                data.get("source_url"),
                data["categories"],
            )
        row = storage.get_video(vid)
        await callback.message.answer("Видео сохранено.")

    if data.get("file_unique_id") and data.get("storage_chat_id") and data.get("storage_message_id"):
        storage.save_storage_message(row["id"], data["storage_chat_id"], data["storage_message_id"])

    await state.clear()
    await send_video_card(callback.message, row, callback.from_user.id)
    await callback.message.answer("Готово.", reply_markup=main_menu_kb(can_manage_content(callback.from_user.id if callback.from_user else None)))
    await callback.answer()


@dp.message(F.text == "🔎 Поиск")
async def search_start(message: Message, state: FSMContext) -> None:
    if not await ensure_user_allowed(message, state):
        return
    await state.set_state(SearchStates.choose_filter)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📁 По категории")],
            [KeyboardButton(text="🔤 По названию")],
            [KeyboardButton(text=BACK), KeyboardButton(text=CANCEL), KeyboardButton(text=MENU)],
        ],
        resize_keyboard=True,
    )
    await message.answer("Выберите тип поиска.", reply_markup=kb)


@dp.message(SearchStates.choose_filter, F.text == BACK)
async def search_filter_back(message: Message, state: FSMContext) -> None:
    await go_menu(message, state)


@dp.message(SearchStates.choose_filter, F.text == CANCEL)
async def search_filter_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Поиск отменен.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))


@dp.message(SearchStates.choose_filter)
async def search_choose_filter(message: Message, state: FSMContext) -> None:
    mapping = {"📁 По категории": "category", "🔤 По названию": "title"}
    if message.text not in mapping:
        await message.answer("Выберите вариант кнопкой.")
        return
    await state.update_data(filter_type=mapping[message.text])
    await state.set_state(SearchStates.wait_query)
    if mapping[message.text] == "category":
        await message.answer(
            "Выберите категорию кнопкой.",
            reply_markup=search_category_kb(),
        )
        return
    await message.answer("Введите запрос.", reply_markup=nav_kb())


@dp.message(SearchStates.wait_query, F.text == BACK)
async def search_query_back(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchStates.choose_filter)
    await search_start(message, state)


@dp.message(SearchStates.wait_query, F.text == CANCEL)
async def search_query_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Поиск отменен.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))


async def send_results(message: Message, user_id: int, mode: str, filter_type: str, query: str, page: int) -> None:
    if mode == "favorites":
        rows, total_pages = storage.favorites(user_id, page)
    elif mode == "all":
        rows, total_pages = storage.list_all_videos(page)
    elif mode == "titles":
        rows, total_pages = storage.list_titles(page)
    else:
        rows, total_pages = storage.search(filter_type, query, page)

    if not rows:
        await message.answer("Ничего не найдено.")
        return
    lines = [f"Страница {page+1}/{total_pages}"]
    for r in rows:
        if mode == "titles":
            lines.append(f"• {r['title']}")
        else:
            lines.append(f"• {r['title']} (id={r['id']})")
    encoded_query = query.replace(":", "%3A")
    kb_rows = []
    if mode != "titles":
        kb_rows.extend([[InlineKeyboardButton(text=r["title"], callback_data=f"video:open:{r['id']}")] for r in rows])
    kb_rows.append(pagination_kb(f"list:{mode}:{filter_type}:{encoded_query}", page, total_pages).inline_keyboard[0])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@dp.message(SearchStates.wait_query)
async def search_query(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    query = (message.text or "").strip()
    if data["filter_type"] == "category":
        if query.startswith("✅ ") or query.startswith("▫️ "):
            query = query[2:].strip()
        if query not in CATEGORY_OPTIONS:
            await message.answer("Выберите категорию кнопкой из списка.", reply_markup=search_category_kb())
            return
    elif not query:
        await message.answer("Пустой запрос недопустим.")
        return

    await send_results(message, message.from_user.id, "search", data["filter_type"], query, 0)
    if data["filter_type"] == "category":
        await message.answer("Выберите другую категорию или вернитесь в меню.", reply_markup=search_category_kb())
        return
    await state.clear()
    await message.answer("Выберите видео или вернитесь в меню.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))


@dp.message(F.text == "📋 Список")
async def titles_list_open(message: Message, state: FSMContext) -> None:
    if not await ensure_user_allowed(message, state):
        return
    await state.clear()
    await send_results(message, message.from_user.id, "titles", "all", "all", 0)


@dp.message(F.text == "⭐ Избранное")
async def favorites_open(message: Message, state: FSMContext) -> None:
    if not await ensure_user_allowed(message, state):
        return
    await state.clear()
    await send_results(message, message.from_user.id, "favorites", "favorite", "my", 0)


@dp.message(F.text == "✏️ Редактировать")
async def edit_open(message: Message, state: FSMContext) -> None:
    if not await ensure_manage_access(message, state):
        return
    await state.set_state(EditStates.wait_video)
    await send_results(message, message.from_user.id, "all", "all", "all", 0)
    await message.answer("Выберите видео для редактирования.", reply_markup=nav_kb())


@dp.message(F.text == "🗑 Удалить")
async def delete_open(message: Message, state: FSMContext) -> None:
    if not await ensure_manage_access(message, state):
        return
    await state.set_state(DeleteStates.wait_video)
    await send_results(message, message.from_user.id, "all", "all", "all", 0)
    await message.answer("Выберите видео для удаления.", reply_markup=nav_kb())


@dp.callback_query(F.data.startswith("list:"))
async def paginate(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 6 or parts[-2] != "page":
        await callback.answer("Не удалось открыть страницу", show_alert=True)
        return
    mode = parts[1]
    filter_type = parts[2]
    query = ":".join(parts[3:-2]).replace("%3A", ":")
    page = parts[-1]
    await callback.message.delete()
    await send_results(callback.message, callback.from_user.id, mode, filter_type, query, int(page))
    await callback.answer()


async def send_video_card(target: Message, row, user_id: int) -> None:
    await target.answer(
        video_card_text(storage, row),
        reply_markup=video_actions_kb(row["id"], storage.is_favorite(user_id, row["id"]), can_manage_content(user_id)),
    )


@dp.callback_query(F.data.startswith("video:open:"))
async def video_open(callback: CallbackQuery, state: FSMContext) -> None:
    vid = int(callback.data.split(":")[-1])
    row = storage.get_video(vid)
    if not row:
        await callback.answer("Видео не найдено", show_alert=True)
        return
    if await state.get_state() == EditStates.wait_video.state:
        await state.update_data(edit_video_id=vid)
        await callback.message.answer(video_card_text(storage, row), reply_markup=edit_actions_kb())
        await callback.answer()
        return
    await send_video_card(callback.message, row, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data.startswith("video:fav:"))
async def video_fav(callback: CallbackQuery) -> None:
    vid = int(callback.data.split(":")[-1])
    new_state = storage.toggle_favorite(callback.from_user.id, vid)
    await callback.answer("Добавлено в избранное" if new_state else "Удалено из избранного")
    row = storage.get_video(vid)
    if row:
        await callback.message.edit_reply_markup(
            reply_markup=video_actions_kb(
                vid,
                storage.is_favorite(callback.from_user.id, vid),
                can_manage_content(callback.from_user.id if callback.from_user else None),
            )
        )


@dp.callback_query(F.data.startswith("video:view:"))
async def video_view(callback: CallbackQuery) -> None:
    vid = int(callback.data.split(":")[-1])
    row = storage.get_video(vid)
    if not row:
        await callback.answer("Видео не найдено", show_alert=True)
        return
    if row["file_id"]:
        try:
            await callback.message.answer_video(row["file_id"], caption=row["title"])
        except TelegramBadRequest as exc:
            text = str(exc).lower()
            if "wrong file identifier" in text or "file_id" in text or "invalid" in text:
                storage.mark_needs_refresh(vid)
                logging.warning("Invalid file_id for video_id=%s: %s", vid, exc)
                await callback.message.answer(
                    "⚠️ Видео временно недоступно: Telegram обновил file_id. Нужна переиндексация из vault-канала."
                )
            else:
                raise
    elif row["source_url"]:
        await callback.message.answer(f"Ссылка: {row['source_url']}")
    else:
        await callback.message.answer("Источник не найден.")
    await callback.answer()


@dp.callback_query(F.data.startswith("video:edit:"))
async def video_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_manage_callback_access(callback, state):
        return
    vid = int(callback.data.split(":")[-1])
    row = storage.get_video(vid)
    if not row:
        await callback.answer("Видео не найдено", show_alert=True)
        return
    await state.set_state(EditStates.wait_video)
    await state.update_data(edit_video_id=vid)
    await callback.message.answer(video_card_text(storage, row), reply_markup=edit_actions_kb())
    await callback.answer()


@dp.callback_query(F.data == "edit:title")
async def edit_title_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_manage_callback_access(callback, state):
        return
    await state.update_data(edit_field="title")
    await callback.message.answer("Введите новое название.", reply_markup=nav_kb())
    await callback.answer()


@dp.message(EditStates.wait_video)
async def edit_message_router(message: Message, state: FSMContext) -> None:
    if not await ensure_manage_access(message, state):
        return
    data = await state.get_data()
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Редактирование отменено.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))
        return
    if message.text == BACK:
        await state.clear()
        await message.answer("Назад в меню.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))
        return
    if data.get("edit_field") == "title":
        title = (message.text or "").strip()
        if not title:
            await message.answer("Пустое название нельзя.")
            return
        storage.update_title(data["edit_video_id"], title)
        await message.answer("Название обновлено.", reply_markup=main_menu_kb(can_manage_content(message.from_user.id if message.from_user else None)))
        await state.clear()


@dp.callback_query(F.data == "edit:delete")
async def edit_delete(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_manage_callback_access(callback, state):
        return
    data = await state.get_data()
    vid = data.get("edit_video_id")
    if not vid:
        await callback.answer("Сначала выберите видео", show_alert=True)
        return
    row = storage.get_video(vid)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить удаление", callback_data=f"del:confirm:{vid}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="del:cancel")],
        ]
    )
    await callback.message.answer(f"Подтвердите удаление:\n{video_card_text(storage, row)}", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("video:delete:"))
async def delete_preview(callback: CallbackQuery) -> None:
    if not can_manage_content(callback.from_user.id if callback.from_user else None):
        await callback.answer("Только чтение", show_alert=True)
        return
    vid = int(callback.data.split(":")[-1])
    row = storage.get_video(vid)
    if not row:
        await callback.answer("Видео не найдено", show_alert=True)
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить удаление", callback_data=f"del:confirm:{vid}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="del:cancel")],
        ]
    )
    await callback.message.answer(f"Перед удалением:\n{video_card_text(storage, row)}", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("del:confirm:"))
async def delete_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_manage_callback_access(callback, state):
        return
    vid = int(callback.data.split(":")[-1])
    storage.delete_video(vid)
    await state.clear()
    await callback.message.answer("Видео удалено.", reply_markup=main_menu_kb(can_manage_content(callback.from_user.id if callback.from_user else None)))
    await callback.answer()


@dp.callback_query(F.data == "del:cancel")
async def delete_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Удаление отменено.", reply_markup=main_menu_kb(can_manage_content(callback.from_user.id if callback.from_user else None)))
    await callback.answer()


@dp.channel_post(F.video)
async def vault_channel_post(message: Message) -> None:
    if not STORAGE_CHAT_ID or message.chat.id != STORAGE_CHAT_ID or not message.video:
        return
    updated = storage.refresh_file_id_from_storage(
        message.message_id,
        message.video.file_id,
        message.video.file_unique_id,
    )
    if updated:
        logging.info("Storage refresh applied for storage_message_id=%s", message.message_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    bot = Bot(token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
