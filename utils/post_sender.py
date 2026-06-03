"""
Единая логика публикации постов в Telegram каналы.
"""
import os
import json
import logging
from typing import List, Optional, Tuple
from aiogram.types import InputMediaPhoto, InputMediaVideo, FSInputFile

from utils.telegraph import create_page

# Лимиты Telegram
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096

# Запас под "Читать полностью →" и подпись в анонсе
ANNOUNCE_TRIM = 900


def get_media_urls(post: dict) -> List[str]:
    """Извлекает список media_urls из поста (с fallback на image_url)"""
    media_urls_raw = post.get('media_urls')
    if media_urls_raw:
        try:
            urls = json.loads(media_urls_raw) if isinstance(media_urls_raw, str) else media_urls_raw
            if isinstance(urls, list) and urls:
                return urls
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback на старое поле
    image_url = post.get('image_url')
    return [image_url] if image_url else []


def _photo_input(url_or_path: str):
    """FSInputFile если локальный файл, иначе URL/file_id как есть"""
    if url_or_path and os.path.isfile(url_or_path):
        return FSInputFile(url_or_path)
    return url_or_path


def _is_video_item(item) -> bool:
    """Проверяет является ли элемент media_urls видео"""
    return isinstance(item, dict) and item.get('type') == 'video'


def _get_media_id(item) -> str:
    """Извлекает file_id/url из элемента media_urls"""
    if isinstance(item, dict):
        return item.get('id', item.get('file_id', ''))
    return item


def _make_input_media(item, caption=None, parse_mode=None):
    """Создаёт InputMediaPhoto или InputMediaVideo из элемента media_urls"""
    media_id = _photo_input(_get_media_id(item))
    if _is_video_item(item):
        return InputMediaVideo(media=media_id, caption=caption, parse_mode=parse_mode)
    return InputMediaPhoto(media=media_id, caption=caption, parse_mode=parse_mode)


def _smart_trim(text: str, limit: int) -> str:
    """Обрезает текст по концу предложения или абзаца, не превышая limit"""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ['\n\n', '.\n', '. ', '! ', '? ', '\n']:
        idx = cut.rfind(sep)
        if idx > limit * 0.6:
            return cut[:idx + len(sep)].strip()
    idx = cut.rfind(' ')
    if idx > limit * 0.6:
        return cut[:idx].strip()
    return cut.strip()


async def _get_or_create_telegraph(post: dict, db, signature: str = None) -> Optional[str]:
    """Возвращает Telegraph URL: либо из БД, либо создаёт новый"""
    existing = post.get('telegraph_url')
    if existing:
        logging.info(f"[SEND] Telegraph: используем кэш {existing}")
        return existing

    media_urls = get_media_urls(post)
    text = post.get('text', '')
    if signature:
        text = f"{text}\n\n{signature}"

    title = post.get('title') or (text.split('\n')[0][:100] if text else 'Новость')

    logging.info(f"[SEND] Telegraph: создаю страницу, title={title[:50]}, text_len={len(text)}, images={len(media_urls)}")

    url = await create_page(
        title=title,
        text=text,
        image_urls=media_urls,
        author_name=""  # НЕ указываем автора — не показываем источник
    )

    if url:
        logging.info(f"[SEND] Telegraph: создана {url}")
        if post.get('id') and db:
            try:
                db.update_telegraph_url(post['id'], url)
            except Exception as e:
                logging.warning(f"[SEND] Не удалось сохранить telegraph_url: {e}")
    else:
        logging.error(f"[SEND] Telegraph: ОШИБКА создания страницы!")

    return url


async def send_post(bot, channel_id: str, post: dict, signature: str = None, db=None) -> bool:
    """
    Отправляет пост в канал. Возвращает True если успешно.
    """
    media_urls = get_media_urls(post)
    text = post.get('text') or ''
    sig_part = f"\n\n{signature}" if signature else ""
    full_text = text + sig_part

    logging.info(
        f"[SEND] post_id={post.get('id')}, channel={channel_id}, "
        f"text_len={len(text)}, full_len={len(full_text)}, "
        f"media={len(media_urls)}, sig_len={len(sig_part)}"
    )

    try:
        # --- Случай 1: Нет медиа ---
        if not media_urls:
            if len(full_text) <= MESSAGE_LIMIT:
                logging.info(f"[SEND] Случай 1a: текст без медиа, влезает")
                await bot.send_message(
                    chat_id=channel_id, text=full_text,
                    parse_mode="HTML", disable_web_page_preview=True
                )
                return True
            logging.info(f"[SEND] Случай 1b: текст без медиа, длинный → Telegraph")
            tg_url = await _get_or_create_telegraph(post, db, signature)
            if not tg_url:
                trimmed = _smart_trim(text, MESSAGE_LIMIT - len(sig_part) - 10)
                await bot.send_message(
                    chat_id=channel_id, text=trimmed + sig_part,
                    parse_mode="HTML", disable_web_page_preview=True
                )
                return True
            announce = _smart_trim(text, ANNOUNCE_TRIM)
            msg = f"{announce}\n\n📖 <a href=\"{tg_url}\">Читать полностью</a>{sig_part}"
            await bot.send_message(
                chat_id=channel_id, text=msg,
                parse_mode="HTML", disable_web_page_preview=False
            )
            return True

        # --- Случай 2: Есть медиа, всё влезает в caption ---
        if len(full_text) <= CAPTION_LIMIT:
            logging.info(f"[SEND] Случай 2: медиа + короткий текст ({len(full_text)} <= {CAPTION_LIMIT})")
            if len(media_urls) == 1:
                mid = _photo_input(_get_media_id(media_urls[0]))
                if _is_video_item(media_urls[0]):
                    await bot.send_video(chat_id=channel_id, video=mid, caption=full_text or None, parse_mode="HTML")
                else:
                    await bot.send_photo(chat_id=channel_id, photo=mid, caption=full_text or None, parse_mode="HTML")
            else:
                group = [_make_input_media(item, caption=full_text if i == 0 else None, parse_mode="HTML" if i == 0 else None) for i, item in enumerate(media_urls[:10])]
                await bot.send_media_group(chat_id=channel_id, media=group)
            return True

        # --- Случай 3: Есть медиа + длинный текст → Telegraph + анонс ---
        logging.info(f"[SEND] Случай 3: медиа + длинный текст ({len(full_text)} > {CAPTION_LIMIT}) → Telegraph")
        tg_url = await _get_or_create_telegraph(post, db, signature)

        if tg_url:
            announce = _smart_trim(text, ANNOUNCE_TRIM)
            caption = f"{announce}\n\n📖 <a href=\"{tg_url}\">Читать полностью</a>{sig_part}"
            if len(caption) > CAPTION_LIMIT:
                overflow = len(caption) - CAPTION_LIMIT
                announce = _smart_trim(text, ANNOUNCE_TRIM - overflow - 20)
                caption = f"{announce}\n\n📖 <a href=\"{tg_url}\">Читать полностью</a>{sig_part}"
            logging.info(f"[SEND] Telegraph OK, caption_len={len(caption)}")
        else:
            logging.error(f"[SEND] Telegraph FAIL → fallback обрезка")
            caption = _smart_trim(full_text, CAPTION_LIMIT - 5) + "…"

        if len(media_urls) == 1:
            mid = _photo_input(_get_media_id(media_urls[0]))
            if _is_video_item(media_urls[0]):
                await bot.send_video(chat_id=channel_id, video=mid, caption=caption, parse_mode="HTML")
            else:
                await bot.send_photo(chat_id=channel_id, photo=mid, caption=caption, parse_mode="HTML")
        else:
            group = [_make_input_media(item, caption=caption if i == 0 else None, parse_mode="HTML" if i == 0 else None) for i, item in enumerate(media_urls[:10])]
            await bot.send_media_group(chat_id=channel_id, media=group)
        return True

    except Exception as e:
        logging.error(f"[SEND] Ошибка отправки в {channel_id}: {e}")
        return False


async def send_preview_media(message, media_urls: List[str]) -> List[int]:
    """Отправляет медиа в чат для предпросмотра (без caption).
    Возвращает список message_id отправленных сообщений."""
    if not media_urls:
        return []
    try:
        if len(media_urls) == 1:
            item = media_urls[0]
            mid = _photo_input(_get_media_id(item))
            if _is_video_item(item):
                sent = await message.answer_video(video=mid)
            else:
                sent = await message.answer_photo(photo=mid)
            return [sent.message_id]
        else:
            group = [_make_input_media(item) for item in media_urls[:10]]
            sent_list = await message.answer_media_group(media=group)
            return [m.message_id for m in sent_list]
    except Exception as e:
        logging.warning(f"Не удалось отправить предпросмотр медиа: {e}")
        return []
