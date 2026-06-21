"""
handlers/stopwords.py — управление стоп-словами.
"""
import logging
import asyncio
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database
from config import ADMIN_IDS

log = logging.getLogger(__name__)
router = Router()
db = Database()

# Хранилище message_id основного сообщения стоп-слов
_sw_msg_ids: dict = {}  # user_id -> message_id


class StopWordsStates(StatesGroup):
    waiting_add = State()


def _build_keyboard(words: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    # Кнопка удаления на каждое слово
    for w in words:
        short = w[:20] + "…" if len(w) > 20 else w
        kb.button(text=f"❌ {short}", callback_data=f"sw_del|{w[:50]}")
    kb.adjust(2)
    # Нижние кнопки
    kb.row(InlineKeyboardButton(text="➕ Добавить", callback_data="sw_add"))
    kb.row(InlineKeyboardButton(text="◀ Главное меню", callback_data="sw_close"))
    return kb.as_markup()


def _build_text(words: list) -> str:
    if not words:
        return "📋 <b>Стоп-слова</b>\n\nСписок пуст."
    words_text = "\n".join(f"• {w}" for w in words)
    return f"📋 <b>Стоп-слова</b> ({len(words)} шт.):\n\n{words_text}"


async def _show_stopwords(target, user_id: int, state: FSMContext = None):
    """Отправляет или редактирует сообщение со стоп-словами."""
    from bot_instance import get_bot
    words = db.get_stop_words()
    text = _build_text(words)
    kb = _build_keyboard(words)

    existing_mid = _sw_msg_ids.get(user_id)
    bot = get_bot()

    if existing_mid:
        try:
            await bot.edit_message_text(
                text, chat_id=target.chat.id,
                message_id=existing_mid,
                reply_markup=kb, parse_mode="HTML"
            )
            return
        except Exception:
            pass

    sent = await target.answer(text, reply_markup=kb, parse_mode="HTML")
    _sw_msg_ids[user_id] = sent.message_id


@router.message(Command("stopwords"))
async def cmd_stopwords(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await _show_stopwords(message, message.from_user.id, state)


@router.callback_query(F.data == "sw_add")
async def cb_sw_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(StopWordsStates.waiting_add)
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sw_cancel_add")]
    ])
    sent = await callback.message.answer(
        "✍️ Введите стоп-слово или фразу.\n"
        "Можно несколько через запятую:\n"
        "<code>реклама, скидка, купить</code>",
        parse_mode="HTML", reply_markup=kb
    )
    await state.update_data(prompt_mid=sent.message_id)


@router.callback_query(F.data == "sw_cancel_add")
async def cb_sw_cancel_add(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _show_stopwords(callback.message, callback.from_user.id)


@router.message(StopWordsStates.waiting_add)
async def handle_sw_add(message: Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    await state.clear()

    # Удаляем prompt и ввод пользователя
    try:
        await message.delete()
    except Exception:
        pass
    prompt_mid = data.get("prompt_mid")
    if prompt_mid:
        try:
            await message.bot.delete_message(message.chat.id, prompt_mid)
        except Exception:
            pass

    # Парсим через запятую
    raw = message.text.strip()
    items = [w.strip().lower() for w in raw.split(",") if w.strip()]

    added = []
    dupes = []
    for w in items:
        if db.add_stop_word(w):
            added.append(w)
        else:
            dupes.append(w)

    if added:
        log.info(f"Стоп-слова добавлены: {added}")

    # Обновляем основное сообщение
    await _show_stopwords(message, user_id)

    # Краткий итог — исчезает через 4 сек
    parts = []
    if added:
        parts.append(f"✅ Добавлено: {', '.join(added)}")
    if dupes:
        parts.append(f"⚠️ Уже есть: {', '.join(dupes)}")
    if parts:
        note = await message.answer("\n".join(parts))
        asyncio.create_task(_delete_later(note, 4))


@router.callback_query(F.data.startswith("sw_del|"))
async def cb_sw_del(callback: CallbackQuery):
    word = callback.data.split("|", 1)[1]
    if db.remove_stop_word(word):
        await callback.answer(f"Удалено: {word}")
        log.info(f"Стоп-слово удалено: {word}")
    else:
        await callback.answer("Не найдено", show_alert=True)
        return
    await _show_stopwords(callback.message, callback.from_user.id)


@router.callback_query(F.data == "sw_close")
async def cb_sw_close(callback: CallbackQuery, state: FSMContext):
    _sw_msg_ids.pop(callback.from_user.id, None)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()
    from handlers.start import show_main_menu
    await show_main_menu(callback.message, state, edit=False)


async def _delete_later(msg, seconds: int):
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception:
        pass
