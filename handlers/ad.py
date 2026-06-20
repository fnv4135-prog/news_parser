import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import List

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database
from state import AdStates
from bot_instance import get_bot
from utils.delete_utils import delete_message

router = Router()
db = Database()


# ======================================================================
# Вспомогательные функции
# ======================================================================

def _cleanup_msg(text: str, signature: str = None) -> str:
    """Добавляет подпись и обрезает если нужно"""
    final = text
    if signature:
        final += f"\n\n{signature}"
    return final


async def _send_ad_to_channel(bot, channel_id: str, text: str, media_ids: list, media_types: list):
    """Отправляет рекламу в один канал"""
    signature = db.get_publish_channel_signature(channel_id)
    final_text = _cleanup_msg(text, signature)

    if media_ids:
        if len(final_text) > 1024:
            split_pos = 1020
            for i in range(1020, 800, -1):
                if final_text[i] in '.!?\n':
                    split_pos = i + 1
                    break
            else:
                for i in range(1020, 800, -1):
                    if final_text[i] == ' ':
                        split_pos = i
                        break

            caption_text = final_text[:split_pos].strip()
            continuation_text = final_text[split_pos:].strip()
            if len(continuation_text) > 4096:
                continuation_text = continuation_text[:4093] + "..."
        else:
            caption_text = final_text
            continuation_text = None

        media_group = []
        for i, (file_id, typ) in enumerate(zip(media_ids, media_types)):
            if typ == 'photo':
                media_group.append(InputMediaPhoto(
                    media=file_id,
                    caption=caption_text if i == 0 else None,
                    parse_mode="HTML" if i == 0 else None
                ))
            else:
                media_group.append(InputMediaVideo(
                    media=file_id,
                    caption=caption_text if i == 0 else None,
                    parse_mode="HTML" if i == 0 else None
                ))
        await bot.send_media_group(chat_id=channel_id, media=media_group)

        if continuation_text:
            await bot.send_message(
                chat_id=channel_id, text=continuation_text,
                parse_mode="HTML", disable_web_page_preview=True
            )
    else:
        if len(final_text) > 4096:
            final_text = final_text[:4093] + "..."
        await bot.send_message(
            chat_id=channel_id, text=final_text,
            parse_mode="HTML", disable_web_page_preview=True
        )


# ======================================================================
# /ad — старт
# ======================================================================
@router.message(Command("ad"))
async def cmd_ad(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdStates.waiting_text)
    await message.answer(
        "📢 Отправьте текст рекламного сообщения.\n"
        "Можно использовать форматирование (жирный, курсив и т.д.)."
    )
    try:
        await message.delete()
    except Exception:
        pass
    logging.info(f"User {message.from_user.id} started ad creation")


# ======================================================================
# Шаг 1: получение текста рекламы
# ======================================================================
@router.message(AdStates.waiting_text, F.text)
async def ad_receive_text(message: Message, state: FSMContext):
    await state.update_data(
        ad_text=message.html_text,
        ad_media_list=[],
        ad_media_types=[]
    )
    await state.set_state(AdStates.waiting_media)
    await message.answer(
        "📎 Теперь пришлите фото или видео (до 10 файлов).\n"
        "После каждого файла бот подтвердит приём.\n"
        "Когда закончите, нажмите /done (или /skip, если медиа не нужны)."
    )
    try:
        await message.delete()
    except Exception:
        pass


# ======================================================================
# Шаг 2: получение медиа (фото / видео / команды)
# ======================================================================
@router.message(AdStates.waiting_media, F.photo)
async def ad_receive_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    media_list = data.get('ad_media_list', [])
    media_types = data.get('ad_media_types', [])

    if len(media_list) >= 10:
        await message.answer("❌ Вы уже отправили 10 файлов. Нажмите /done, чтобы продолжить.")
        return

    media_list.append(message.photo[-1].file_id)
    media_types.append('photo')
    await state.update_data(ad_media_list=media_list, ad_media_types=media_types)
    await message.answer(f"✅ Фото принято ({len(media_list)}/10). Отправьте ещё или /done.")
    try:
        await message.delete()
    except Exception:
        pass


@router.message(AdStates.waiting_media, F.video)
async def ad_receive_video(message: Message, state: FSMContext):
    data = await state.get_data()
    media_list = data.get('ad_media_list', [])
    media_types = data.get('ad_media_types', [])

    if len(media_list) >= 10:
        await message.answer("❌ Вы уже отправили 10 файлов. Нажмите /done, чтобы продолжить.")
        return

    media_list.append(message.video.file_id)
    media_types.append('video')
    await state.update_data(ad_media_list=media_list, ad_media_types=media_types)
    await message.answer(f"✅ Видео принято ({len(media_list)}/10). Отправьте ещё или /done.")
    try:
        await message.delete()
    except Exception:
        pass


@router.message(AdStates.waiting_media, Command("done", "skip"))
async def ad_media_done(message: Message, state: FSMContext):
    """Завершение этапа медиа — переход к выбору города"""
    await state.set_state(AdStates.waiting_city)
    folders = db.get_folders()
    if not folders:
        await message.answer("📭 Нет добавленных городов. Сначала создайте город через /cities.")
        await state.clear()
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Все города", callback_data="ad_city_all")
    for folder in folders:
        builder.button(text=folder['name'], callback_data=f"ad_city_{folder['id']}")
    builder.button(text="❌ Отмена", callback_data="ad_cancel")
    builder.adjust(2)
    await message.answer(
        "🏙 Выберите город, в каналы которого хотите отправить рекламу:",
        reply_markup=builder.as_markup()
    )
    try:
        await message.delete()
    except Exception:
        pass


@router.message(AdStates.waiting_media, F.text)
async def ad_media_unexpected_text(message: Message, state: FSMContext):
    """Любой другой текст на этапе медиа — подсказка"""
    await message.answer("📎 Отправьте фото/видео, или нажмите /done (или /skip).")


# ======================================================================
# Шаг 3: выбор города (callback)
# ======================================================================
@router.callback_query(F.data == "ad_city_all")
async def ad_city_all_selected(callback: CallbackQuery, state: FSMContext):
    """Выбор всех городов — собираем ВСЕ каналы"""
    folders = db.get_folders()
    all_channels = []
    for folder in folders:
        channels = db.get_publish_channels_by_folder(folder['id'])
        all_channels.extend(channels)
    if not all_channels:
        await callback.message.edit_text("📭 Нет каналов для публикации.")
        await callback.answer()
        return
    # Сразу выбираем все каналы
    all_ids = [ch['channel_id'] for ch in all_channels]
    await state.update_data(ad_city_id='all', ad_channels=all_ids)
    
    names = [f"✅ {ch['channel_name']}" for ch in all_channels]
    await callback.message.edit_text(
        f"📢 Выбраны ВСЕ каналы ({len(all_channels)}):\n" +
        "\n".join(names) +
        "\n\nНажмите Готово для продолжения.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data="ad_channels_selected")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="ad_cancel")]
        ])
    )
    await callback.answer()

@router.callback_query(F.data.startswith("ad_city_"))
async def ad_city_selected(callback: CallbackQuery, state: FSMContext):
    city_id = int(callback.data.split("_")[2])
    await state.update_data(ad_city_id=city_id, ad_channels=[])
    channels = db.get_publish_channels_by_folder(city_id)
    if not channels:
        await callback.message.edit_text(
            "📭 В выбранном городе нет каналов для публикации. Добавьте через /add_channel."
        )
        await callback.answer()
        return
    builder = InlineKeyboardBuilder()
    for ch in channels:
        builder.button(
            text=f"⬜ {ch['channel_name']} ({ch['channel_username']})",
            callback_data=f"ad_toggle_channel|{ch['channel_id']}"
        )
    builder.button(text="✅ Готово", callback_data="ad_channels_selected")
    builder.button(text="❌ Отмена", callback_data="ad_cancel")
    builder.adjust(1)
    await callback.message.edit_text(
        "Выберите каналы для публикации (можно несколько):",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


# ======================================================================
# Шаг 4: переключение каналов (callback)
# ======================================================================
@router.callback_query(F.data.startswith("ad_toggle_channel|"))
async def ad_toggle_channel(callback: CallbackQuery, state: FSMContext):
    channel_id = callback.data.split("|")[1]
    data = await state.get_data()
    selected = set(data.get('ad_channels', []))

    if channel_id in selected:
        selected.remove(channel_id)
    else:
        selected.add(channel_id)
    await state.update_data(ad_channels=list(selected))

    city_id = data.get('ad_city_id')
    channels = db.get_publish_channels_by_folder(city_id)
    builder = InlineKeyboardBuilder()
    for ch in channels:
        mark = "✅" if ch['channel_id'] in selected else "⬜"
        builder.button(
            text=f"{mark} {ch['channel_name']} ({ch['channel_username']})",
            callback_data=f"ad_toggle_channel|{ch['channel_id']}"
        )
    builder.button(text="✅ Готово", callback_data="ad_channels_selected")
    builder.button(text="❌ Отмена", callback_data="ad_cancel")
    builder.adjust(1)
    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())
    await callback.answer()


# ======================================================================
# Шаг 5: каналы выбраны — что делаем?
# ======================================================================
@router.callback_query(F.data == "ad_channels_selected")
async def ad_channels_selected(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('ad_channels', [])
    if not selected:
        await callback.answer("Выберите хотя бы один канал", show_alert=True)
        return
    await state.set_state(AdStates.waiting_channels)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Опубликовать сейчас", callback_data="ad_publish_now")],
        [InlineKeyboardButton(text="🕒 Отложить", callback_data="ad_schedule")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="ad_cancel")]
    ])
    await callback.message.edit_text("Теперь выберите действие:", reply_markup=kb)
    await callback.answer()


# ======================================================================
# Публикация сейчас
# ======================================================================
@router.callback_query(F.data == "ad_publish_now")
async def ad_publish_now(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    text = data.get('ad_text')
    media_ids = data.get('ad_media_list', [])
    media_types = data.get('ad_media_types', [])
    selected_channel_ids = data.get('ad_channels', [])

    if not text or not selected_channel_ids:
        await callback.answer("Ошибка: текст или каналы не выбраны", show_alert=True)
        return

    bot = get_bot()
    success = []
    errors = []

    for channel_id in selected_channel_ids:
        try:
            await _send_ad_to_channel(bot, channel_id, text, media_ids, media_types)
            success.append(channel_id)
        except Exception as e:
            logging.error(f"Ошибка публикации рекламы в {channel_id}: {e}")
            errors.append(f"{channel_id}: {e}")

    await state.clear()

    msg = f"✅ Реклама опубликована в {len(success)} канал(ов)."
    if errors:
        msg += f"\n❌ Ошибки: {', '.join(errors)}"
    await callback.message.edit_text(msg)
    await callback.answer()


# ======================================================================
# Отложить — запрос даты
# ======================================================================
@router.callback_query(F.data == "ad_schedule")
async def ad_ask_schedule_time(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdStates.waiting_time)
    await callback.message.edit_text(
        "⏰ Введите дату и время публикации в формате:\n\n"
        "`YYYY-MM-DD HH:MM`\n\n"
        "Пример: `2026-04-17 15:30`\n\n"
        "Время московское (UTC+3)."
    )
    await callback.answer()


# ======================================================================
# Получение даты отложенной публикации
# ======================================================================
@router.message(AdStates.waiting_time, F.text)
async def ad_receive_time(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        scheduled_time = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте YYYY-MM-DD HH:MM")
        return

    now_msk = datetime.utcnow() + timedelta(hours=3)
    if scheduled_time < now_msk:
        await message.answer("❌ Дата и время должны быть в будущем.")
        return
    
    scheduled_time_utc = scheduled_time - timedelta(hours=3)

    data = await state.get_data()
    ad_text = data.get('ad_text')
    media_ids = data.get('ad_media_list', [])
    media_types = data.get('ad_media_types', [])
    selected_channel_ids = data.get('ad_channels', [])
    city_id = data.get('ad_city_id')

    if not ad_text or not selected_channel_ids:
        await message.answer("Ошибка: текст или каналы не выбраны")
        await state.clear()
        return

    media_list_json = json.dumps(
        [{"type": t, "file_id": f} for t, f in zip(media_types, media_ids)]
    ) if media_ids else None

    for channel_id in selected_channel_ids:
        signature = db.get_publish_channel_signature(channel_id)
        final_text = _cleanup_msg(ad_text, signature)
        db.add_scheduled_post(
            post_id=-1,
            channel_ids=[channel_id],
            scheduled_at=scheduled_time_utc,
            text=final_text,
            image_url=None,
            video_url=None,
            signature=None,
            is_ad=1,
            folder_id=city_id,
            media_list=media_list_json
        )

    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Закрыть", callback_data="ad_schedule_done")]
    ])
    await message.answer(
        f"✅ Реклама запланирована на {scheduled_time.strftime('%Y-%m-%d %H:%M')} "
        f"в {len(selected_channel_ids)} каналах.",
        reply_markup=kb
    )
    try:
        await message.delete()
    except Exception:
        pass


# ======================================================================
# Служебные callback'и
# ======================================================================
@router.callback_query(F.data == "ad_schedule_done")
async def ad_schedule_done(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data == "ad_cancel")
async def ad_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Публикация рекламы отменена.")
    await callback.answer()
