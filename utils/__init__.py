"""
Утилиты для бота.
"""

import asyncio
import logging
from functools import wraps
from typing import TypeVar, Callable, Any

from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError

logger = logging.getLogger(__name__)

T = TypeVar('T')


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Декоратор для повторных попыток при ошибках.
    
    Args:
        max_attempts: Максимум попыток
        delay: Начальная задержка в секундах
        backoff: Множитель задержки между попытками
        exceptions: Кортеж исключений для перехвата
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            current_delay = delay
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                
                except TelegramRetryAfter as e:
                    # Telegram просит подождать — ждём указанное время
                    wait_time = e.retry_after + 1
                    logger.warning(f"Telegram rate limit, ждём {wait_time}s")
                    await asyncio.sleep(wait_time)
                    last_exception = e
                    
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts:
                        logger.error(
                            f"Все {max_attempts} попыток исчерпаны для {func.__name__}: {e}"
                        )
                        raise
                    
                    logger.warning(
                        f"Попытка {attempt}/{max_attempts} не удалась для {func.__name__}: {e}. "
                        f"Повтор через {current_delay}s"
                    )
                    
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            
            raise last_exception
        
        return wrapper
    return decorator


def format_post_for_send(post: dict, signature: str = None) -> tuple[str, str, bool]:
    """
    Форматирует пост для отправки в Telegram.
    
    Returns:
        tuple: (text, image_url, separate)
        - separate=True: фото и текст отправляются отдельно
        - separate=False: фото с caption
    """
    text = post.get('text', '') or ''
    
    if signature:
        text = f"{text}\n\n{signature}".strip()
    
    image_url = post.get('image_url')
    separate = False
    
    if image_url:
        # Caption ограничен 1024 символами
        if len(text) > 1024:
            separate = True
            # Текст для отдельного сообщения — лимит 4096
            if len(text) > 4096:
                text = text[:4093] + "..."
        else:
            # Текст как caption
            if len(text) > 1024:
                text = text[:1021] + "..."
    else:
        # Только текст — лимит 4096
        if len(text) > 4096:
            text = text[:4093] + "..."
    
    return text, image_url, separate


def escape_html(text: str) -> str:
    """Экранирует HTML-символы для Telegram."""
    if not text:
        return ""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def truncate(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Обрезает текст до указанной длины."""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def format_datetime(dt) -> str:
    """Форматирует datetime для отображения."""
    if not dt:
        return "—"
    try:
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(dt)
