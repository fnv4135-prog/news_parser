import asyncio
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database
from state import ChannelStates
from utils.delete_utils import delete_message

router = Router()
db = Database()


# ----------------------------------------------------------------------
# Команда /channels – показать список городов
# ----------------------------------------------------------------------
@router.message(Command("channels"))
async def cmd_channels(message: Message, state: FSMContext):
    await state.clear()
    try:
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
            builder.button(text=folder['name'], callback_data=f"ch_list_{folder['id']}")
        builder.button(text="❌ Отмена", callback_data="cancel_channels")
        builder.adjust(2)
        await message.answer("🏙 Выберите город, чтобы просмотреть его каналы для публикации:", reply_markup=builder.as_markup())
        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        logging.error(f"Ошибка в /channels: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data.startswith("ch_list_"))
async def show_publish_channels(callback: CallbackQuery):
    try:
        folder_id = int(callback.data.split("_")[2])
        channels = db.get_publish_channels_by_folder(folder_id)
        folder = db.get_folder_by_id(folder_id)
        
        builder = InlineKeyboardBuilder()
        
        if channels:
            for ch in channels:
                builder.button(text=f"{ch['channel_name']} ({ch['channel_username']})", callback_data=f"ch_action_{ch['id']}")
        
        # Кнопка добавления всегда показывается
        builder.button(text="➕ Добавить канал", callback_data=f"addch_city_{folder_id}")
        builder.button(text="◀ Назад", callback_data="back_to_cities_channels")
        builder.adjust(1)
        
        if channels:
            text = f"📢 Каналы для публикации в городе «{folder['name']}»:"
        else:
            text = f"📭 В городе «{folder['name']}» нет каналов.\n\nНажмите «➕ Добавить канал»:"
        
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        await callback.answer()
    except Exception as e:
        logging.error(f"Ошибка в show_publish_channels: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data == "back_to_cities_channels")
async def back_to_cities_channels(callback: CallbackQuery):
    folders = db.get_folders()
    if not folders:
        await callback.message.edit_text("📭 Нет добавленных городов.")
        await callback.answer()
        return
    builder = InlineKeyboardBuilder()
    for folder in folders:
        builder.button(text=folder['name'], callback_data=f"ch_list_{folder['id']}")
    builder.button(text="❌ Отмена", callback_data="cancel_channels")
    builder.adjust(1)
    await callback.message.edit_text("🏙 Выберите город:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("ch_action_"))
async def channel_action_menu(callback: CallbackQuery):
    try:
        channel_db_id = int(callback.data.split("_")[2])
        conn = db.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM publish_channels WHERE id = ?", (channel_db_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            await callback.answer("Канал не найден", show_alert=True)
            return
        conn2 = db.get_conn()
        cursor2 = conn2.cursor()
        cursor2.execute("PRAGMA table_info(publish_channels)")
        columns = [col[1] for col in cursor2.fetchall()]
        cursor2.close()
        conn2.close()
        ch = dict(zip(columns, row))
        folder = db.get_folder_by_id(ch['folder_id'])
        
        signature_text = ch.get('signature') or '❌ не задана'
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить подпись", callback_data=f"ch_sig_{ch['id']}")],
            [InlineKeyboardButton(text="🗑 Удалить канал", callback_data=f"ch_del_{ch['id']}")],
            [InlineKeyboardButton(text="◀ Назад", callback_data=f"ch_list_{ch['folder_id']}")]
        ])
        await callback.message.edit_text(
            f"📢 Канал: {ch['channel_name']}\n"
            f"👤 Username: @{ch['channel_username']}\n"
            f"🏙 Город: {folder['name']}\n\n"
            f"📝 Подпись:\n{signature_text}",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logging.error(f"Ошибка в channel_action_menu: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("ch_sig_"))
async def ask_signature(callback: CallbackQuery, state: FSMContext):
    """Запрос подписи — устанавливаем FSM состояние"""
    try:
        channel_db_id = int(callback.data.split("_")[2])
        await state.update_data(channel_db_id=channel_db_id)
        await state.set_state(ChannelStates.waiting_signature)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Очистить подпись", callback_data="clear_signature")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_signature")]
        ])
        await callback.message.edit_text(
            "✍️ Введите текст подписи для этого канала.\n\n"
            "Подпись будет добавляться в конец каждого поста.\n\n"
            "💡 Можно использовать форматирование:\n"
            "• Ссылки\n"
            "• <b>Жирный</b>, <i>курсив</i>\n"
            "• @username каналов",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logging.error(f"Ошибка в ask_signature: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.message(ChannelStates.waiting_signature)
async def set_signature_handler(message: Message, state: FSMContext):
    """Получение подписи — срабатывает ТОЛЬКО в состоянии waiting_signature"""
    try:
        # Используем html_text для сохранения форматирования (ссылки, жирный и т.д.)
        signature = message.html_text.strip() if message.html_text else message.text.strip()
        data = await state.get_data()
        channel_db_id = data.get('channel_db_id')
        
        await state.clear()
        
        db.update_publish_channel_signature(channel_db_id, signature)
        
        confirm = await message.answer(
            f"✅ Подпись сохранена!\n\n"
            f"📝 Новая подпись:\n{signature}",
            parse_mode="HTML"
        )
        try:
            await message.delete()
        except Exception:
            pass
        asyncio.create_task(delete_message(confirm, 15))
    except Exception as e:
        logging.error(f"Ошибка в set_signature_handler: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")
        await state.clear()


@router.callback_query(F.data == "clear_signature", ChannelStates.waiting_signature)
async def clear_signature(callback: CallbackQuery, state: FSMContext):
    """Очистка подписи"""
    data = await state.get_data()
    channel_db_id = data.get('channel_db_id')
    await state.clear()
    
    db.update_publish_channel_signature(channel_db_id, None)
    await callback.message.edit_text("✅ Подпись очищена.")
    await callback.answer()


@router.callback_query(F.data == "cancel_signature")
async def cancel_signature(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Изменение подписи отменено.")
    await callback.answer()


@router.callback_query(F.data.startswith("ch_del_"))
async def confirm_delete_channel(callback: CallbackQuery):
    try:
        channel_db_id = int(callback.data.split("_")[2])
        conn = db.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT folder_id, channel_name FROM publish_channels WHERE id = ?", (channel_db_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            await callback.answer("Канал не найден", show_alert=True)
            return
        folder_id, channel_name = row
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_del_ch_{channel_db_id}")],
            [InlineKeyboardButton(text="❌ Нет", callback_data=f"ch_list_{folder_id}")]
        ])
        await callback.message.edit_text(f"⚠️ Удалить канал «{channel_name}» из списка публикации?", reply_markup=kb)
        await callback.answer()
    except Exception as e:
        logging.error(f"Ошибка в confirm_delete_channel: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("confirm_del_ch_"))
async def delete_channel(callback: CallbackQuery):
    try:
        channel_db_id = int(callback.data.split("_")[3])
        db.delete_publish_channel(channel_db_id)
        await callback.message.edit_text("✅ Канал удалён.")
        await callback.answer()
    except Exception as e:
        logging.error(f"Ошибка в delete_channel: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data == "cancel_channels")
async def cancel_channels(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer()


# ----------------------------------------------------------------------
# Команда /add_channel – добавить канал для публикации
# ----------------------------------------------------------------------
@router.message(Command("add_channel"))
async def cmd_add_channel_start(message: Message, state: FSMContext):
    await state.clear()
    try:
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
            builder.button(text=folder['name'], callback_data=f"addch_city_{folder['id']}")
        builder.button(text="❌ Отмена", callback_data="cancel_add_channel")
        builder.adjust(2)
        await message.answer("🏙 Выберите город, в который добавить канал для публикации:", reply_markup=builder.as_markup())
        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        logging.error(f"Ошибка в /add_channel: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data.startswith("addch_city_"))
async def ask_channel_username(callback: CallbackQuery, state: FSMContext):
    try:
        folder_id = int(callback.data.split("_")[2])
        await state.update_data(folder_id=folder_id)
        await state.set_state(ChannelStates.waiting_username)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_channel")]
        ])
        await callback.message.edit_text(
            "📢 Отправьте данные канала одним из способов:\n\n"
            "1️⃣ Username: @news_channel\n"
            "2️⃣ Ссылка: https://t.me/news_channel\n"
            "3️⃣ <b>Для приватных каналов:</b> перешлите любое сообщение из канала\n\n"
            "⚠️ Бот должен быть администратором канала!",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logging.error(f"Ошибка в ask_channel_username: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.message(ChannelStates.waiting_username, F.forward_from_chat)
async def receive_channel_forward(message: Message, state: FSMContext):
    """Получение канала через пересланное сообщение"""
    try:
        chat = message.forward_from_chat
        
        if chat.type not in ["channel", "supergroup"]:
            await message.answer("❌ Это не канал. Перешлите сообщение из канала:")
            return
        
        data = await state.get_data()
        folder_id = data.get('folder_id')
        await state.clear()
        
        db.add_publish_channel(
            folder_id=folder_id,
            channel_id=str(chat.id),
            channel_name=chat.title,
            channel_username=chat.username or f"private_{chat.id}",
            signature=None
        )
        folder = db.get_folder_by_id(folder_id)
        
        confirm = await message.answer(
            f"✅ Канал добавлен!\n\n"
            f"📢 Канал: {chat.title}\n"
            f"🏙 Город: {folder['name']}\n\n"
            f"💡 Настройте подпись через /channels"
        )
        try:
            await message.delete()
        except Exception:
            nano +338 /root/bots/news_parser/handlers/publish_channels.pypass
        asyncio.create_task(delete_message(confirm, 15))
        
    except Exception as e:
        logging.error(f"Ошибка в receive_channel_forward: {e}")
        await message.answer("❌ Не удалось получить информацию о канале.")


@router.message(ChannelStates.waiting_username)
async def receive_channel_username(message: Message, state: FSMContext):
    """Получение username канала — срабатывает ТОЛЬКО в состоянии waiting_username"""
    try:
        user_input = message.text.strip()
        data = await state.get_data()
        folder_id = data.get('folder_id')
        
        chat = None
        
        # Проверяем, не chat_id ли это (число)
        if user_input.lstrip('-').isdigit():
            try:
                chat = await message.bot.get_chat(int(user_input))
            except:
                pass
        
        # Пробуем как username или ссылку
        if not chat:
            if user_input.startswith("https://t.me/"):
                username = user_input.replace("https://t.me/", "")
                # Приватные ссылки (+ или joinchat) не поддерживаются напрямую
                if username.startswith("+") or username.startswith("joinchat/"):
                    await message.answer(
                        "❌ Приватные ссылки не поддерживаются напрямую.\n\n"
                        "📌 <b>Для приватного канала:</b>\n"
                        "Перешлите любое сообщение из канала сюда.",
                        parse_mode="HTML"
                    )
                    return
                else:
                    chat = await message.bot.get_chat("@" + username)
            elif user_input.startswith("@"):
                chat = await message.bot.get_chat(user_input)
            else:
                chat = await message.bot.get_chat("@" + user_input)
        
        if not chat:
            await message.answer("❌ Канал не найден. Попробуйте ещё раз:")
            return
        
        if chat.type not in ["channel", "supergroup"]:
            await message.answer("❌ Это не канал. Отправьте username канала:")
            return
        
        await state.clear()
        
        db.add_publish_channel(
            folder_id=folder_id,
            channel_id=str(chat.id),
            channel_name=chat.title,
            channel_username=chat.username or f"private_{chat.id}",
            signature=None
        )
        folder = db.get_folder_by_id(folder_id)
        
        confirm = await message.answer(
            f"✅ Канал добавлен!\n\n"
            f"📢 Канал: {chat.title}\n"
            f"🏙 Город: {folder['name']}\n\n"
            f"💡 Настройте подпись через /channels"
        )
        try:
            await message.delete()
        except Exception:
            pass
        asyncio.create_task(delete_message(confirm, 15))
        
    except Exception as e:
        logging.error(f"Ошибка в receive_channel_username: {e}")
        await message.answer(
            "❌ Канал не найден или бот не имеет доступа.\n\n"
            "Убедитесь, что:\n"
            "1. Канал существует\n"
            "2. Бот добавлен как администратор\n\n"
            "💡 Для приватного канала перешлите сообщение из него."
        )


@router.callback_query(F.data == "cancel_add_channel")
async def cancel_add_channel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Добавление канала отменено.")
    await callback.answer()
