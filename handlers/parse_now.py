import asyncio
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from scheduler.jobs import parse_vk_and_save, parse_telegram_and_save, parse_rss_and_save
from utils.delete_utils import delete_message

router = Router()

@router.message(Command("parse_now"))
async def cmd_parse_now(message: Message, state: FSMContext):
    await state.clear()  # Очищаем FSM состояние
    sent = await message.answer("🔄 Запускаю парсинг VK, Telegram и RSS...")
    await message.delete()
    await asyncio.gather(parse_vk_and_save(), parse_telegram_and_save(), parse_rss_and_save())
    await sent.delete()
    done_msg = await message.answer("✅ Парсинг завершён. Используйте /posts для просмотра новых постов.")
    asyncio.create_task(delete_message(done_msg, 15))