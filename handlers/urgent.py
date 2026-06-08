"""
handlers/urgent.py — срочные новости.

Команды:
    /urgent_words        — показать список ключевых слов
    /urgent_words_add    — добавить слово
    /urgent_words_del    — удалить слово

Callback:
    urgent_publish_{id}  — опубликовать срочную новость
    urgent_skip_{id}     — пропустить
"""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database
from config import ADMIN_IDS

log = logging.getLogger(__name__)
router = Router()
db = Database()

_DEFAULT_URGENT_WORDS = [
    "дтп", "авария", "пожар", "чс", "срочно", "экстренно",
    "взрыв", "эвакуация", "погиб", "задержан", "обрушение",
    "затопление", "стрельба", "взрывчатка", "теракт",
]


async def _ensure_defaults():
    words = db.get_urgent_words()
    if not words:
        for w in _DEFAULT_URGENT_WORDS:
            db.add_urgent_word(w)
        log.info(f"Добавлено {len(_DEFAULT_URGENT_WORDS)} дефолтных срочных слов")


@router.message(Command("urgent_words"))
async def cmd_urgent_words(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await _ensure_defaults()
    words = db.get_urgent_words()
    if not words:
        await message.answer(
            "⚡️ <b>Срочные ключевые слова</b>\n\n"
            "Список пуст.\n\n"
            "Добавить: <code>/urgent_words_add слово</code>",
            parse_mode="HTML"
        )
        return
    words_text = "\n".join(f"• {w}" for w in words)
    await message.answer(
        f"⚡️ <b>Срочные ключевые слова</b> ({len(words)} шт.)\n\n"
        f"{words_text}\n\n"
        f"➕ Добавить: <code>/urgent_words_add слово</code>\n"
        f"🗑 Удалить: <code>/urgent_words_del слово</code>",
        parse_mode="HTML"
    )


@router.message(Command("urgent_words_add"))
async def cmd_urgent_words_add(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Укажите слово: <code>/urgent_words_add слово</code>", parse_mode="HTML")
        return
    word = parts[1].strip().lower()
    if db.add_urgent_word(word):
        await message.answer(f"✅ Слово <b>{word}</b> добавлено.", parse_mode="HTML")
    else:
        await message.answer(f"⚠️ Слово <b>{word}</b> уже есть.", parse_mode="HTML")


@router.message(Command("urgent_words_del"))
async def cmd_urgent_words_del(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Укажите слово: <code>/urgent_words_del слово</code>", parse_mode="HTML")
        return
    word = parts[1].strip().lower()
    if db.remove_urgent_word(word):
        await message.answer(f"✅ Слово <b>{word}</b> удалено.", parse_mode="HTML")
    else:
        await message.answer(f"❌ Слово <b>{word}</b> не найдено.", parse_mode="HTML")


@router.callback_query(F.data.startswith("urgent_publish_"))
async def cb_urgent_publish(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    post_id = int(callback.data.split("_")[2])
    post = db.get_post_by_id(post_id)
    if not post:
        await callback.answer("❌ Пост не найден", show_alert=True)
        return

    # Получаем каналы для папки поста
    channels = db.get_publish_channels_by_folder(post['folder_id'])
    if not channels:
        await callback.answer("❌ Нет каналов для публикации", show_alert=True)
        return

    from utils.post_sender import send_post
    from bot_instance import get_bot
    bot = get_bot()
    success = 0
    for ch in channels:
        try:
            ok = await send_post(bot, int(ch['channel_id']), post,
                                 signature=ch.get('signature'))
            if ok:
                success += 1
                db.mark_as_posted(post_id)
                db.add_to_history(post_id, ch['channel_id'])
        except Exception as e:
            log.error(f"[URGENT] Ошибка публикации в {ch['channel_id']}: {e}")

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ Опубликовано в {success} канал(ов).",
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("urgent_skip_"))
async def cb_urgent_skip(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    post_id = int(callback.data.split("_")[2])
    db.mark_as_posted(post_id)  # помечаем чтобы больше не предлагать
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ Пропущено.",
        parse_mode="HTML"
    )
    await callback.answer()


def build_urgent_keyboard(post_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="🚀 Опубликовать", callback_data=f"urgent_publish_{post_id}")
    kb.button(text="❌ Пропустить", callback_data=f"urgent_skip_{post_id}")
    kb.adjust(2)
    return kb.as_markup()
