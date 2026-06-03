# bot_instance.py
from aiogram import Bot

_bot: Bot = None

def set_bot(bot: Bot):
    global _bot
    _bot = bot

def get_bot() -> Bot:
    if _bot is None:
        raise RuntimeError("Bot not initialized")
    return _bot