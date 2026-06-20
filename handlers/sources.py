import asyncio
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database
from state import SourceStates
from utils.delete_utils import delete_message

router = Router()
db = Database()


# ----------------------------------------------------------------------
# Команда /sources – список городов
# ----------------------------------------------------------------------
@router.message(Command("sources"))
async def cmd_sources(message: Message):
    folders = db.get_folders()
    if not folders:
        await message.answer("📭 Нет добавленных городов. Сначала создайте город через /cities.")
        try:
            await message.delete()
        except Exception:
            pass
        return
    builder = InlineKeyboardBuilder()
    for folder in folders:
        builder.button(text=folder['name'], callback_data=f"src_list_{folder['id']}")
    builder.button(text="❌ Отмена", callback_data="cancel_sources")
    builder.adjust(2)
    await message.answer("🏙 Выберите город, чтобы просмотреть его источники:", reply_markup=builder.as_markup())
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("src_list_"))
async def show_sources(callback: CallbackQuery):
    folder_id = int(callback.data.split("_")[2])
    sources = db.get_sources_by_folder(folder_id)
    folder = db.get_folder_by_id(folder_id)
    
    builder = InlineKeyboardBuilder()
    
    if sources:
        for src in sources:
            type_icon = {"vk": "VK", "telegram": "TG", "rss": "RSS"}.get(src['type'], src['type'])
            builder.button(text=f"{type_icon}: {src['name']}", callback_data=f"src_action_{src['id']}")
    
    # Кнопка добавления всегда показывается
    builder.button(text="➕ Добавить источник", callback_data=f"addsrc_city_{folder_id}")
    builder.button(text="◀ Назад", callback_data="back_to_cities_sources")
    builder.adjust(1)
    
    if sources:
        text = f"📡 Источники города «{folder['name']}»:"
    else:
        text = f"📭 В городе «{folder['name']}» нет источников.\n\nНажмите «➕ Добавить источник»:"
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "back_to_cities_sources")
async def back_to_cities_sources(callback: CallbackQuery):
    folders = db.get_folders()
    if not folders:
        await callback.message.edit_text("📭 Нет добавленных городов.")
        await callback.answer()
        return
    builder = InlineKeyboardBuilder()
    for folder in folders:
        builder.button(text=folder['name'], callback_data=f"src_list_{folder['id']}")
    builder.button(text="❌ Отмена", callback_data="cancel_sources")
    builder.adjust(1)
    await callback.message.edit_text("🏙 Выберите город:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("src_action_"))
async def source_action_menu(callback: CallbackQuery):
    source_id = int(callback.data.split("_")[2])
    src = None
    for s in db.get_all_active_sources():
        if s['id'] == source_id:
            src = s
            break
    if not src:
        await callback.answer("Источник не найден", show_alert=True)
        return
    folder = db.get_folder_by_id(src['folder_id'])
    type_icon = {"vk": "VK", "telegram": "TG", "rss": "RSS"}.get(src['type'], src['type'])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить источник", callback_data=f"del_source_{source_id}")],
        [InlineKeyboardButton(text="◀ Назад", callback_data=f"src_list_{src['folder_id']}")]
    ])
    await callback.message.edit_text(
        f"📡 Источник: {src['name']}\n"
        f"Тип: {type_icon}\n"
        f"Значение: {src['value']}\n"
        f"Город: {folder['name']}",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("del_source_"))
async def confirm_delete_source(callback: CallbackQuery):
    source_id = int(callback.data.split("_")[2])
    src = None
    for s in db.get_all_active_sources():
        if s['id'] == source_id:
            src = s
            break
    if not src:
        await callback.answer("Источник не найден", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_del_source_{source_id}")],
        [InlineKeyboardButton(text="❌ Нет", callback_data=f"src_action_{source_id}")]
    ])
    await callback.message.edit_text(f"⚠️ Удалить источник «{src['name']}»?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_source_"))
async def delete_source(callback: CallbackQuery):
    source_id = int(callback.data.split("_")[3])
    db.delete_source(source_id)
    await callback.message.edit_text("✅ Источник удалён.")
    await callback.answer()


@router.callback_query(F.data == "cancel_sources")
async def cancel_sources(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await callback.answer()


# ----------------------------------------------------------------------
# Команда /add_source — добавление источника с FSM
# ----------------------------------------------------------------------
@router.message(Command("add_source"))
async def cmd_add_source(message: Message, state: FSMContext):
    await state.clear()  # Очищаем предыдущее состояние
    folders = db.get_folders()
    if not folders:
        await message.answer("📭 Нет добавленных городов. Сначала создайте город через /cities.")
        try:
            await message.delete()
        except Exception:
            pass
        return
    builder = InlineKeyboardBuilder()
    for folder in folders:
        builder.button(text=folder['name'], callback_data=f"addsrc_city_{folder['id']}")
    builder.button(text="❌ Отмена", callback_data="cancel_add_source")
    builder.adjust(1)
    await message.answer("🏙 Выберите город, в который добавить источник:", reply_markup=builder.as_markup())
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("addsrc_city_"))
async def select_source_type(callback: CallbackQuery, state: FSMContext):
    folder_id = int(callback.data.split("_")[2])
    await state.update_data(folder_id=folder_id)
    await state.set_state(SourceStates.waiting_type)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="VK группа", callback_data="src_type_vk")],
        [InlineKeyboardButton(text="Telegram канал", callback_data="src_type_telegram")],
        [InlineKeyboardButton(text="RSS лента", callback_data="src_type_rss")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_source")]
    ])
    await callback.message.edit_text("📡 Выберите тип источника:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("src_type_"), SourceStates.waiting_type)
async def ask_source_value(callback: CallbackQuery, state: FSMContext):
    source_type = callback.data.split("_")[2]
    await state.update_data(source_type=source_type)
    await state.set_state(SourceStates.waiting_value)
    
    if source_type == 'vk':
        hint = "Введите ID или короткий адрес группы VK\n\nПример: club215921691 или novgorod_life"
    elif source_type == 'telegram':
        hint = "Введите username канала или ссылку\n\nПример: @novgorodtop или https://t.me/novgorodtop"
    else:
        hint = "Введите URL RSS-ленты\n\nПример: https://news.yandex.ru/Novgorod.rss"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_source")]
    ])
    await callback.message.edit_text(f"🔗 {hint}", reply_markup=kb)
    await callback.answer()


@router.message(SourceStates.waiting_value)
async def receive_source_value(message: Message, state: FSMContext):
    """Получение значения источника — срабатывает ТОЛЬКО в состоянии waiting_value"""
    value = message.text.strip()
    if not value:
        await message.answer("❌ Значение не может быть пустым. Попробуйте ещё раз:")
        return
    
    data = await state.get_data()
    source_type = data.get('source_type')
    folder_id = data.get('folder_id')
    
    # Очищаем состояние
    await state.clear()
    
    # Добавляем источник
    name = value
    db.add_source(folder_id, source_type, value, name)
    folder = db.get_folder_by_id(folder_id)
    
    type_names = {"vk": "VK", "telegram": "Telegram", "rss": "RSS"}
    type_name = type_names.get(source_type, source_type)
    
    confirm = await message.answer(
        f"✅ Источник добавлен!\n\n"
        f"📁 Город: {folder['name']}\n"
        f"📡 Тип: {type_name}\n"
        f"🔗 Значение: {value}"
    )
    try:
        await message.delete()
    except Exception:
        pass
    asyncio.create_task(delete_message(confirm, 15))


@router.callback_query(F.data == "cancel_add_source")
async def cancel_add_source(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    from handlers.sources import cmd_sources
    await cmd_sources(callback.message)
