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
    editing_post = State()


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
    builder.adjust(2)
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
        [InlineKeyboardButton(text="📅 Посты на сегодня", callback_data=f"ap_review|{folder_id}|0")],
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


# ==================== ПРОСМОТР ПЛАНА ====================

def build_review_keyboard(folder_id: int, index: int, total: int, scheduled_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать", callback_data=f"ap_edit|{scheduled_id}|{folder_id}|{index}")
    builder.button(text="🔄 Заменить", callback_data=f"ap_replace|{folder_id}|{scheduled_id}|{index}")
    if index + 1 < total:
        builder.button(text=f"Следующий ({index + 2}/{total}) ➡️", callback_data=f"ap_review|{folder_id}|{index + 1}")
    else:
        builder.button(text="◀ К первому", callback_data=f"ap_review|{folder_id}|0")
    builder.button(text="❌ Закрыть", callback_data="ap_close")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


@router.callback_query(F.data.startswith("ap_review|"))
async def ap_review(callback: CallbackQuery):
    parts = callback.data.split("|")
    folder_id = int(parts[1])
    index = int(parts[2])

    folder = db.get_folder_by_id(folder_id)
    folder_name = folder['name'] if folder else f"Город #{folder_id}"

    scheduled = db.get_scheduled_by_folder(folder_id)
    if not scheduled:
        await callback.message.edit_text(f"📭 {folder_name} — нет запланированных постов.")
        await callback.answer()
        return

    total = len(scheduled)
    if index >= total:
        index = total - 1

    sch = scheduled[index]
    sched_time = sch['scheduled_at']
    if isinstance(sched_time, str):
        from datetime import datetime, timedelta
        sched_time = datetime.strptime(sched_time[:16], "%Y-%m-%d %H:%M")
    from datetime import timedelta
    sched_msk = sched_time + timedelta(hours=3)
    time_str = sched_msk.strftime("%H:%M")

    text_preview = (sch['text'] or '')[:300]

    text = (
        f"📋 {folder_name} — пост {index + 1}/{total}\n"
        f"🕐 Слот: {time_str}\n\n"
        f"{text_preview}"
        f"{'...' if len(sch['text'] or '') > 300 else ''}"
    )
    kb = build_review_keyboard(folder_id, index, total, sch['id'])
    # Получаем фото
    from utils.post_sender import get_media_urls
    import os
    post = db.get_post_by_id(sch['post_id']) if sch.get('post_id') else None
    image_url = sch.get('image_url') or (post.get('image_url') if post else None)
    media_urls = get_media_urls(post) if post else []
    photo = media_urls[0] if media_urls else image_url

    try:
        await callback.message.delete()
    except Exception:
        pass

    if photo and os.path.isfile(str(photo)):
        from aiogram.types import FSInputFile
        await callback.message.answer_photo(
            FSInputFile(photo), caption=text, reply_markup=kb, parse_mode="HTML"
        )
    elif photo:
        await callback.message.answer_photo(
            photo, caption=text, reply_markup=kb, parse_mode="HTML"
        )
    else:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()
    await callback.answer()


@router.callback_query(F.data.startswith("ap_confirm|"))
async def ap_confirm(callback: CallbackQuery):
    folder_id = int(callback.data.split("|")[1])
    folder = db.get_folder_by_id(folder_id)
    folder_name = folder['name'] if folder else f"Город #{folder_id}"

    scheduled = db.get_scheduled_by_folder(folder_id)
    await callback.message.edit_text(
        f"✅ {folder_name} — план запущен!\n"
        f"Запланировано постов: {len(scheduled)}"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ap_replace|"))
async def ap_replace(callback: CallbackQuery):
    parts = callback.data.split("|")
    folder_id = int(parts[1])
    scheduled_id = int(parts[2])
    index = int(parts[3])

    # Получаем текущий scheduled пост
    current = db.get_scheduled_by_id(scheduled_id)
    if not current:
        await callback.answer("Пост не найден", show_alert=True)
        return

    # Ищем замену — пост не в расписании и не тот же
    candidates = db.get_posts_for_autopilot(folder_id, limit=20)
    replacement = None
    for c in candidates:
        if c['id'] != current.get('post_id'):
            replacement = c
            break

    if not replacement:
        await callback.answer("⚠️ Нет подходящих постов для замены", show_alert=True)
        return

    # Обновляем scheduled_post
    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE scheduled_posts
        SET post_id = ?, text = ?, image_url = ?, media_list = ?
        WHERE id = ?
    """, (
        replacement['id'],
        replacement.get('text'),
        replacement.get('image_url'),
        replacement.get('media_urls'),
        scheduled_id
    ))
    conn.commit()
    conn.close()

    await callback.answer("✅ Пост заменён")

    # Показываем обновлённый пост
    await ap_review(callback.__class__(
        update=callback.update,
        bot=callback.bot,
        **{**callback.__dict__,
           'data': f"ap_review|{folder_id}|{index}"}
    ))

@router.callback_query(F.data.startswith("ap_edit|"))
async def ap_edit(callback: CallbackQuery, state: FSMContext):
    """Редактирование текста запланированного поста."""
    parts = callback.data.split("|")
    scheduled_id = int(parts[1])
    folder_id = int(parts[2]) if len(parts) > 2 else None
    index = int(parts[3]) if len(parts) > 3 else 0
    current = db.get_scheduled_by_id(scheduled_id)
    if not current:
        await callback.answer("❌ Пост не найден.", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    prompt = await callback.message.answer(
        "✏️ Отправьте новый текст для поста:\n\n" +
        f"<i>{(current.get('text') or '')[:3500]}</i>",
        parse_mode="HTML"
    )
    await state.update_data(
        editing_scheduled_id=scheduled_id,
        editing_folder_id=folder_id,
        editing_index=index,
        prompt_msg_id=prompt.message_id,
        prompt_chat_id=prompt.chat.id
    )
    await state.set_state(AutopilotStates.editing_post)
    await callback.answer()

@router.message(AutopilotStates.editing_post)
async def ap_edit_save(message: Message, state: FSMContext):
    """Сохраняет отредактированный текст."""
    data = await state.get_data()
    scheduled_id = data.get('editing_scheduled_id')
    if not scheduled_id:
        await state.clear()
        return
    conn = db.get_conn()
    conn.execute("UPDATE scheduled_posts SET text = ? WHERE id = ?", (message.text, scheduled_id))
    conn.commit()
    conn.close()
    # Удаляем сообщение-запрос
    prompt_msg_id = data.get('prompt_msg_id')
    prompt_chat_id = data.get('prompt_chat_id')
    if prompt_msg_id:
        try:
            await message.bot.delete_message(prompt_chat_id, prompt_msg_id)
        except Exception:
            pass
    await state.clear()
    folder_id = data.get('editing_folder_id')
    index = data.get('editing_index', 0)
    import asyncio
    try:
        await message.delete()
    except Exception:
        pass
    if folder_id is not None:
        # Возвращаемся к просмотру плана
        callback_data = f"ap_review|{folder_id}|{index}"
        from aiogram.types import CallbackQuery as CQ
        fake_cb = type('obj', (object,), {
            'data': callback_data,
            'message': message,
            'from_user': message.from_user,
            'answer': lambda *a, **kw: asyncio.sleep(0),
            'bot': message.bot,
        })()
        await ap_review(fake_cb)
    else:
        done = await message.answer("✅ Текст обновлён!")
        await asyncio.sleep(3)
        try:
            await done.delete()
        except Exception:
            pass

@router.callback_query(F.data == "ap_close")
async def ap_close(callback: CallbackQuery):
    """Закрывает просмотр плана."""
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()

@router.message(Command("today"))
async def cmd_today(message: Message):
    """Показывает список городов с автопилотом для просмотра плана на сегодня."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    all_settings = db.get_all_autopilot_settings()
    if not all_settings:
        await message.answer("⚠️ Нет городов с включённым автопилотом.")
        return
    builder = InlineKeyboardBuilder()
    for s in all_settings:
        folder = db.get_folder_by_id(s['folder_id'])
        if not folder:
            continue
        count = len(db.get_scheduled_by_folder(s['folder_id']))
        builder.button(
            text=f"{folder['name']} ({count} постов)",
            callback_data=f"ap_review|{s['folder_id']}|0"
        )
    builder.adjust(1)
    await message.answer("📅 Выберите город для просмотра плана:", reply_markup=builder.as_markup())
