import asyncio
import logging
from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from database import Database
from scheduler.jobs import parse_vk_and_save, parse_rss_and_save
from utils.delete_utils import delete_message

router = Router()
db = Database()
log = logging.getLogger(__name__)

def _last_tg_parse_info() -> str:
    """Возвращает строку о последнем TG парсинге из БД."""
    try:
        conn = db.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(parsed_at) FROM posts WHERE source = 'telegram'")
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            last = datetime.fromisoformat(row[0])
            now = datetime.utcnow()
            diff = now - last
            minutes = int(diff.total_seconds() // 60)
            if minutes < 60:
                return f"📱 TG: последний парсинг {minutes} мин назад"
            else:
                hours = minutes // 60
                return f"📱 TG: последний парсинг {hours} ч назад"
        return "📱 TG: ещё не парсился"
    except Exception:
        return "📱 TG: нет данных"

@router.message(Command("parse_now"))
async def cmd_parse_now(message: Message, state: FSMContext):
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    tg_info = _last_tg_parse_info()
    sent = await message.answer(
        f"🔄 Запускаю парсинг VK и RSS...\n\n{tg_info}\n⏰ TG парсится автоматически в 00:00 МСК"
    )
    await asyncio.gather(parse_vk_and_save(), parse_rss_and_save())
    await sent.delete()
    tg_info = _last_tg_parse_info()
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="◀ Главное меню", callback_data="parse_done_close")
    done_msg = await message.answer(
        f"✅ VK и RSS готово.\n\n{tg_info}\nИспользуйте /posts для просмотра новых постов.",
        reply_markup=kb.as_markup()
    )
    asyncio.create_task(delete_message(done_msg, 30))


@router.callback_query(F.data == "parse_done_close")
async def parse_done_close(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()
    from handlers.start import show_main_menu
    await show_main_menu(callback.message, state)
