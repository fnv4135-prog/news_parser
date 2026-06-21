import logging
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database import Database
from utils.text_cleaner import clean_text
from state import (
    PostEditStates,
    user_current_post, user_edited_text, user_selected_channels,
    user_selected_folder_for_publish, user_posts_cache, user_pages
)

router = Router()
db = Database()


def _preview_kb(post_id: int) -> InlineKeyboardMarkup:
    """Кнопки предпросмотра поста (дубль из posts.py — избегаем circular import)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать текст", callback_data=f"edit_post_text|{post_id}")],
        [InlineKeyboardButton(text="🖼 Заменить фото/видео", callback_data=f"replace_photo|{post_id}")],
        [InlineKeyboardButton(text="📢 Выбрать город для публикации", callback_data=f"choose_publish_city|{post_id}")],
        [InlineKeyboardButton(text="◀ К списку", callback_data="back_to_posts_list")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_publish|{post_id}")]
    ])


# ----------------------------------------------------------------------
# Редактирование текста
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("edit_post_text|"))
async def edit_post_text(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    post = user_current_post.get(user_id)
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return
    
    await state.set_state(PostEditStates.waiting_text)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_edit_text|{post['id']}")]
    ])
    
    current_text = post['text'][:2000]
    if len(post['text']) > 2000:
        current_text += "..."
    
    await callback.message.edit_text(
        f"✍️ Введите новый текст поста.\n\n"
        f"Текущий текст:\n{current_text}",
        reply_markup=kb
    )
    await callback.answer()


@router.message(PostEditStates.waiting_text)
async def handle_edit_text(message: Message, state: FSMContext):
    """Получение нового текста — срабатывает ТОЛЬКО в состоянии waiting_text"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    if not text:
        await message.answer("❌ Текст не может быть пустым. Попробуйте ещё раз:")
        return
    
    post = user_current_post.get(user_id)
    if not post:
        await state.clear()
        await message.answer("❌ Пост не найден. Начните заново через /posts")
        return
    
    data = await state.get_data()
    from_urgent = data.get('from_urgent', False)
    urgent_index = data.get('urgent_index', 0)
    await state.clear()

    post['text'] = text
    user_edited_text[user_id] = text
    user_current_post[user_id] = post

    try:
        await message.delete()
    except Exception:
        pass

    if from_urgent:
        from handlers.urgent import show_urgent_post
        await show_urgent_post(message, index=urgent_index)
    else:
        preview_text = text[:500]
        if len(text) > 500:
            preview_text += "..."
        await message.answer(
            f"📝 <b>Текст обновлён!</b>\n\n{preview_text}\n\n"
            f"Выберите действие:",
            parse_mode="HTML",
            reply_markup=_preview_kb(post['id'])
        )


@router.callback_query(F.data.startswith("cancel_edit_text|"))
async def cancel_edit_text(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    post = user_current_post.get(user_id)
    
    if post:
        preview_text = post['text'][:500]
        if len(post['text']) > 500:
            preview_text += "..."
        
        await callback.message.edit_text(
            f"📝 <b>Предпросмотр поста</b>\n\n{preview_text}\n\n"
            f"Выберите действие:",
            parse_mode="HTML",
            reply_markup=_preview_kb(post['id'])
        )
    else:
        await callback.message.edit_text("❌ Редактирование отменено.")
    
    await callback.answer()


# ----------------------------------------------------------------------
# Отложенная публикация (запрос даты)
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("schedule_post|"))
async def ask_schedule_time(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    post = user_current_post.get(user_id)
    selected_channel_ids = user_selected_channels.get(user_id, set())
    
    if not post or not selected_channel_ids:
        await callback.answer("Ошибка: пост или каналы не выбраны", show_alert=True)
        return
    
    # media_urls
    media_urls_raw = post.get('media_urls')
    
    # Сохраняем данные в FSM
    await state.update_data(
        post_id=post['id'],
        channel_ids=list(selected_channel_ids),
        edited_text=post['text'],
        image_url=post.get('image_url'),
        media_urls=media_urls_raw,
        folder_id=user_selected_folder_for_publish.get(user_id)
    )
    await state.set_state(PostEditStates.waiting_time)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_schedule|{post['id']}")]
    ])
    
    await callback.message.edit_text(
        "⏰ Введите дату и время публикации:\n\n"
        "Формат: `ГГГГ-ММ-ДД ЧЧ:ММ`\n\n"
        "Пример: `2026-04-10 15:30`\n\n"
        "Время московское (UTC+3).",
        parse_mode="Markdown",
        reply_markup=kb
    )
    await callback.answer()


@router.message(PostEditStates.waiting_time)
async def handle_schedule_time(message: Message, state: FSMContext):
    """Получение времени — срабатывает ТОЛЬКО в состоянии waiting_time"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    try:
        scheduled_time = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте: ГГГГ-ММ-ДД ЧЧ:ММ\n\nПример: 2026-04-10 15:30")
        return
    
    # Ростик вводит MSK (UTC+3), сервер в UTC — конвертируем
    now_msk = datetime.utcnow() + timedelta(hours=3)
    if scheduled_time < now_msk:
        await message.answer("❌ Дата и время должны быть в будущем. Попробуйте ещё раз:")
        return
    
    # Сохраняем в БД как UTC
    scheduled_time_utc = scheduled_time - timedelta(hours=3)
    
    data = await state.get_data()
    await state.clear()
    
    post_id = data['post_id']
    channel_ids = data['channel_ids']
    edited_text = data.get('edited_text')
    if edited_text:
        edited_text = clean_text(edited_text)
    image_url = data.get('image_url')
    media_urls = data.get('media_urls')
    folder_id = data.get('folder_id')
    
    for channel_id in channel_ids:
        signature = db.get_publish_channel_signature(channel_id)
        db.add_scheduled_post(
            post_id=post_id,
            channel_ids=[channel_id],
            scheduled_at=scheduled_time_utc,
            text=edited_text,
            image_url=image_url,
            signature=signature,
            folder_id=folder_id,
            media_list=media_urls
        )
    
    # Очистка временных данных
    user_current_post.pop(user_id, None)
    user_selected_channels.pop(user_id, None)
    user_edited_text.pop(user_id, None)
    user_selected_folder_for_publish.pop(user_id, None)
    
    # Обновляем кэш постов
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
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ К постам", callback_data="back_to_posts_list")]
    ])
    
    await message.answer(
        f"✅ Пост запланирован!\n\n"
        f"📅 Дата: {scheduled_time.strftime('%Y-%m-%d %H:%M')}\n"
        f"📢 Каналов: {len(channel_ids)}",
        reply_markup=kb
    )
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("cancel_schedule|"))
async def cancel_schedule(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    post = user_current_post.get(user_id)
    
    if post:
        from handlers.posts import _publish_kb
        kb = _publish_kb(post['id'])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀ К постам", callback_data="back_to_posts_list")]
        ])
    await callback.message.edit_text("Выберите действие:", reply_markup=kb)
    await callback.answer()
