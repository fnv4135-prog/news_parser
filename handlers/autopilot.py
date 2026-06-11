import json
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database

router = Router()
db = Database()


class AutopilotStates(StatesGroup):
    selecting_city = State()
    selecting_slots = State()
    selecting_posts_per_day = State()
    selecting_plan_time = State()
    selecting_report_time = State()


def build_city_keyboard():
    folders = db.get_folders()
    builder = InlineKeyboardBuilder()
    for f in folders:
        settings = db.get_autopilot_settings(f['id'])
        icon = "✅" if settings and settings['is_enabled'] else "⬜"
        builder.button(
            text=f"{icon} {f['name']}",
            callback_data=f"ap_city|{f['id']}"
        )
    builder.button(text="❌ Закрыть", callback_data="ap_close")
    builder.adjust(1)
    return builder.as_markup()


def build_city_menu(folder_id: int, folder_name: str):
    settings = db.get_autopilot_settings(folder_id)
    is_enabled = settings['is_enabled'] if settings else 0
    posts_per_day = settings['posts_per_day'] if settings else 6
    slots = settings['slots'] if settings else []
    plan_time = settings['plan_time'] if settings else '05:00'
    report_time = settings['report_time'] if settings else '06:00'

    slots_str = ", ".join(slots) if slots else "не настроены"
    status = "✅ Включён" if is_enabled else "⛔ Выключен"

    text = (
        f"⚙️ Автопилот — {folder_name}\n\n"
        f"Статус: {status}\n"
        f"Постов в день: {posts_per_day}\n"
        f"Слоты: {slots_str}\n"
        f"Формирование плана: {plan_time} (МСК)\n"
        f"Утренняя сводка: {report_time} (МСК)"
    )

    toggle_text = "⛔ Выключить" if is_enabled else "✅ Включить"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=f"ap_toggle|{folder_id}")],
        [InlineKeyboardButton(text="🕐 Настроить слоты", callback_data=f"ap_slots|{folder_id}")],
        [InlineKeyboardButton(text=f"📊 Постов в день: {posts_per_day}", callback_data=f"ap_ppd|{folder_id}")],
        [InlineKeyboardButton(text=f"🌙 Время плана: {plan_time}", callback_data=f"ap_plantime|{folder_id}")],
        [InlineKeyboardButton(text=f"☀️ Время сводки: {report_time}", callback_data=f"ap_reporttime|{folder_id}")],
        [InlineKeyboardButton(text="◀ Назад", callback_data="ap_back")],
    ])
    return text, kb


def build_slots_keyboard(folder_id: int, selected: list):
    builder = InlineKeyboardBuilder()
    for h in range(24):
        time_str = f"{h:02d}:00"
        icon = "✅" if time_str in selected else "⬜"
        builder.button(
            text=f"{icon} {time_str}",
            callback_data=f"ap_slot_toggle|{folder_id}|{time_str}"
        )
    builder.adjust(4)
    builder.row(
        InlineKeyboardButton(text="💾 Сохранить", callback_data=f"ap_slots_save|{folder_id}"),
        InlineKeyboardButton(text="◀ Назад", callback_data=f"ap_city|{folder_id}")
    )
    return builder.as_markup()


@router.message(Command("autopilot"))
async def cmd_autopilot(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🤖 Автопилот\n\nВыберите город для настройки:",
        reply_markup=build_city_keyboard()
    )
    await message.delete()


@router.callback_query(F.data == "ap_back")
async def ap_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🤖 Автопилот\n\nВыберите город для настройки:",
        reply_markup=build_city_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "ap_close")
async def ap_close(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data.startswith("ap_city|"))
async def ap_select_city(callback: CallbackQuery, state: FSMContext):
    folder_id = int(callback.data.split("|")[1])
    folder = db.get_folder_by_id(folder_id)
    if not folder:
        await callback.answer("Город не найден", show_alert=True)
        return
    await state.update_data(folder_id=folder_id)
    text, kb = build_city_menu(folder_id, folder['name'])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("ap_toggle|"))
async def ap_toggle(callback: CallbackQuery):
    folder_id = int(callback.data.split("|")[1])
    folder = db.get_folder_by_id(folder_id)
    settings = db.get_autopilot_settings(folder_id)
    current = settings['is_enabled'] if settings else 0
    db.save_autopilot_settings(folder_id, is_enabled=0 if current else 1)
    text, kb = build_city_menu(folder_id, folder['name'])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("✅ Сохранено")


@router.callback_query(F.data.startswith("ap_slots|"))
async def ap_slots(callback: CallbackQuery, state: FSMContext):
    folder_id = int(callback.data.split("|")[1])
    settings = db.get_autopilot_settings(folder_id)
    selected = settings['slots'] if settings else []
    await state.update_data(folder_id=folder_id, selected_slots=selected)
    await state.set_state(AutopilotStates.selecting_slots)
    await callback.message.edit_text(
        "🕐 Выберите временные слоты публикации:\n(нажимайте для выбора/отмены)",
        reply_markup=build_slots_keyboard(folder_id, selected)
    )
    await callback.answer()


@router.callback_query(AutopilotStates.selecting_slots, F.data.startswith("ap_slot_toggle|"))
async def ap_slot_toggle(callback: CallbackQuery, state: FSMContext):
    _, folder_id_str, time_str = callback.data.split("|")
    folder_id = int(folder_id_str)
    data = await state.get_data()
    selected = data.get('selected_slots', [])
    if time_str in selected:
        selected.remove(time_str)
    else:
        selected.append(time_str)
        selected.sort()
    await state.update_data(selected_slots=selected)
    await callback.message.edit_reply_markup(
        reply_markup=build_slots_keyboard(folder_id, selected)
    )
    await callback.answer()


@router.callback_query(AutopilotStates.selecting_slots, F.data.startswith("ap_slots_save|"))
async def ap_slots_save(callback: CallbackQuery, state: FSMContext):
    folder_id = int(callback.data.split("|")[1])
    folder = db.get_folder_by_id(folder_id)
    data = await state.get_data()
    selected = data.get('selected_slots', [])
    db.save_autopilot_settings(folder_id, slots=selected)
    await state.set_state(None)
    text, kb = build_city_menu(folder_id, folder['name'])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("✅ Слоты сохранены")


@router.callback_query(F.data.startswith("ap_ppd|"))
async def ap_posts_per_day(callback: CallbackQuery, state: FSMContext):
    folder_id = int(callback.data.split("|")[1])
    await state.update_data(folder_id=folder_id)
    await state.set_state(AutopilotStates.selecting_posts_per_day)
    builder = InlineKeyboardBuilder()
    for n in range(1, 13):
        builder.button(text=str(n), callback_data=f"ap_ppd_set|{folder_id}|{n}")
    builder.adjust(4)
    builder.row(InlineKeyboardButton(text="◀ Назад", callback_data=f"ap_city|{folder_id}"))
    await callback.message.edit_text(
        "📊 Сколько постов публиковать в день?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ap_ppd_set|"))
async def ap_ppd_set(callback: CallbackQuery, state: FSMContext):
    _, folder_id_str, n_str = callback.data.split("|")
    folder_id = int(folder_id_str)
    folder = db.get_folder_by_id(folder_id)
    db.save_autopilot_settings(folder_id, posts_per_day=int(n_str))
    await state.set_state(None)
    text, kb = build_city_menu(folder_id, folder['name'])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("✅ Сохранено")


@router.callback_query(F.data.startswith("ap_plantime|"))
async def ap_plantime(callback: CallbackQuery, state: FSMContext):
    folder_id = int(callback.data.split("|")[1])
    await state.update_data(folder_id=folder_id)
    await state.set_state(AutopilotStates.selecting_plan_time)
    builder = InlineKeyboardBuilder()
    for h in range(24):
        builder.button(text=f"{h:02d}:00", callback_data=f"ap_plantime_set|{folder_id}|{h:02d}:00")
    builder.adjust(4)
    builder.row(InlineKeyboardButton(text="◀ Назад", callback_data=f"ap_city|{folder_id}"))
    await callback.message.edit_text(
        "🌙 Во сколько формировать план на день? (МСК)",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ap_plantime_set|"))
async def ap_plantime_set(callback: CallbackQuery, state: FSMContext):
    _, folder_id_str, time_str = callback.data.split("|")
    folder_id = int(folder_id_str)
    folder = db.get_folder_by_id(folder_id)
    db.save_autopilot_settings(folder_id, plan_time=time_str)
    await state.set_state(None)
    text, kb = build_city_menu(folder_id, folder['name'])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("✅ Сохранено")


@router.callback_query(F.data.startswith("ap_reporttime|"))
async def ap_reporttime(callback: CallbackQuery, state: FSMContext):
    folder_id = int(callback.data.split("|")[1])
    await state.update_data(folder_id=folder_id)
    await state.set_state(AutopilotStates.selecting_report_time)
    builder = InlineKeyboardBuilder()
    for h in range(24):
        builder.button(text=f"{h:02d}:00", callback_data=f"ap_reporttime_set|{folder_id}|{h:02d}:00")
    builder.adjust(4)
    builder.row(InlineKeyboardButton(text="◀ Назад", callback_data=f"ap_city|{folder_id}"))
    await callback.message.edit_text(
        "☀️ Во сколько присылать утреннюю сводку? (МСК)",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ap_reporttime_set|"))
async def ap_reporttime_set(callback: CallbackQuery, state: FSMContext):
    _, folder_id_str, time_str = callback.data.split("|")
    folder_id = int(folder_id_str)
    folder = db.get_folder_by_id(folder_id)
    db.save_autopilot_settings(folder_id, report_time=time_str)
    await state.set_state(None)
    text, kb = build_city_menu(folder_id, folder['name'])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("✅ Сохранено")
