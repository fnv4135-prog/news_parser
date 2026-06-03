import asyncio
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database
from state import FolderStates

router = Router()
db = Database()


# ----------------------------------------------------------------------
# Команда /cities
# ----------------------------------------------------------------------
@router.message(Command("cities"))
async def cmd_cities(message: Message, state: FSMContext):
    await state.clear()
    folders = db.get_folders()
    builder = InlineKeyboardBuilder()
    
    if folders:
        for folder in folders:
            builder.button(text=folder['name'], callback_data=f"city_{folder['id']}")
    
    builder.button(text="➕ Добавить город", callback_data="add_city")
    builder.button(text="❌ Закрыть", callback_data="cancel_cities")
    builder.adjust(2)
    
    if folders:
        await message.answer("🏙 Список городов:", reply_markup=builder.as_markup())
    else:
        await message.answer("📭 Нет добавленных городов.\n\nНажмите «➕ Добавить город»:", reply_markup=builder.as_markup())
    
    await message.delete()


@router.callback_query(F.data == "add_city")
async def add_city_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FolderStates.waiting_name)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_city")]
    ])
    await callback.message.edit_text("✍️ Введите название нового города:", reply_markup=kb)
    await callback.answer()


@router.message(FolderStates.waiting_name)
async def add_city_name(message: Message, state: FSMContext):
    """Получение названия города — срабатывает ТОЛЬКО в состоянии waiting_name"""
    name = message.text.strip()
    if not name:
        await message.answer("❌ Название не может быть пустым. Попробуйте ещё раз:")
        return
    
    await state.clear()
    db.add_folder(name)
    
    await message.answer(f"✅ Город «{name}» добавлен!")
    await message.delete()


@router.callback_query(F.data == "cancel_add_city")
async def cancel_add_city(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Добавление города отменено.")
    await callback.answer()


@router.callback_query(F.data.startswith("city_"))
async def city_actions(callback: CallbackQuery):
    city_id = int(callback.data.split("_")[1])
    city = db.get_folder_by_id(city_id)
    if not city:
        await callback.answer("Город не найден", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"edit_city_{city_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_city_{city_id}")],
        [InlineKeyboardButton(text="◀ Назад", callback_data="back_to_cities")]
    ])
    await callback.message.edit_text(f"🏙 Город: {city['name']}", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("edit_city_"))
async def edit_city_prompt(callback: CallbackQuery, state: FSMContext):
    city_id = int(callback.data.split("_")[2])
    await state.update_data(city_id=city_id)
    await state.set_state(FolderStates.waiting_edit_name)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit_city")]
    ])
    await callback.message.edit_text("✍️ Введите новое название города:", reply_markup=kb)
    await callback.answer()


@router.message(FolderStates.waiting_edit_name)
async def edit_city_name(message: Message, state: FSMContext):
    """Получение нового названия — срабатывает ТОЛЬКО в состоянии waiting_edit_name"""
    name = message.text.strip()
    if not name:
        await message.answer("❌ Название не может быть пустым. Попробуйте ещё раз:")
        return
    
    data = await state.get_data()
    city_id = data.get('city_id')
    await state.clear()
    
    db.update_folder_name(city_id, name)
    
    await message.answer(f"✅ Город переименован в «{name}»!")
    await message.delete()


@router.callback_query(F.data == "cancel_edit_city")
async def cancel_edit_city(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Переименование отменено.")
    await callback.answer()


@router.callback_query(F.data.startswith("del_city_"))
async def confirm_delete_city(callback: CallbackQuery):
    city_id = int(callback.data.split("_")[2])
    city = db.get_folder_by_id(city_id)
    if not city:
        await callback.answer("Город не найден", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_del_city_{city_id}")],
        [InlineKeyboardButton(text="❌ Нет", callback_data=f"city_{city_id}")]
    ])
    await callback.message.edit_text(
        f"⚠️ Удалить город «{city['name']}»?\n\n"
        f"Все его источники и каналы также будут удалены!",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_city_"))
async def delete_city(callback: CallbackQuery):
    city_id = int(callback.data.split("_")[3])
    db.delete_folder(city_id)
    await callback.message.edit_text("✅ Город удалён.")
    await callback.answer()


@router.callback_query(F.data == "back_to_cities")
async def back_to_cities(callback: CallbackQuery):
    folders = db.get_folders()
    builder = InlineKeyboardBuilder()
    
    if folders:
        for folder in folders:
            builder.button(text=folder['name'], callback_data=f"city_{folder['id']}")
    
    builder.button(text="➕ Добавить город", callback_data="add_city")
    builder.button(text="❌ Закрыть", callback_data="cancel_cities")
    builder.adjust(2)
    
    await callback.message.edit_text("🏙 Список городов:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "cancel_cities")
async def cancel_cities(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer()
