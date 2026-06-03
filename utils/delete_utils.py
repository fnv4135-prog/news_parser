import asyncio
from typing import Union
from aiogram.types import Message, CallbackQuery

async def delete_message(message: Union[Message, CallbackQuery], delay: int = 0):
    """Удаляет сообщение через заданную задержку (секунд). Поддерживает Message и CallbackQuery."""
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        if isinstance(message, CallbackQuery):
            await message.message.delete()
        else:
            await message.delete()
    except Exception:
        pass