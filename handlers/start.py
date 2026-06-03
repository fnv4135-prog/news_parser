from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # Очищаем FSM состояние
    await message.answer(
        "📌 Доступные команды:\n\n"
        "/posts - просмотр и публикация постов\n"
        "/cities - управление городами\n"
        "/sources - источники (VK, Telegram, RSS)\n"
        "/channels - каналы для публикации и подписи\n"
        "/add_channel - добавить канал для публикации\n"
        "/ad - рекламное сообщение\n"
        "/parse_now - запустить парсинг вручную\n"
        "/scheduled - отложенные посты\n"
        "/help - подробная инструкция"
    )
    await message.delete()