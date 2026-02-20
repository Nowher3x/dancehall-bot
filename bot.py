import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
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
    wait_tags = State()
    confirm = State()


class SearchStates(StatesGroup):
    choose_filter = State()
    wait_query = State()


class EditStates(StatesGroup):
    wait_video = State()


class DeleteStates(StatesGroup):
    wait_video = State()


from storage import Storage, normalize_url


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить видео"), KeyboardButton(text="🔎 Поиск")],
            [KeyboardButton(text="⭐ Избранное"), KeyboardButton(text="✏️ Редактировать")],
            [KeyboardButton(text="🗑 Удалить")],
        ],
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
    tags = ", ".join(storage.video_tags(row["id"])) or "—"
    return f"🔥 {row['title']}\nКатегории: {categories}\nТеги: {tags}"


def video_actions_kb(video_id: int, is_favorite: bool) -> InlineKeyboardMarkup:
    fav = "💔 Убрать из избранного" if is_favorite else "⭐ Добавить в избранное"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬇️ Скачать", callback_data=f"video:download:{video_id}")],
            [InlineKeyboardButton(text=fav, callback_data=f"video:fav:{video_id}")],
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"video:edit:{video_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"video:delete:{video_id}")],
        ]
    )


load_dotenv()
storage = Storage()
storage.ensure_taxonomy()
dp = Dispatcher(storage=MemoryStorage())


async def go_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Главное меню", reply_markup=main_menu_kb())


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await go_menu(message, state)


@dp.message(F.text == MENU)
async def menu_btn(message: Message, state: FSMContext) -> None:
    await go_menu(message, state)


@dp.message(F.text == "➕ Добавить видео")
async def add_video_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddVideoStates.wait_video)
    await message.answer("Пришлите видеофайл Telegram или URL.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_video, F.text == BACK)
async def add_video_back_from_video(message: Message, state: FSMContext) -> None:
    await go_menu(message, state)


@dp.message(AddVideoStates.wait_video, F.text == CANCEL)
async def add_video_cancel_video(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=main_menu_kb())


@dp.message(AddVideoStates.wait_video)
async def add_video_video(message: Message, state: FSMContext) -> None:
    file_id = file_unique_id = source_url = None
    if message.video:
        file_id = message.video.file_id
        file_unique_id = message.video.file_unique_id
        existing = storage.find_video_by_file_uid(file_unique_id)
        if existing:
            await message.answer("Такое видео уже есть, дубликат не создан.")
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

    await state.update_data(file_id=file_id, file_unique_id=file_unique_id, source_url=source_url)
    await state.set_state(AddVideoStates.wait_title)
    await message.answer("Введите название видео.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_title, F.text == BACK)
async def add_video_title_back(message: Message, state: FSMContext) -> None:
    await state.set_state(AddVideoStates.wait_video)
    await message.answer("Шаг назад. Пришлите видеофайл или URL.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_title, F.text == CANCEL)
async def add_video_title_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=main_menu_kb())


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
    await message.answer(
        "Введите категории через запятую (пример: Female, Groove).", reply_markup=nav_kb()
    )


@dp.callback_query(F.data.startswith("add:dup:"))
async def add_duplicate_choice(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(":")[-1]
    if choice == "rename":
        await callback.message.answer("Введите новое название.")
        await callback.answer()
        return
    await state.update_data(duplicate_choice=choice)
    await state.set_state(AddVideoStates.wait_categories)
    await callback.message.answer("Введите категории через запятую.", reply_markup=nav_kb())
    await callback.answer()


@dp.message(AddVideoStates.wait_categories, F.text == BACK)
async def add_categories_back(message: Message, state: FSMContext) -> None:
    await state.set_state(AddVideoStates.wait_title)
    await message.answer("Шаг назад. Введите название.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_categories, F.text == CANCEL)
async def add_categories_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=main_menu_kb())


@dp.message(AddVideoStates.wait_categories)
async def add_categories(message: Message, state: FSMContext) -> None:
    categories = [c.strip() for c in (message.text or "").split(",") if c.strip()]
    if not categories:
        await message.answer("Нужно выбрать хотя бы одну категорию.")
        return
    await state.update_data(categories=categories)
    await state.set_state(AddVideoStates.wait_tags)
    await message.answer("Введите теги через запятую.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_tags, F.text == BACK)
async def add_tags_back(message: Message, state: FSMContext) -> None:
    await state.set_state(AddVideoStates.wait_categories)
    await message.answer("Шаг назад. Введите категории.", reply_markup=nav_kb())


@dp.message(AddVideoStates.wait_tags, F.text == CANCEL)
async def add_tags_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=main_menu_kb())


@dp.message(AddVideoStates.wait_tags)
async def add_tags(message: Message, state: FSMContext) -> None:
    tags = [t.strip() for t in (message.text or "").split(",") if t.strip()]
    if not tags:
        await message.answer("Нужно выбрать хотя бы один тег.")
        return
    await state.update_data(tags=tags)
    data = await state.get_data()
    preview = f"Предпросмотр:\n🔥 {data['title']}\nКатегории: {', '.join(data['categories'])}\nТеги: {', '.join(tags)}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Сохранить", callback_data="add:save")]]
    )
    await state.set_state(AddVideoStates.confirm)
    await message.answer(preview, reply_markup=kb)


@dp.message(AddVideoStates.confirm, F.text == BACK)
async def add_confirm_back(message: Message, state: FSMContext) -> None:
    await state.set_state(AddVideoStates.wait_tags)
    await message.answer("Шаг назад. Введите теги.", reply_markup=nav_kb())


@dp.message(AddVideoStates.confirm, F.text == CANCEL)
async def add_confirm_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=main_menu_kb())


@dp.callback_query(F.data == "add:save")
async def add_save(callback: CallbackQuery, state: FSMContext) -> None:
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
            data["tags"],
        )
        row = storage.get_video(duplicate_video_id)
        await callback.message.answer("Видео заменено.")
    else:
        vid = storage.create_video(
            data["title"],
            data.get("file_id"),
            data.get("file_unique_id"),
            data.get("source_url"),
            data["categories"],
            data["tags"],
        )
        row = storage.get_video(vid)
        await callback.message.answer("Видео сохранено.")

    await state.clear()
    await send_video_card(callback.message, row, callback.from_user.id)
    await callback.message.answer("Готово.", reply_markup=main_menu_kb())
    await callback.answer()


@dp.message(F.text == "🔎 Поиск")
async def search_start(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchStates.choose_filter)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏷 По тегу"), KeyboardButton(text="📁 По категории")],
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
    await message.answer("Поиск отменен.", reply_markup=main_menu_kb())


@dp.message(SearchStates.choose_filter)
async def search_choose_filter(message: Message, state: FSMContext) -> None:
    mapping = {"🏷 По тегу": "tag", "📁 По категории": "category", "🔤 По названию": "title"}
    if message.text not in mapping:
        await message.answer("Выберите вариант кнопкой.")
        return
    await state.update_data(filter_type=mapping[message.text])
    await state.set_state(SearchStates.wait_query)
    await message.answer("Введите запрос.", reply_markup=nav_kb())


@dp.message(SearchStates.wait_query, F.text == BACK)
async def search_query_back(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchStates.choose_filter)
    await search_start(message, state)


@dp.message(SearchStates.wait_query, F.text == CANCEL)
async def search_query_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Поиск отменен.", reply_markup=main_menu_kb())


async def send_results(message: Message, user_id: int, mode: str, filter_type: str, query: str, page: int) -> None:
    if mode == "favorites":
        rows, total_pages = storage.favorites(user_id, page)
    elif mode == "all":
        rows, total_pages = storage.list_all_videos(page)
    else:
        rows, total_pages = storage.search(filter_type, query, page)

    if not rows:
        await message.answer("Ничего не найдено. Нажмите ⬅️ Назад или 🏠 В меню.")
        return
    lines = [f"Страница {page+1}/{total_pages}"]
    for r in rows:
        lines.append(f"• {r['title']} (id={r['id']})")
    kb_rows = [[InlineKeyboardButton(text=r["title"], callback_data=f"video:open:{r['id']}")] for r in rows]
    kb_rows.append(pagination_kb(f"list:{mode}:{filter_type}:{query}", page, total_pages).inline_keyboard[0])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@dp.message(SearchStates.wait_query)
async def search_query(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Пустой запрос недопустим.")
        return
    data = await state.get_data()
    await state.clear()
    await send_results(message, message.from_user.id, "search", data["filter_type"], query, 0)
    await message.answer("Выберите видео или вернитесь в меню.", reply_markup=main_menu_kb())


@dp.message(F.text == "⭐ Избранное")
async def favorites_open(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_results(message, message.from_user.id, "favorites", "favorite", "my", 0)


@dp.message(F.text == "✏️ Редактировать")
async def edit_open(message: Message, state: FSMContext) -> None:
    await state.set_state(EditStates.wait_video)
    await send_results(message, message.from_user.id, "all", "all", "all", 0)
    await message.answer("Откройте карточку и нажмите ✏️ Редактировать.", reply_markup=nav_kb())


@dp.message(F.text == "🗑 Удалить")
async def delete_open(message: Message, state: FSMContext) -> None:
    await state.set_state(DeleteStates.wait_video)
    await send_results(message, message.from_user.id, "all", "all", "all", 0)
    await message.answer("Выберите видео для удаления.", reply_markup=nav_kb())


@dp.callback_query(F.data.startswith("list:"))
async def paginate(callback: CallbackQuery) -> None:
    _, mode, filter_type, query, _, page = callback.data.split(":", 5)
    await callback.message.delete()
    await send_results(callback.message, callback.from_user.id, mode, filter_type, query, int(page))
    await callback.answer()


async def send_video_card(target: Message, row, user_id: int) -> None:
    await target.answer(
        video_card_text(storage, row),
        reply_markup=video_actions_kb(row["id"], storage.is_favorite(user_id, row["id"])),
    )


@dp.callback_query(F.data.startswith("video:open:"))
async def video_open(callback: CallbackQuery) -> None:
    vid = int(callback.data.split(":")[-1])
    row = storage.get_video(vid)
    if not row:
        await callback.answer("Видео не найдено", show_alert=True)
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
            reply_markup=video_actions_kb(vid, storage.is_favorite(callback.from_user.id, vid))
        )


@dp.callback_query(F.data.startswith("video:download:"))
async def video_download(callback: CallbackQuery) -> None:
    vid = int(callback.data.split(":")[-1])
    row = storage.get_video(vid)
    if not row:
        await callback.answer("Видео не найдено", show_alert=True)
        return
    if row["file_id"]:
        await callback.message.answer_video(row["file_id"], caption=row["title"])
    elif row["source_url"]:
        await callback.message.answer(f"Ссылка: {row['source_url']}")
    else:
        await callback.message.answer("Источник не найден.")
    await callback.answer()


@dp.callback_query(F.data.startswith("video:edit:"))
async def video_edit(callback: CallbackQuery, state: FSMContext) -> None:
    vid = int(callback.data.split(":")[-1])
    row = storage.get_video(vid)
    if not row:
        await callback.answer("Видео не найдено", show_alert=True)
        return
    await state.set_state(EditStates.wait_video)
    await state.update_data(edit_video_id=vid)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Название", callback_data="edit:title")],
            [InlineKeyboardButton(text="🏷 Теги", callback_data="edit:tags")],
            [InlineKeyboardButton(text="📁 Категории", callback_data="edit:categories")],
            [InlineKeyboardButton(text="🎬 Видео", callback_data="edit:video")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data="edit:delete")],
        ]
    )
    await callback.message.answer(video_card_text(storage, row), reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "edit:title")
async def edit_title_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(edit_field="title")
    await callback.message.answer("Введите новое название.", reply_markup=nav_kb())
    await callback.answer()


@dp.message(EditStates.wait_video)
async def edit_message_router(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Редактирование отменено.", reply_markup=main_menu_kb())
        return
    if message.text == BACK:
        await state.clear()
        await message.answer("Назад в меню.", reply_markup=main_menu_kb())
        return
    if data.get("edit_field") == "title":
        title = (message.text or "").strip()
        if not title:
            await message.answer("Пустое название нельзя.")
            return
        storage.update_title(data["edit_video_id"], title)
        await message.answer("Название обновлено.", reply_markup=main_menu_kb())
        await state.clear()


@dp.callback_query(F.data == "edit:delete")
async def edit_delete(callback: CallbackQuery, state: FSMContext) -> None:
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
    vid = int(callback.data.split(":")[-1])
    storage.delete_video(vid)
    await state.clear()
    await callback.message.answer("Видео удалено.", reply_markup=main_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "del:cancel")
async def delete_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Удаление отменено.", reply_markup=main_menu_kb())
    await callback.answer()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    bot = Bot(token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
