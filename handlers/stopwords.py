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
from aiogram.types import Message

from database import Database
from handlers._kb import is_admin

log = logging.getLogger(__name__)
router = Router()
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
    if not is_admin(message.from_user.id):
        return

    await _ensure_defaults()
    words = db.get_stop_words()

    if not words:
        await message.answer(
            "📋 <b>Стоп-слова</b>\n\n"
            "Список пуст.\n\n"
            "Добавить: <code>/stopwords_add слово</code>",
            parse_mode="HTML"
        )
        return

    words_text = "\n".join(f"• {w}" for w in words)
    await message.answer(
        f"📋 <b>Стоп-слова</b> ({len(words)} шт.)\n\n"
        f"{words_text}\n\n"
        f"➕ Добавить: <code>/stopwords_add слово</code>\n"
        f"🗑 Удалить: <code>/stopwords_del слово</code>",
        parse_mode="HTML"
    )


@router.message(Command("stopwords_add"))
async def cmd_stopwords_add(message: Message):
    if not is_admin(message.from_user.id):
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
    if not is_admin(message.from_user.id):
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
