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
from aiogram.fsm.context import FSMContext
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



def build_urgent_keyboard(post_id: int, index: int = 0, total: int = 1):
    kb = InlineKeyboardBuilder()
    kb.button(text="🚀 Опубликовать", callback_data=f"urgent_publish_{post_id}_{index}")
    kb.button(text="⏭ Следующая", callback_data=f"urgent_skip_{post_id}_{index}")
    kb.button(text="✏️ Редактировать", callback_data=f"urgent_edit_{post_id}_{index}")
    kb.button(text="❌ Закрыть", callback_data="urgent_close")
    kb.adjust(2, 1, 1)
    return kb.as_markup()

@router.message(Command("urgent"))
async def cmd_urgent(message: Message):
    """Показывает список срочных новостей."""
    try:
        await message.delete()
    except Exception:
        pass
    count = db.get_urgent_count()
    if count == 0:
        await message.answer("✅ Нет новых срочных новостей.")
        return
    await show_urgent_post(message, index=0)


async def show_urgent_post(message, index: int = 0, edit_msg=None):
    """Показывает срочный пост по индексу."""
    from aiogram.types import FSInputFile
    import os
    from utils.post_sender import get_media_urls

    posts = db.get_urgent_posts(status='new')
    if not posts:
        text = "✅ Нет новых срочных новостей."
        if edit_msg:
            await edit_msg.edit_text(text)
        else:
            await message.answer(text)
        return

    total = len(posts)
    if index >= total:
        index = 0
    post = posts[index]

    folder = db.get_folder_by_id(post.get('folder_id'))
    city = folder['name'] if folder else 'Неизвестный город'
    text_preview = (post.get('text') or '')[:800]
    urgent_word = post.get('urgent_word', '')

    text = (
        f"⚡️ <b>СРОЧНАЯ</b> — {city} ({index+1}/{total})\n\n"
        f"{text_preview}"
        f"{'...' if len(post.get('text') or '') > 800 else ''}\n\n"
        f"🔑 Ключевое слово: <b>{urgent_word}</b>"
    )

    kb = build_urgent_keyboard(post['id'], index, total)

    media_urls = get_media_urls(post)
    photo = media_urls[0] if media_urls else post.get('image_url')

    if photo and os.path.isfile(str(photo)):
        await message.answer_photo(FSInputFile(photo), caption=text, reply_markup=kb, parse_mode="HTML")
    elif photo:
        await message.answer_photo(photo, caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("urgent_publish_"))
async def cb_urgent_publish(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split("_")
    post_id = int(parts[2])
    index = int(parts[3]) if len(parts) > 3 else 0

    post = db.get_post_by_id(post_id)
    if not post:
        await callback.answer("❌ Пост не найден", show_alert=True)
        return

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
            ok = await send_post(bot, int(ch['channel_id']), post, signature=ch.get('signature'))
            if ok:
                success += 1
                db.mark_as_posted(post_id)
                db.add_to_history(post_id, ch['channel_id'])
        except Exception as e:
            log.error(f"[URGENT] Ошибка публикации в {ch['channel_id']}: {e}")

    db.set_urgent_status(post_id, 'published')
    await callback.answer(f"✅ Опубликовано в {success} канал(ов)!")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_urgent_post(callback.message, index=index)


@router.callback_query(F.data.startswith("urgent_skip_"))
async def cb_urgent_skip(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split("_")
    post_id = int(parts[2])
    index = int(parts[3]) if len(parts) > 3 else 0

    db.set_urgent_status(post_id, 'skipped')
    await callback.answer("⏭ Пропущено")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_urgent_post(callback.message, index=index)



@router.callback_query(F.data.startswith("urgent_edit_"))
async def cb_urgent_edit(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split("_")
    post_id = int(parts[2])
    index = int(parts[3]) if len(parts) > 3 else 0
    # Открываем пост в стандартном редакторе
    from state import user_current_post, user_edited_text, user_selected_channels, user_selected_folder_for_publish
    post = db.get_post_by_id(post_id)
    if not post:
        await callback.answer("❌ Пост не найден", show_alert=True)
        return
    user_id = callback.from_user.id
    user_current_post[user_id] = post
    user_edited_text[user_id] = post['text']
    user_selected_channels[user_id] = set()
    if post.get('folder_id'):
        user_selected_folder_for_publish[user_id] = post['folder_id']
    await callback.answer("✏️ Открываю редактор...")
    try:
        await callback.message.delete()
    except Exception:
        pass
    # Показываем превью поста через posts handler
    from handlers.posts import _preview_kb, get_media_urls_from_post
    from utils.post_sender import get_media_urls
    media_urls = get_media_urls(post)
    kb = _preview_kb(post['id'], has_image=bool(media_urls) or bool(post.get('image_url')))
    text_preview = (post.get('text') or '')[:3000]
    await callback.message.answer(
        f"📝 <b>Редактирование срочного поста</b>\n\n{text_preview}\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(F.data == "urgent_close")
async def cb_urgent_close(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()
    from handlers.start import show_main_menu
    await show_main_menu(callback.message, state)

