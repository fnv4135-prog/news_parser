import asyncio
import logging
import os
import json
from datetime import datetime
from typing import Dict, List, Set

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database
from state import (
    PostEditStates,
    user_posts_cache, user_pages, user_current_post,
    user_selected_channels, user_edited_text, user_selected_folder_for_publish,
    user_custom_signature, user_source_filter, user_schedule_data,
    user_preview_msg_ids, user_list_msg_id
)
from utils.delete_utils import delete_message
from utils.post_sender import send_post, send_preview_media, get_media_urls
from bot_instance import get_bot

router = Router()
db = Database()


def get_photo_input(image_url: str):
    """
    Определяет тип фото и возвращает нужный формат для отправки.
    Если это локальный файл — возвращает FSInputFile.
    Если URL или file_id — возвращает как есть.
    """
    if image_url and os.path.isfile(image_url):
        return FSInputFile(image_url)
    return image_url


async def _cleanup_preview(bot, chat_id: int, user_id: int, except_msg_id: int = None):
    """Удаляет старые сообщения предпросмотра поста (фото + текст с кнопками).
    except_msg_id — не удалять это сообщение (для edit_text)."""
    msg_ids = user_preview_msg_ids.pop(user_id, [])
    for mid in msg_ids:
        if mid == except_msg_id:
            continue
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass


async def _cleanup_all(bot, chat_id: int, user_id: int, except_msg_id: int = None):
    """Удаляет ВСЕ сообщения бота из потока постов (список + превью)."""
    # Удаляем превью
    await _cleanup_preview(bot, chat_id, user_id, except_msg_id)
    # Удаляем сообщение-список
    list_mid = user_list_msg_id.pop(user_id, None)
    if list_mid and list_mid != except_msg_id:
        try:
            await bot.delete_message(chat_id, list_mid)
        except Exception:
            pass


def _extract_post_id(callback_data: str) -> int:
    """Извлекает post_id из callback_data (последний элемент после |)"""
    parts = callback_data.split("|")
    return int(parts[-1])


def _preview_kb(post_id: int, has_image: bool = False) -> InlineKeyboardMarkup:
    """Кнопки предпросмотра поста"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать текст", callback_data=f"edit_post_text|{post_id}")],
        [InlineKeyboardButton(text="🖼 Заменить фото/видео", callback_data=f"replace_photo|{post_id}")],
        *([[InlineKeyboardButton(text="🪄 Убрать водяной знак", callback_data=f"remove_watermark|{post_id}")]] if has_image else []),
        [InlineKeyboardButton(text="📢 Выбрать город для публикации", callback_data=f"choose_publish_city|{post_id}")],
        [InlineKeyboardButton(text="◀ К списку", callback_data="back_to_posts_list"), InlineKeyboardButton(text="➡️ Следующий", callback_data=f"next_post|{post_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_publish|{post_id}")]
    ])


def _publish_kb(post_id: int) -> InlineKeyboardMarkup:
    """Кнопки публикации (после выбора каналов)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Опубликовать сейчас", callback_data=f"publish_now|{post_id}")],
        [InlineKeyboardButton(text="🕒 Отложить", callback_data=f"schedule_post|{post_id}")],
        [InlineKeyboardButton(text="📝 Изменить подпись", callback_data=f"edit_post_signature|{post_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_publish|{post_id}")]
    ])


def _restart_kb() -> InlineKeyboardMarkup:
    """Кнопка перезапуска /posts — для тупиков"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📰 Открыть /posts", callback_data="restart_posts")]
    ])


async def _validate_post(callback: CallbackQuery) -> dict:
    """Проверяет что post_id из кнопки совпадает с текущим. Возвращает post или None."""
    user_id = callback.from_user.id
    try:
        btn_post_id = _extract_post_id(callback.data)
    except (ValueError, IndexError):
        await callback.answer("⚠️ Ошибка кнопки. Откройте пост заново.", show_alert=True)
        return None

    current = user_current_post.get(user_id)
    if not current or current.get('id') != btn_post_id:
        await callback.answer(
            "⚠️ Эта кнопка устарела — вы уже открыли другой пост.\n"
            "Нажмите на нужный пост в списке заново.",
            show_alert=True
        )
        return None
    return current

# ----------------------------------------------------------------------
# Команда /posts – выбор города
# ----------------------------------------------------------------------
@router.message(Command("posts"))
async def cmd_posts(message: Message, state: FSMContext):
    await state.clear()  # Очищаем FSM состояние
    user_id = message.from_user.id
    logging.info(f"User {user_id} вызвал /posts")
    
    # Удаляем все старые сообщения из потока постов
    bot = get_bot()
    await _cleanup_all(bot, message.chat.id, user_id)
    
    folders = db.get_folders()
    if not folders:
        await message.answer("📭 Нет добавленных городов. Сначала создайте город через /cities.")
        await message.delete()
        return
    builder = InlineKeyboardBuilder()
    for folder in folders:
        builder.button(text=folder['name'], callback_data=f"posts_city_{folder['id']}")
    builder.button(text="❌ Отмена", callback_data="cancel_posts")
    builder.adjust(2)
    sent = await message.answer("🏙 Выберите город для просмотра постов:", reply_markup=builder.as_markup())
    user_list_msg_id[user_id] = sent.message_id
    await message.delete()

@router.callback_query(F.data.startswith("posts_city_"))
async def select_city_for_posts(callback: CallbackQuery):
    """После выбора города показываем меню фильтра по источникам"""
    folder_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    logging.info(f"User {user_id} выбрал город {folder_id} для просмотра постов")
    
    user_selected_folder_for_publish[user_id] = folder_id
    
    # Получаем статистику по источникам
    stats = db.get_posts_by_source_count(folder_id)
    vk_count = stats.get('vk', 0)
    tg_count = stats.get('telegram', 0)
    rss_count = stats.get('rss', 0)
    total = vk_count + tg_count + rss_count
    
    if total == 0:
        await callback.message.edit_text("📭 Нет новых постов в этом городе. Запустите парсинг через /parse_now.", reply_markup=_restart_kb())
        await callback.answer()
        return
    
    builder = InlineKeyboardBuilder()
    if vk_count > 0:
        builder.button(text=f"📺 VK ({vk_count})", callback_data=f"filter_source|vk")
    if tg_count > 0:
        builder.button(text=f"📱 TG ({tg_count})", callback_data=f"filter_source|telegram")
    if rss_count > 0:
        builder.button(text=f"🌐 RSS ({rss_count})", callback_data=f"filter_source|rss")
    builder.button(text=f"📋 Все ({total})", callback_data=f"filter_source|all")
    builder.button(text="❌ Отмена", callback_data="cancel_posts")
    builder.adjust(2)
    
    await callback.message.edit_text(
        f"📰 Выберите источник:\n\n"
        f"📺 VK: {vk_count} постов\n"
        f"📱 Telegram: {tg_count} постов\n"
        f"🌐 RSS: {rss_count} постов\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📋 Всего: {total} постов",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("filter_source|"))
async def filter_by_source(callback: CallbackQuery):
    """Загружаем посты с выбранным фильтром"""
    source_filter = callback.data.split("|")[1]
    user_id = callback.from_user.id
    folder_id = user_selected_folder_for_publish.get(user_id)
    
    if not folder_id:
        await callback.answer("Ошибка: город не выбран", show_alert=True)
        return
    
    user_source_filter[user_id] = source_filter
    
    # Загружаем посты с фильтром
    if source_filter == 'all':
        # Для "Все" делаем микс — чередуем источники
        posts = await get_mixed_posts(folder_id)
    else:
        posts = db.get_posts(folder_id=folder_id, only_new=True, limit=200, source_filter=source_filter)
    
    if not posts:
        filter_name = {'vk': 'VK', 'telegram': 'Telegram', 'rss': 'RSS', 'all': 'всех источников'}.get(source_filter, source_filter)
        await callback.message.edit_text(f"📭 Нет постов из {filter_name}.", reply_markup=_restart_kb())
        await callback.answer()
        return
    
    posts.sort(key=lambda p: p['published_at'] or datetime.min, reverse=True)
    posts = posts[:100]
    user_posts_cache[user_id] = posts
    user_pages[user_id] = 0
    
    text, markup = await get_posts_page_text_and_markup(user_id, 0)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


async def get_mixed_posts(folder_id: int) -> list:
    """Получаем посты вперемешку из разных источников"""
    vk_posts = db.get_posts(folder_id=folder_id, only_new=True, limit=100, source_filter='vk')
    tg_posts = db.get_posts(folder_id=folder_id, only_new=True, limit=100, source_filter='telegram')
    rss_posts = db.get_posts(folder_id=folder_id, only_new=True, limit=100, source_filter='rss')
    
    # Сортируем каждый список по дате
    for lst in [vk_posts, tg_posts, rss_posts]:
        lst.sort(key=lambda p: p['published_at'] or datetime.min, reverse=True)
    
    # Чередуем: берём по одному из каждого источника
    mixed = []
    max_len = max(len(vk_posts), len(tg_posts), len(rss_posts))
    
    for i in range(max_len):
        if i < len(vk_posts):
            mixed.append(vk_posts[i])
        if i < len(tg_posts):
            mixed.append(tg_posts[i])
        if i < len(rss_posts):
            mixed.append(rss_posts[i])
    
    return mixed[:100]

async def get_posts_page_text_and_markup(user_id: int, page: int):
    posts = user_posts_cache.get(user_id, [])
    if not posts:
        return "Посты не найдены. Используйте /posts заново.", None
    start = page * 10
    end = start + 10
    page_posts = posts[start:end]
    if not page_posts:
        return "Это последняя страница", None

    builder = InlineKeyboardBuilder()
    for post in page_posts:
        # Иконка источника
        source_icon = {'vk': '📺', 'telegram': '📱', 'rss': '🌐'}.get(post.get('source'), '❓')
        title = post.get('title') or ''
        text_preview = post.get('text') or ''
        # RSS имеет нормальный title, VK/TG — берём начало текста
        if title and title != text_preview[:len(title)]:
            short_title = title[:45].replace('\n', ' ')
        else:
            short_title = text_preview[:45].replace('\n', ' ')
        builder.button(text=f"{source_icon} {short_title}", callback_data=f"post|{post['id']}")
    builder.adjust(1)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"posts_page|{page-1}"))
    if end < len(posts):
        nav_buttons.append(InlineKeyboardButton(text="Вперёд ▶", callback_data=f"posts_page|{page+1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
    
    # Кнопка обновления и фильтра
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_posts"),
        InlineKeyboardButton(text="🔀 Фильтр", callback_data="change_filter")
    )
    builder.row(InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel_posts"))

    # Показываем текущий фильтр
    current_filter = user_source_filter.get(user_id, 'all')
    filter_name = {'vk': '📺 VK', 'telegram': '📱 TG', 'rss': '🌐 RSS', 'all': '📋 Все'}.get(current_filter, current_filter)
    
    total_pages = (len(posts) - 1) // 10 + 1
    text = f"📰 Посты [{filter_name}] (стр. {page+1}/{total_pages}, всего {len(posts)}):"
    return text, builder.as_markup()


@router.callback_query(F.data == "change_filter")
async def change_filter(callback: CallbackQuery):
    """Вернуться к выбору фильтра"""
    user_id = callback.from_user.id
    folder_id = user_selected_folder_for_publish.get(user_id)
    
    if not folder_id:
        await callback.answer("Выберите город заново через /posts", show_alert=True)
        return
    
    # Получаем статистику по источникам
    stats = db.get_posts_by_source_count(folder_id)
    vk_count = stats.get('vk', 0)
    tg_count = stats.get('telegram', 0)
    rss_count = stats.get('rss', 0)
    total = vk_count + tg_count + rss_count
    
    builder = InlineKeyboardBuilder()
    if vk_count > 0:
        builder.button(text=f"📺 VK ({vk_count})", callback_data=f"filter_source|vk")
    if tg_count > 0:
        builder.button(text=f"📱 TG ({tg_count})", callback_data=f"filter_source|telegram")
    if rss_count > 0:
        builder.button(text=f"🌐 RSS ({rss_count})", callback_data=f"filter_source|rss")
    builder.button(text=f"📋 Все ({total})", callback_data=f"filter_source|all")
    builder.button(text="❌ Отмена", callback_data="cancel_posts")
    builder.adjust(2)
    
    await callback.message.edit_text(
        f"📰 Выберите источник:\n\n"
        f"📺 VK: {vk_count} постов\n"
        f"📱 Telegram: {tg_count} постов\n"
        f"🌐 RSS: {rss_count} постов\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📋 Всего: {total} постов",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "refresh_posts")
async def refresh_posts(callback: CallbackQuery):
    """Обновить список постов из БД с текущим фильтром"""
    user_id = callback.from_user.id
    folder_id = user_selected_folder_for_publish.get(user_id)
    source_filter = user_source_filter.get(user_id, 'all')
    
    if not folder_id:
        await callback.answer("Выберите город заново через /posts", show_alert=True)
        return
    
    # Загружаем посты с текущим фильтром
    if source_filter == 'all':
        posts = await get_mixed_posts(folder_id)
    else:
        posts = db.get_posts(folder_id=folder_id, only_new=True, limit=200, source_filter=source_filter)
    
    if not posts:
        await callback.message.edit_text("📭 Нет новых постов.", reply_markup=_restart_kb())
        await callback.answer()
        return
    
    posts.sort(key=lambda p: p['published_at'] or datetime.min, reverse=True)
    posts = posts[:100]
    user_posts_cache[user_id] = posts
    user_pages[user_id] = 0
    
    text, markup = await get_posts_page_text_and_markup(user_id, 0)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer("✅ Список обновлён")

@router.callback_query(F.data.startswith("posts_page|"))
async def change_page(callback: CallbackQuery):
    user_id = callback.from_user.id
    page = int(callback.data.split("|")[1])
    user_pages[user_id] = page
    text, markup = await get_posts_page_text_and_markup(user_id, page)
    if markup is None:
        await callback.answer(text)
        return
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()

# ----------------------------------------------------------------------
# Выбор поста – показываем фото (если есть) и текст
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("post|"))
async def select_post(callback: CallbackQuery):
    post_id = int(callback.data.split("|")[1])
    user_id = callback.from_user.id
    post = db.get_post_by_id(post_id)
    
    current_folder = user_selected_folder_for_publish.get(user_id)
    
    if not post:
        await callback.answer("⚠️ Пост не найден. Нажмите /posts заново.", show_alert=True)
        return

    # Проверяем что пост из того города, который выбран
    if current_folder and post.get('folder_id') and post['folder_id'] != current_folder:
        await callback.answer(
            "⚠️ Этот пост из другого города. Нажмите /posts и выберите город заново.",
            show_alert=True
        )
        return

    user_current_post[user_id] = post
    user_edited_text[user_id] = post['text']
    user_selected_channels[user_id] = set()
    # Устанавливаем folder_id из поста (на случай рестарта бота)
    if post.get('folder_id'):
        user_selected_folder_for_publish[user_id] = post['folder_id']

    # Удаляем ВСЕ старые сообщения (и превью, и список)
    bot = get_bot()
    await _cleanup_all(bot, callback.message.chat.id, user_id)
    # Также удаляем само сообщение-список (на случай если оно не трекалось)
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Показываем ВСЕ медиа (альбомом если несколько)
    preview_msg_ids = []
    media_urls = get_media_urls(post)
    if media_urls:
        media_ids = await send_preview_media(callback.message, media_urls)
        preview_msg_ids.extend(media_ids)
    
    preview_text = post['text'][:3000]
    if len(post['text']) > 3000:
        preview_text += "..."
    
    # Показываем источник поста
    source_icon = {'vk': '📺 VK', 'telegram': '📱 TG', 'rss': '🌐 RSS'}.get(post.get('source'), '❓')

    # Информация о публикации
    text_len = len(post['text'])
    info_line = ""
    if media_urls and text_len > 1024:
        info_line = f"\n\n📖 <i>Длинный текст — будет опубликован с кнопкой «Читать полностью»</i>"
    media_info = ""
    if len(media_urls) > 1:
        media_info = f" · 🖼 {len(media_urls)} фото"
    elif len(media_urls) == 1:
        media_info = " · 🖼 фото"
    
    kb = _preview_kb(post['id'], has_image=bool(media_urls) or bool(post.get('image_url')))
    sent_text = await callback.message.answer(
        f"📝 <b>Предпросмотр поста</b> [{source_icon}{media_info}]{info_line}\n\n{preview_text}\n\n"
        f"Выберите действие:",
        parse_mode="HTML",
        reply_markup=kb
    )
    preview_msg_ids.append(sent_text.message_id)
    user_preview_msg_ids[user_id] = preview_msg_ids
    await callback.answer()


@router.callback_query(F.data == "back_to_posts_list")
async def back_to_posts_list(callback: CallbackQuery):
    """Вернуться к списку постов"""
    user_id = callback.from_user.id
    
    # Удаляем ВСЕ старые сообщения кроме текущего (его переиспользуем как список)
    bot = get_bot()
    await _cleanup_all(bot, callback.message.chat.id, user_id,
                       except_msg_id=callback.message.message_id)
    
    # Всегда перезагружаем кэш с текущим фильтром
    folder_id = user_selected_folder_for_publish.get(user_id)
    source_filter = user_source_filter.get(user_id, 'all')
    if folder_id:
        if source_filter == 'all':
            posts = await get_mixed_posts(folder_id)
        else:
            posts = db.get_posts(folder_id=folder_id, only_new=True, limit=200, source_filter=source_filter)
        if posts:
            posts.sort(key=lambda p: p['published_at'] or datetime.min, reverse=True)
            user_posts_cache[user_id] = posts[:100]
            user_pages[user_id] = 0
    
    page = user_pages.get(user_id, 0)
    text, markup = await get_posts_page_text_and_markup(user_id, page)
    if markup:
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            # Если edit не сработал — отправляем новое
            sent = await callback.message.answer(text, reply_markup=markup)
            user_list_msg_id[user_id] = sent.message_id
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.answer()
            return
        # Текущее сообщение стало списком — трекаем
        user_list_msg_id[user_id] = callback.message.message_id
    else:
        await callback.message.edit_text("📭 Нет постов.", reply_markup=_restart_kb())
    await callback.answer()

# ----------------------------------------------------------------------
# Замена фото
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("replace_photo|"))
async def replace_photo(callback: CallbackQuery, state: FSMContext):
    post = await _validate_post(callback)
    if not post:
        return
    await state.set_state(PostEditStates.waiting_image)
    await state.update_data(new_media=[])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data=f"done_replace_media|{post['id']}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_replace_photo|{post['id']}")]
    ])
    await callback.message.edit_text(
        "📸 Отправьте фото и/или видео (можно несколько).\n"
        "Когда закончите — нажмите <b>Готово</b>.",
        parse_mode="HTML",
        reply_markup=kb
    )
    await callback.answer()


@router.message(PostEditStates.waiting_image, F.photo)
async def handle_photo_for_post(message: Message, state: FSMContext):
    """Сбор фото в альбом"""
    data = await state.get_data()
    media = data.get('new_media', [])
    media.append(message.photo[-1].file_id)  # строка = фото
    await state.update_data(new_media=media)
    
    await message.delete()
    count = len(media)
    sent = await message.answer(f"📎 Добавлено медиа: {count}. Отправьте ещё или нажмите Готово.")
    # Автоудаление счётчика через 3 сек
    asyncio.create_task(delete_message(sent, 3))


@router.message(PostEditStates.waiting_image, F.video)
async def handle_video_for_post(message: Message, state: FSMContext):
    """Сбор видео в альбом"""
    data = await state.get_data()
    media = data.get('new_media', [])
    media.append({"type": "video", "id": message.video.file_id})
    await state.update_data(new_media=media)
    
    await message.delete()
    count = len(media)
    sent = await message.answer(f"📎 Добавлено медиа: {count}. Отправьте ещё или нажмите Готово.")
    asyncio.create_task(delete_message(sent, 3))


@router.callback_query(F.data.startswith("done_replace_media|"))
async def done_replace_media(callback: CallbackQuery, state: FSMContext):
    """Завершение сбора медиа"""
    post = await _validate_post(callback)
    if not post:
        await state.clear()
        return
    
    data = await state.get_data()
    media = data.get('new_media', [])
    await state.clear()
    
    if not media:
        await callback.answer("Вы не отправили ни одного фото/видео", show_alert=True)
        return
    
    user_id = callback.from_user.id
    post['media_urls'] = json.dumps(media)
    post['image_url'] = media[0] if isinstance(media[0], str) else media[0].get('id')
    user_current_post[user_id] = post

    count_photo = sum(1 for m in media if isinstance(m, str))
    count_video = sum(1 for m in media if isinstance(m, dict))
    info = f"🖼 {count_photo} фото" if count_photo else ""
    if count_video:
        info += (", " if info else "") + f"🎬 {count_video} видео"
    
    preview_text = post['text'][:3000]
    if len(post['text']) > 3000:
        preview_text += "..."
    
    await callback.message.edit_text(
        f"📝 <b>Медиа обновлено!</b> ({info})\n\n{preview_text}\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=_preview_kb(post['id'], has_image=bool(post.get('image_url') or post.get('media_urls')))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_replace_photo|"))
async def cancel_replace_photo(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    post = user_current_post.get(user_id)
    
    if post:
        preview_text = post['text'][:3000]
        if len(post['text']) > 3000:
            preview_text += "..."
        
        await callback.message.edit_text(
            f"📝 <b>Предпросмотр поста</b>\n\n{preview_text}\n\n"
            f"Выберите действие:",
            parse_mode="HTML",
            reply_markup=_preview_kb(post['id'], has_image=bool(post.get('image_url') or post.get('media_urls')))
        )
    else:
        await callback.message.edit_text("❌ Замена фото отменена.")
    
    await callback.answer()

# ----------------------------------------------------------------------
# Выбор города для публикации
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("choose_publish_city|"))
async def choose_publish_city(callback: CallbackQuery):
    post = await _validate_post(callback)
    if not post:
        return
    folders = db.get_folders()
    if not folders:
        await callback.message.edit_text("📭 Нет добавленных городов. Сначала создайте город через /cities.")
        await callback.answer()
        return
    builder = InlineKeyboardBuilder()
    for folder in folders:
        builder.button(text=folder['name'], callback_data=f"pub_city_{folder['id']}|{post['id']}")
    builder.button(text="❌ Отмена", callback_data=f"cancel_publish|{post['id']}")
    builder.adjust(2)
    await callback.message.edit_text("🏙 Выберите город, в каналы которого хотите опубликовать пост:", reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("pub_city_"))
async def select_publish_city(callback: CallbackQuery):
    # pub_city_{folder_id}|{post_id}
    parts = callback.data.split("|")
    folder_id = int(parts[0].split("_")[2])
    post = await _validate_post(callback)
    if not post:
        return
    user_id = callback.from_user.id
    user_selected_folder_for_publish[user_id] = folder_id
    user_selected_channels[user_id] = set()  # Очищаем старые выборы
    channels = db.get_publish_channels_by_folder(folder_id)
    if not channels:
        await callback.message.edit_text(f"📭 В выбранном городе нет каналов для публикации. Добавьте через /add_channel.")
        await callback.answer()
        return
    builder = InlineKeyboardBuilder()
    for ch in channels:
        builder.button(
            text=f"⬜ {ch['channel_name']} ({ch['channel_username']})",
            callback_data=f"pub_ch|{ch['channel_id']}|{post['id']}"
        )
    builder.button(text="✅ Готово", callback_data=f"pub_ch_done|{post['id']}")
    builder.button(text="❌ Отмена", callback_data=f"cancel_publish|{post['id']}")
    builder.adjust(1)
    await callback.message.edit_text("Выберите каналы для публикации (можно несколько):", reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("pub_ch|"))
async def publish_toggle_channel(callback: CallbackQuery):
    # pub_ch|{channel_id}|{post_id}
    parts = callback.data.split("|")
    channel_id = parts[1]
    post = await _validate_post(callback)
    if not post:
        return
    user_id = callback.from_user.id
    selected = user_selected_channels.get(user_id, set())
    if channel_id in selected:
        selected.remove(channel_id)
    else:
        selected.add(channel_id)
    user_selected_channels[user_id] = selected

    folder_id = user_selected_folder_for_publish.get(user_id)
    if not folder_id:
        await callback.answer("Ошибка: город не выбран", show_alert=True)
        return
    channels = db.get_publish_channels_by_folder(folder_id)
    builder = InlineKeyboardBuilder()
    for ch in channels:
        mark = "✅" if ch['channel_id'] in selected else "⬜"
        builder.button(
            text=f"{mark} {ch['channel_name']} ({ch['channel_username']})",
            callback_data=f"pub_ch|{ch['channel_id']}|{post['id']}"
        )
    builder.button(text="✅ Готово", callback_data=f"pub_ch_done|{post['id']}")
    builder.button(text="❌ Отмена", callback_data=f"cancel_publish|{post['id']}")
    builder.adjust(1)
    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("pub_ch_done|"))
async def publish_channels_selected(callback: CallbackQuery):
    post = await _validate_post(callback)
    if not post:
        return
    user_id = callback.from_user.id
    selected = user_selected_channels.get(user_id, set())
    if not selected:
        await callback.answer("Выберите хотя бы один канал", show_alert=True)
        return
    
    # Показываем текущую подпись если есть
    custom_sig = user_custom_signature.get(user_id)
    if custom_sig:
        sig_text = f"\n\n📝 Подпись: {custom_sig[:50]}{'...' if len(custom_sig) > 50 else ''}"
    else:
        sig_text = "\n\n📝 Подпись: стандартная (из настроек канала)"
    
    kb = _publish_kb(post['id'])
    await callback.message.edit_text(f"Выберите действие:{sig_text}", reply_markup=kb)
    await callback.answer()


# ----------------------------------------------------------------------
# Изменение подписи перед публикацией
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("edit_post_signature|"))
async def edit_post_signature(callback: CallbackQuery, state: FSMContext):
    post = await _validate_post(callback)
    if not post:
        return
    user_id = callback.from_user.id
    current_sig = user_custom_signature.get(user_id, "")
    
    await state.set_state(PostEditStates.waiting_signature)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Использовать стандартную", callback_data=f"use_default_sig|{post['id']}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_edit_sig|{post['id']}")]
    ])
    
    if current_sig:
        text = f"📝 Текущая подпись:\n{current_sig}\n\n✍️ Введите новую подпись:"
    else:
        text = "✍️ Введите подпись для этого поста.\n\n💡 Можно использовать форматирование и ссылки."
    
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.message(PostEditStates.waiting_signature)
async def receive_post_signature(message: Message, state: FSMContext):
    """Получение подписи для поста"""
    user_id = message.from_user.id
    signature = message.html_text.strip() if message.html_text else message.text.strip()
    
    await state.clear()
    user_custom_signature[user_id] = signature
    
    post = user_current_post.get(user_id)
    post_id = post['id'] if post else 0
    
    sig_preview = signature[:50] + "..." if len(signature) > 50 else signature
    
    kb = _publish_kb(post_id)
    await message.answer(
        f"✅ Подпись установлена!\n\n📝 Подпись: {sig_preview}\n\nВыберите действие:",
        reply_markup=kb
    )
    await message.delete()


@router.callback_query(F.data.startswith("use_default_sig|"))
async def use_default_signature(callback: CallbackQuery, state: FSMContext):
    """Использовать стандартную подпись из настроек канала"""
    post = await _validate_post(callback)
    if not post:
        return
    user_id = callback.from_user.id
    await state.clear()
    user_custom_signature.pop(user_id, None)
    
    kb = _publish_kb(post['id'])
    await callback.message.edit_text(
        "✅ Будет использована стандартная подпись из настроек канала.\n\nВыберите действие:",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_edit_sig|"))
async def cancel_edit_signature(callback: CallbackQuery, state: FSMContext):
    """Отмена редактирования подписи"""
    post = await _validate_post(callback)
    if not post:
        return
    user_id = callback.from_user.id
    await state.clear()
    
    custom_sig = user_custom_signature.get(user_id)
    if custom_sig:
        sig_text = f"\n\n📝 Подпись: {custom_sig[:50]}{'...' if len(custom_sig) > 50 else ''}"
    else:
        sig_text = "\n\n📝 Подпись: стандартная (из настроек канала)"
    
    kb = _publish_kb(post['id'])
    await callback.message.edit_text(f"Выберите действие:{sig_text}", reply_markup=kb)
    await callback.answer()


# ----------------------------------------------------------------------
# Публикация сейчас
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("publish_now|"))
async def publish_now(callback: CallbackQuery):
    post = await _validate_post(callback)
    if not post:
        return
    user_id = callback.from_user.id
    selected_channel_ids = user_selected_channels.get(user_id, set())
    if not selected_channel_ids:
        await callback.answer("Ошибка: каналы не выбраны", show_alert=True)
        return

    bot = get_bot()
    success = []
    errors = []
    
    # Удаляем все старые сообщения
    await _cleanup_all(bot, callback.message.chat.id, user_id,
                       except_msg_id=callback.message.message_id)
    
    custom_sig = user_custom_signature.get(user_id)

    for channel_id in selected_channel_ids:
        if custom_sig is not None:
            signature = custom_sig
        else:
            signature = db.get_publish_channel_signature(channel_id)

        ok = await send_post(bot, channel_id, post, signature=signature, db=db)
        if ok:
            success.append(channel_id)
            db.mark_as_posted(post['id'])
            db.add_to_history(post['id'], channel_id)
        else:
            errors.append(str(channel_id))

    # Очистка (folder_id оставляем для "Вернуться к постам")
    user_current_post.pop(user_id, None)
    user_selected_channels.pop(user_id, None)
    user_edited_text.pop(user_id, None)
    user_custom_signature.pop(user_id, None)
    folder_id = user_selected_folder_for_publish.get(user_id)

    if folder_id:
        new_posts = db.get_posts(folder_id=folder_id, only_new=True, limit=200)
        if new_posts:
            new_posts.sort(key=lambda p: p['published_at'] or datetime.min, reverse=True)
            new_posts = new_posts[:100]
            user_posts_cache[user_id] = new_posts
            user_pages[user_id] = 0
        else:
            user_posts_cache.pop(user_id, None)
            user_pages.pop(user_id, None)

    msg = f"✅ Опубликовано в {len(success)} каналов."
    if errors:
        msg += f"\n❌ Ошибки: {', '.join(errors)}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Вернуться к постам", callback_data="back_to_posts_after_publish")]
    ])
    await callback.message.edit_text(msg, reply_markup=kb)
    await callback.answer()

# ----------------------------------------------------------------------
# Возврат к постам и отмена
# ----------------------------------------------------------------------
@router.callback_query(F.data == "back_to_posts_after_publish")
async def back_to_posts_after_publish(callback: CallbackQuery):
    user_id = callback.from_user.id
    folder_id = user_selected_folder_for_publish.get(user_id)
    posts = []
    if folder_id:
        posts = db.get_posts(folder_id=folder_id, only_new=True, limit=200)
        if posts:
            posts.sort(key=lambda p: p['published_at'] or datetime.min, reverse=True)
            posts = posts[:100]
            user_posts_cache[user_id] = posts
            user_pages[user_id] = 0
    if not posts:
        posts = user_posts_cache.get(user_id, [])
    if not posts:
        await callback.message.edit_text("📭 Нет новых постов.", reply_markup=_restart_kb())
        await callback.answer()
        return
    text, markup = await get_posts_page_text_and_markup(user_id, 0)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()

@router.callback_query(F.data.startswith("cancel_publish|"))
async def cancel_publish(callback: CallbackQuery):
    user_id = callback.from_user.id
    bot = get_bot()
    await _cleanup_all(bot, callback.message.chat.id, user_id,
                       except_msg_id=callback.message.message_id)
    page = user_pages.get(user_id, 0)
    user_current_post.pop(user_id, None)
    user_selected_channels.pop(user_id, None)
    user_edited_text.pop(user_id, None)
    user_schedule_data.pop(user_id, None)
    user_selected_folder_for_publish.pop(user_id, None)
    await callback.answer("❌ Публикация отменена.")
    # Возвращаем на текущую страницу
    text, markup = await get_posts_page_text_and_markup(user_id, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)

@router.callback_query(F.data == "cancel_posts")
async def cancel_posts(callback: CallbackQuery):
    user_id = callback.from_user.id
    bot = get_bot()
    await _cleanup_all(bot, callback.message.chat.id, user_id)
    user_posts_cache.pop(user_id, None)
    user_pages.pop(user_id, None)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


# ----------------------------------------------------------------------
# Перезапуск /posts по кнопке (из тупиков)
# ----------------------------------------------------------------------
@router.callback_query(F.data == "restart_posts")
async def restart_posts(callback: CallbackQuery):
    """Эмулирует /posts — показывает выбор города"""
    user_id = callback.from_user.id
    bot = get_bot()
    await _cleanup_all(bot, callback.message.chat.id, user_id,
                       except_msg_id=callback.message.message_id)
    folders = db.get_folders()
    if not folders:
        await callback.message.edit_text("📭 Нет добавленных городов.")
        await callback.answer()
        return
    builder = InlineKeyboardBuilder()
    for folder in folders:
        builder.button(text=folder['name'], callback_data=f"posts_city_{folder['id']}")
    builder.button(text="❌ Отмена", callback_data="cancel_posts")
    builder.adjust(1)
    await callback.message.edit_text("🏙 Выберите город для просмотра постов:", reply_markup=builder.as_markup())
    user_list_msg_id[user_id] = callback.message.message_id
    await callback.answer()


# ----------------------------------------------------------------------
# Ловушки для старых кнопок (без |post_id) — от сообщений до обновления
# ----------------------------------------------------------------------

@router.callback_query(F.data.startswith("remove_watermark|"))
async def remove_watermark_handler(callback: CallbackQuery):
    """Удаляет водяной знак с фото поста."""
    from utils.watermark_remover import remove_watermark
    post_id = int(callback.data.split("|")[1])
    user_id = callback.from_user.id
    post = user_current_post.get(user_id)
    if not post:
        await callback.answer("❌ Пост не найден.", show_alert=True)
        return
    image_path = post.get('image_url')
    if not image_path:
        await callback.answer("❌ У поста нет фото.", show_alert=True)
        return
    await callback.answer("⏳ Обрабатываю...")
    result = await remove_watermark(image_path)
    if not result:
        await callback.message.answer("❌ Не удалось убрать водяной знак. Попробуйте позже.")
        return
    if result == image_path:
        await callback.message.answer("ℹ️ Водяной знак не обнаружен на фото.")
        return
    # Обновляем путь в посте
    post['image_url'] = result
    user_current_post[user_id] = post
    db.update_post_image(post_id, result)
    from aiogram.types import FSInputFile
    await callback.message.answer_photo(
        FSInputFile(result),
        caption="✅ Водяной знак убран! Фото обновлено. Проверьте качество.",
    )


@router.callback_query(F.data.startswith("next_post|"))
async def next_post_handler(callback: CallbackQuery):
    """Переход к следующему посту без возврата к списку."""
    user_id = callback.from_user.id
    page = user_pages.get(user_id, 0)
    next_page = page + 1
    posts = user_posts_cache.get(user_id, [])
    if not posts:
        await callback.answer("⚠️ Кэш постов пуст. Нажмите /posts заново.", show_alert=True)
        return
    # Считаем сколько постов на странице (из get_posts_page_text_and_markup)
    POSTS_PER_PAGE = 10
    start = next_page * POSTS_PER_PAGE
    if start >= len(posts):
        await callback.answer("✅ Это последний пост.", show_alert=True)
        return
    user_pages[user_id] = next_page
    # Очищаем текущий превью
    bot = get_bot()
    await _cleanup_all(bot, callback.message.chat.id, user_id,
                       except_msg_id=callback.message.message_id)
    user_current_post.pop(user_id, None)
    user_selected_channels.pop(user_id, None)
    user_edited_text.pop(user_id, None)
    user_schedule_data.pop(user_id, None)
    # Показываем список следующей страницы
    text, markup = await get_posts_page_text_and_markup(user_id, next_page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await callback.answer()

_OLD_CALLBACKS = {
    "publish_now", "schedule_post", "edit_post_text", "replace_photo",
    "choose_publish_city", "cancel_publish", "edit_post_signature",
    "use_default_signature", "cancel_edit_signature", "cancel_edit_text",
    "cancel_replace_photo", "publish_channels_selected", "cancel_schedule"
}

@router.callback_query(F.data.in_(_OLD_CALLBACKS))
async def catch_old_buttons(callback: CallbackQuery):
    """Перехватывает нажатия на кнопки от старых сообщений (до обновления)"""
    await callback.answer(
        "⚠️ Эта кнопка устарела. Нажмите /posts заново.",
        show_alert=True
    )

@router.callback_query(F.data.startswith("publish_toggle_channel|"))
async def catch_old_toggle(callback: CallbackQuery):
    """Старые кнопки выбора каналов"""
    await callback.answer(
        "⚠️ Эта кнопка устарела. Нажмите /posts заново.",
        show_alert=True
    )