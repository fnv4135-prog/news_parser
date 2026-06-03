import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from handlers import start, help, post_edit, publish_channels, ad, sources, schedule, parse_now, posts, folders
from scheduler.jobs import setup_scheduler
from bot_instance import set_bot

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в .env")

bot = Bot(token=BOT_TOKEN)
set_bot(bot)

# Используем MemoryStorage для FSM
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Роутеры - порядок больше не критичен благодаря FSM
dp.include_routers(
    start.router,
    help.router,
    post_edit.router,
    publish_channels.router,
    ad.router,
    sources.router,
    schedule.router,
    parse_now.router,
    posts.router,
    folders.router,
)

async def main():
    logging.basicConfig(level=logging.INFO)
    setup_scheduler()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())