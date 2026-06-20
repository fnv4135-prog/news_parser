"""
handlers/stopwords.py — управление стоп-словами.

Команды:
    /stopwords — показать список
    /stopwords_add <слово> — добавить
    /stopwords_del <слово> — удалить
"""
import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from database import Database
from config import ADMIN_IDS

log = logging.getLogger(__name__)
router = Router()

async def _delete_later(msg, seconds: int):
    import asyncio
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception:
        pass
db = Database()

_DEFAULT_STOP_WORDS = [
    "реклама", "скидка", "скидки", "промокод", "акция",
    "натяжной потолок", "бесплатный замер", "расчёт стоимости",
    "купить", "продам", "сдам", "заказать",
]


async def _ensure_defaults():
    """Добавляет дефолтные стоп-слова если список пуст."""
    words = db.get_stop_words()
    if not words:
        for w in _DEFAULT_STOP_WORDS:
            db.add_stop_word(w)
        log.info(f"Добавлено {len(_DEFAULT_STOP_WORDS)} дефолтных стоп-слов")


@router.message(Command("stopwords"))
async def cmd_stopwords(message: Message):
    try:
        words = db.get_stop_words()
        if not words:
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            kb = InlineKeyboardBuilder()
            kb.button(text="◀ Главное меню", callback_data="stopwords_close")
            await message.answer("📋 Стоп-слова: список пуст.\n\nДобавить: /stopwords_add слово", reply_markup=kb.as_markup())
            return
        words_text = "\n".join(f"• {w}" for w in words)
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="◀ Главное меню", callback_data="stopwords_close")
        await message.answer(
            f"📋 Стоп-слова ({len(words)} шт.):\n\n{words_text}\n\n"
            f"Добавить: /stopwords_add слово\n"
            f"Удалить: /stopwords_del слово",
            reply_markup=kb.as_markup()
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"[STOPWORDS] ошибка: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("stopwords_add"))
async def cmd_stopwords_add(message: Message):
    try:
        await message.delete()
    except Exception:
        pass
    if message.from_user.id not in ADMIN_IDS:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Укажите слово: <code>/stopwords_add слово</code>", parse_mode="HTML")
        return

    word = parts[1].strip().lower()
    if db.add_stop_word(word):
        await message.answer(f"✅ Стоп-слово <b>{word}</b> добавлено.", parse_mode="HTML")
        log.info(f"Стоп-слово добавлено: {word}")
    else:
        await message.answer(f"⚠️ Слово <b>{word}</b> уже есть в списке.", parse_mode="HTML")


@router.message(Command("stopwords_del"))
async def cmd_stopwords_del(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Укажите слово: <code>/stopwords_del слово</code>", parse_mode="HTML")
        return

    word = parts[1].strip().lower()
    if db.remove_stop_word(word):
        await message.answer(f"✅ Стоп-слово <b>{word}</b> удалено.", parse_mode="HTML")
        log.info(f"Стоп-слово удалено: {word}")
    else:
        await message.answer(f"❌ Слово <b>{word}</b> не найдено в списке.", parse_mode="HTML")


@router.callback_query(lambda c: c.data == "stopwords_close")
async def stopwords_close(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()
    from handlers.start import show_main_menu
    await show_main_menu(callback.message, state)
