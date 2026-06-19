from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="📰 Посты", callback_data="menu_posts")
    kb.button(text="⚡️ Срочные", callback_data="menu_urgent")
    kb.button(text="📅 План на сегодня", callback_data="menu_today")
    kb.button(text="🤖 Автопилот", callback_data="menu_autopilot")
    kb.button(text="🕒 Отложенные", callback_data="menu_scheduled")
    kb.button(text="🔄 Парсинг", callback_data="menu_parse")
    kb.button(text="🏙 Города", callback_data="menu_cities")
    kb.button(text="📡 Источники", callback_data="menu_sources")
    kb.button(text="📢 Каналы", callback_data="menu_channels")
    kb.button(text="🛑 Стоп-слова", callback_data="menu_stopwords")
    kb.button(text="📣 Реклама", callback_data="menu_ad")
    kb.button(text="❓ Помощь", callback_data="menu_help")
    kb.adjust(2)
    await message.answer(
        "👋 Привет! Выберите действие:",
        reply_markup=kb.as_markup()
    )
    try:
        await message.delete()
    except Exception:
        pass

@router.callback_query(lambda c: c.data and c.data.startswith("menu_"))
async def menu_callback(callback, state: FSMContext):
    from aiogram.types import Message as Msg
    commands = {
        "menu_posts": "/posts",
        "menu_urgent": "/urgent",
        "menu_today": "/today",
        "menu_autopilot": "/autopilot",
        "menu_scheduled": "/scheduled",
        "menu_parse": "/parse_now",
        "menu_cities": "/cities",
        "menu_sources": "/sources",
        "menu_channels": "/channels",
        "menu_stopwords": "/stopwords",
        "menu_ad": "/ad",
        "menu_help": "/help",
    }
    cmd = commands.get(callback.data)
    if cmd:
        await callback.answer()
        try:
            await callback.message.delete()
        except Exception:
            pass
        # Эмулируем команду
        fake = await callback.message.answer(cmd)
        await fake.delete()
        # Вызываем через send_message
        await callback.message.answer(f"Используйте команду {cmd}")
    else:
        await callback.answer()
