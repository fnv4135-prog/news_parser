import asyncio
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database
from state import ScheduleEditStates
from utils.delete_utils import delete_message

router = Router()
db = Database()

# Временное хранение выбранного поста для редактирования
user_editing_scheduled: dict = {}


@router.message(Command("scheduled"))
async def cmd_scheduled(message: Message, state: FSMContext):
    await state.clear()
    
    # Получаем статистику по городам
    folder_counts = db.get_scheduled_count_by_folder()
    total = sum(folder_counts.values())
    
    if total == 0:
        sent = await message.answer("📭 Нет запланированных постов.")
        asyncio.create_task(delete_message(sent, 30))
        try:
            await message.delete()
        except Exception:
            pass
        return
    
    # Если только один город — сразу показываем посты
    if len(folder_counts) == 1:
        folder_id = list(folder_counts.keys())[0]
        await show_scheduled_list(message, folder_id, is_message=True)
        return
    
    # Несколько городов — показываем выбор
    builder = InlineKeyboardBuilder()
    folders = db.get_folders()
    folder_names = {f['id']: f['name'] for f in folders}
    
    for folder_id, count in folder_counts.items():
        name = folder_names.get(folder_id, f"Город #{folder_id}")
        builder.button(text=f"🏙 {name} ({count})", callback_data=f"sched_city|{folder_id}")
    
    builder.button(text=f"📋 Все ({total})", callback_data="sched_city|all")
    builder.button(text="❌ Закрыть", callback_data="close_scheduled_list")
    builder.adjust(1)
    
    await message.answer(
        f"📋 Запланированные посты ({total}):\n\n"
        "Выберите город:",
        reply_markup=builder.as_markup()
    )
    try:
        await message.delete()
    except Exception:
        pass


async def show_scheduled_list(target, folder_id, is_message=False):
    """Показать список постов для города"""
    if folder_id == "all":
        scheduled = db.get_all_scheduled()
        title = "Все города"
    else:
        scheduled = db.get_scheduled_by_folder(int(folder_id))
        folder = db.get_folder_by_id(int(folder_id))
        title = folder['name'] if folder else f"Город #{folder_id}"
    
    if not scheduled:
        text = "📭 Нет запланированных постов."
        if is_message:
            await target.answer(text)
            await target.delete()
        else:
            await target.message.edit_text(text)
        return
    
    builder = InlineKeyboardBuilder()
    for sch in scheduled:
        sched_time = sch['scheduled_at']
        if isinstance(sched_time, str):
            sched_time = datetime.strptime(sched_time[:16], "%Y-%m-%d %H:%M")
        sched_time_msk = sched_time + timedelta(hours=3)
        time_str = sched_time_msk.strftime("%H:%M")
        date_str = sched_time_msk.strftime("%m-%d")
        
        if sch.get('is_ad'):
            text_preview = (sch['text'][:20] + "...") if len(sch['text']) > 20 else sch['text']
            button_text = f"📢 {date_str} {time_str} | {text_preview}"
        else:
            post = db.get_post_by_id(sch['post_id'])
            text_preview = post['text'][:20] if post else "Пост"
            button_text = f"📰 {date_str} {time_str} | {text_preview}..."
        builder.button(text=button_text, callback_data=f"view_scheduled|{sch['id']}")
    
    builder.button(text="◀ Назад", callback_data="sched_back_to_cities")
    builder.button(text="❌ Закрыть", callback_data="close_scheduled_list")
    builder.adjust(1)
    
    text = f"📋 {title} — запланировано ({len(scheduled)}):\n\nНажмите на пост для управления."
    
    if is_message:
        await target.answer(text, reply_markup=builder.as_markup())
        await target.delete()
    else:
        await target.message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("sched_city|"))
async def select_scheduled_city(callback: CallbackQuery):
    folder_id = callback.data.split("|")[1]
    await show_scheduled_list(callback, folder_id)
    await callback.answer()


@router.callback_query(F.data.startswith("view_scheduled|"))
async def view_scheduled_post(callback: CallbackQuery):
    """Показать меню для отложенного поста"""
    scheduled_id = int(callback.data.split("|")[1])
    user_id = callback.from_user.id
    
    # Получаем пост из БД
    scheduled = db.get_scheduled_by_id(scheduled_id)
    if not scheduled:
        await callback.answer("Пост не найден", show_alert=True)
        return
    
    user_editing_scheduled[user_id] = scheduled_id
    
    sched_time = scheduled['scheduled_at']
    if isinstance(sched_time, str):
        sched_time = datetime.strptime(sched_time[:16], "%Y-%m-%d %H:%M")
    sched_time_msk = sched_time + timedelta(hours=3)
    time_str = sched_time_msk.strftime("%Y-%m-%d %H:%M")
    
    text_preview = scheduled['text'][:200] if scheduled['text'] else "Без текста"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕒 Изменить время", callback_data=f"edit_sched_time|{scheduled_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_delete_sched|{scheduled_id}")],
        [InlineKeyboardButton(text="◀ Назад", callback_data="back_to_sched_list")]
    ])
    
    await callback.message.edit_text(
        f"📋 Отложенный пост\n\n"
        f"📅 Время: {time_str}\n"
        f"📢 Каналов: {len(scheduled['channel_ids'])}\n\n"
        f"📝 Текст:\n{text_preview}{'...' if len(scheduled['text'] or '') > 200 else ''}",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_sched_time|"))
async def edit_scheduled_time(callback: CallbackQuery, state: FSMContext):
    """Запросить новое время"""
    scheduled_id = int(callback.data.split("|")[1])
    
    await state.update_data(editing_scheduled_id=scheduled_id)
    await state.set_state(ScheduleEditStates.waiting_new_time)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit_sched_time")]
    ])
    
    await callback.message.edit_text(
        "⏰ Введите новое время публикации:\n\n"
        "Формат: `ГГГГ-ММ-ДД ЧЧ:ММ`\n\n"
        "Пример: `2026-04-11 15:30`\n\n"
        "Время московское (UTC+3).",
        parse_mode="Markdown",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_edit_sched_time")
async def cancel_edit_sched_time(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Редактирование отменено.")
    asyncio.create_task(delete_message(callback.message, 3))
    await callback.answer()


@router.message(ScheduleEditStates.waiting_new_time)
async def handle_new_scheduled_time(message: Message, state: FSMContext):
    """Обработка нового времени для отложенного поста"""
    text = message.text.strip()
    
    try:
        new_time = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте: ГГГГ-ММ-ДД ЧЧ:ММ\n\nПример: 2026-04-11 15:30")
        return
    
    now_msk = datetime.utcnow() + timedelta(hours=3)
    if new_time < now_msk:
        await message.answer("❌ Время должно быть в будущем.")
        return
    
    new_time_utc = new_time - timedelta(hours=3)
    
    data = await state.get_data()
    scheduled_id = data.get('editing_scheduled_id')
    
    if scheduled_id:
        db.update_scheduled_time(scheduled_id, new_time_utc)
        await message.answer(f"✅ Время изменено на {new_time.strftime('%Y-%m-%d %H:%M')} (МСК)")
    else:
        await message.answer("❌ Ошибка: пост не найден")
    
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("confirm_delete_sched|"))
async def confirm_delete_scheduled(callback: CallbackQuery):
    """Подтверждение удаления"""
    scheduled_id = int(callback.data.split("|")[1])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delete_scheduled|{scheduled_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_sched_list")]
    ])
    
    await callback.message.edit_text(
        "⚠️ Удалить этот пост из расписания?",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_scheduled|"))
async def delete_scheduled(callback: CallbackQuery):
    """Удалить пост"""
    scheduled_id = int(callback.data.split("|")[1])
    db.cancel_scheduled(scheduled_id)
    await callback.message.edit_text("✅ Пост удалён из расписания.")
    asyncio.create_task(delete_message(callback.message, 3))
    await callback.answer()


@router.callback_query(F.data == "back_to_sched_list")
async def back_to_sched_list(callback: CallbackQuery):
    """Вернуться к списку отложенных"""
    folder_counts = db.get_scheduled_count_by_folder()
    
    if not folder_counts:
        await callback.message.edit_text("📭 Нет запланированных постов.")
        await callback.answer()
        return
    
    # Показываем все посты
    await show_scheduled_list(callback, "all")
    await callback.answer()


@router.callback_query(F.data == "sched_back_to_cities")
async def back_to_scheduled_cities(callback: CallbackQuery):
    folder_counts = db.get_scheduled_count_by_folder()
    total = sum(folder_counts.values())
    
    if total == 0:
        await callback.message.edit_text("📭 Нет запланированных постов.")
        await callback.answer()
        return
    
    builder = InlineKeyboardBuilder()
    folders = db.get_folders()
    folder_names = {f['id']: f['name'] for f in folders}
    
    for folder_id, count in folder_counts.items():
        name = folder_names.get(folder_id, f"Город #{folder_id}")
        builder.button(text=f"🏙 {name} ({count})", callback_data=f"sched_city|{folder_id}")
    
    builder.button(text=f"📋 Все ({total})", callback_data="sched_city|all")
    builder.button(text="❌ Закрыть", callback_data="close_scheduled_list")
    builder.adjust(1)
    
    await callback.message.edit_text(
        f"📋 Запланированные посты ({total}):\n\nВыберите город:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "close_scheduled_list")
async def close_scheduled_list(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()