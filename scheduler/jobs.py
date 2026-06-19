import logging
import asyncio
import random
import functools
import os
import time

from aiogram.types import InputMediaVideo, InputMediaPhoto, FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import PARSE_INTERVAL, MAX_POSTS_PER_SOURCE, MEDIA_PATH, MEDIA_PATH_VK, ADMIN_IDS, ADMIN_IDS
from database import Database
from parsers.vk_parser import VKParser
from parsers.tg_parser import TelegramParser
from parsers.rss_parser import RSSParser
from utils.text_cleaner import clean_text, is_ad_post
from bot_instance import get_bot
from scheduler.autopilot_jobs import run_autopilot_planner, run_autopilot_reporter

db = Database()
scheduler = AsyncIOScheduler()

last_tg_parse_time = None  # Время последнего TG парсинга

async def parse_vk_and_save():
    logging.info("Запуск парсинга VK...")
    token = os.getenv("VK_TOKEN")
    if not token:
        logging.warning("VK_TOKEN не задан")
        return
    # Получаем все активные VK-источники из БД
    sources = db.get_all_active_sources()
    vk_sources = [s for s in sources if s['type'] == 'vk']
    if not vk_sources:
        logging.info("Нет VK источников для парсинга")
        return
    # Round-robin по городам, макс 20 на город
    from collections import defaultdict
    buckets = defaultdict(list)
    for s in vk_sources:
        buckets[s['folder_id']].append(s)
    rr_sources = []
    max_len = min(max((len(v) for v in buckets.values()), default=0), 20)
    for i in range(max_len):
        for folder_id in buckets:
            if i < len(buckets[folder_id]):
                rr_sources.append(buckets[folder_id][i])
    vk_sources = rr_sources

    async with VKParser(token, media_path=MEDIA_PATH_VK) as parser:
        new_count = 0
        skipped = 0
        for src in vk_sources:
            await asyncio.sleep(random.uniform(3, 5))
            group_id = src['value']
            folder_id = src['folder_id']
            posts = await parser.get_wall_posts(group_id, count=MAX_POSTS_PER_SOURCE)
            for post in posts:
                # Фильтр: без фото и длиннее 4096 → пропускаем (не поместится одним сообщением)
                if not post.media_urls and len(post.text or '') > 4096:
                    skipped += 1
                    continue
                if is_ad_post(post.text or ''):
                    skipped += 1
                    continue
                stop_word = await db.run_async(db.post_has_stop_words, post.text or '')
                if stop_word:
                    log.debug(f"Пост заблокирован стоп-словом '{stop_word}': {post.post_id}")
                    skipped += 1
                    continue
                urgent_word = await db.run_async(db.post_has_urgent_words, post.text or '')
                if not await db.run_async(db.post_exists, post.post_id):
                    saved_id = await db.run_async(functools.partial(
                        db.add_post,
                        post_id=post.post_id,
                        source=post.source,
                        source_name=post.author,
                        title=post.title,
                        text=clean_text(post.text or ""),
                        url=post.url,
                        author=post.author,
                        image_url=post.image_url,
                        media_urls=post.media_urls,
                        published_at=post.published_at,
                        folder_id=folder_id
                    ))
                    new_count += 1
                    if urgent_word and saved_id:
                        await notify_urgent_post(saved_id, {'text': post.text, 'folder_id': folder_id}, urgent_word)
        logging.info(f"VK парсинг завершён. Добавлено: {new_count}, пропущено длинных: {skipped}")

async def parse_telegram_and_save():
    logging.info("Запуск парсинга Telegram...")
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    phone = os.getenv("TG_PHONE")
    if not all([api_id, api_hash, phone]):
        logging.warning("Telegram парсинг пропущен: не заданы TG_API_ID, TG_API_HASH или TG_PHONE")
        return
    sources = db.get_all_active_sources()
    tg_sources = [s for s in sources if s['type'] == 'telegram']
    random.shuffle(tg_sources)
    if not tg_sources:
        logging.info("Нет Telegram источников для парсинга")
        return
    async with TelegramParser(int(api_id), api_hash, phone, media_path=MEDIA_PATH) as parser:
        new_count = 0
        skipped = 0
        for src in tg_sources:
            await asyncio.sleep(random.uniform(3, 5))
            channel = src['value']
            folder_id = src['folder_id']
            posts = await parser.get_channel_posts(channel, limit=MAX_POSTS_PER_SOURCE)
            for post in posts:
                if not post.media_urls and len(post.text or '') > 4096:
                    skipped += 1
                    continue
                if is_ad_post(post.text or ''):
                    skipped += 1
                    continue
                stop_word = await db.run_async(db.post_has_stop_words, post.text or '')
                if stop_word:
                    log.debug(f"Пост заблокирован стоп-словом '{stop_word}': {post.post_id}")
                    skipped += 1
                    continue
                urgent_word = await db.run_async(db.post_has_urgent_words, post.text or '')
                if not await db.run_async(db.post_exists, post.post_id):
                    saved_id = await db.run_async(functools.partial(
                        db.add_post,
                        post_id=post.post_id,
                        source=post.source,
                        source_name=post.author,
                        title=post.title,
                        text=clean_text(post.text or ""),
                        url=post.url,
                        author=post.author,
                        image_url=post.image_url,
                        media_urls=post.media_urls,
                        published_at=post.published_at,
                        folder_id=folder_id
                    ))
                    new_count += 1
                    if urgent_word and saved_id:
                        await notify_urgent_post(saved_id, {'text': post.text, 'folder_id': folder_id}, urgent_word)
        global last_tg_parse_time
    import datetime
    last_tg_parse_time = datetime.datetime.now()
    logging.info(f"Telegram парсинг завершён. Добавлено: {new_count}, пропущено длинных: {skipped}")

async def parse_rss_and_save():
    logging.info("Запуск парсинга RSS...")
    sources = db.get_all_active_sources()
    rss_sources = [s for s in sources if s['type'] == 'rss']
    if not rss_sources:
        logging.info("Нет RSS источников для парсинга")
        return
    new_count = 0
    skipped = 0
    for src in rss_sources:
        feed_url = src['value']
        source_name = src['name'] or feed_url
        folder_id = src['folder_id']
        async with RSSParser(feed_url, source_name) as parser:
            posts = await parser.fetch_feed()
            for post in posts:
                if not post.media_urls and len(post.text or '') > 4096:
                    skipped += 1
                    continue
                if is_ad_post(post.text or ''):
                    skipped += 1
                    continue
                stop_word = await db.run_async(db.post_has_stop_words, post.text or '')
                if stop_word:
                    log.debug(f"Пост заблокирован стоп-словом '{stop_word}': {post.post_id}")
                    skipped += 1
                    continue
                if not await db.run_async(db.post_exists, post.post_id):
                    urgent_word = await db.run_async(db.post_has_urgent_words, post.text or '')
                    saved_id = await db.run_async(functools.partial(
                        db.add_post,
                        post_id=post.post_id,
                        source=post.source,
                        source_name=post.author,
                        title=post.title,
                        text=clean_text(post.text or ""),
                        url=post.url,
                        author=post.author,
                        image_url=post.image_url,
                        media_urls=post.media_urls,
                        published_at=post.published_at,
                        folder_id=folder_id
                    ))
                    new_count += 1
                    if urgent_word and saved_id:
                        await notify_urgent_post(saved_id, {'text': post.text, 'folder_id': folder_id}, urgent_word)
    logging.info(f"RSS парсинг завершён. Добавлено: {new_count}, пропущено длинных: {skipped}")

def get_photo_input(image_url: str):
    """
    Определяет тип фото и возвращает нужный формат для отправки.
    Если это локальный файл — возвращает FSInputFile.
    Если URL или file_id — возвращает как есть.
    """
    import os
    if image_url and os.path.isfile(image_url):
        return FSInputFile(image_url)
    return image_url


async def _send_scheduled_ad(bot, channel_id, text, media_list_json):
    """Отдельная логика для рекламных постов — у них media_list содержит file_id от Telegram"""
    import json as _json
    if media_list_json:
        media_list = _json.loads(media_list_json)
        if len(text) > 1024:
            split_pos = 1020
            for i in range(1020, 800, -1):
                if text[i] in '.!?\n':
                    split_pos = i + 1
                    break
            else:
                for i in range(1020, 800, -1):
                    if text[i] == ' ':
                        split_pos = i
                        break
            caption_text = text[:split_pos].strip()
            continuation_text = text[split_pos:].strip()
            if len(continuation_text) > 4096:
                continuation_text = continuation_text[:4093] + "..."
        else:
            caption_text = text
            continuation_text = None

        media_group = []
        for i, m in enumerate(media_list):
            if m['type'] == 'photo':
                media_group.append(InputMediaPhoto(
                    media=m['file_id'],
                    caption=caption_text if i == 0 else None,
                    parse_mode="HTML" if i == 0 else None
                ))
            else:
                media_group.append(InputMediaVideo(
                    media=m['file_id'],
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
        if len(text) > 4096:
            text = text[:4093] + "..."
        await bot.send_message(
            chat_id=channel_id, text=text,
            parse_mode="HTML", disable_web_page_preview=True
        )


async def notify_urgent_post(post_id: int, post: dict, urgent_word: str) -> None:
    """Помечает пост как срочный и отправляет тихое уведомление."""
    try:
        # Помечаем пост как срочный в БД
        db.mark_post_urgent(post_id, urgent_word)
        # Считаем сколько непросмотренных
        count = db.get_urgent_count()
        bot = get_bot()
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"⚡️ Срочных новостей: <b>{count}</b>\n\nНажмите /urgent для просмотра",
                    parse_mode="HTML",
                    disable_notification=True
                )
            except Exception as e:
                log.error(f"[URGENT] Ошибка уведомления админу {admin_id}: {e}")
    except Exception as e:
        log.error(f"[URGENT] notify_urgent_post: {e}")


async def check_scheduled_posts():
    from utils.post_sender import send_post
    import json as _json

    bot = get_bot()
    scheduled = db.get_pending_scheduled()
    for sch in scheduled:
        channel_ids = sch['channel_ids']

        # Сразу помечаем как отправляемый — защита от дублей
        db.mark_scheduled_done(sch['id'])

        if sch.get('is_ad'):
            # Рекламный пост — оставляем специальную логику (file_id от Telegram)
            text = sch['text']
            media_list_json = sch.get('media_list')
            for channel_id in channel_ids:
                try:
                    await _send_scheduled_ad(bot, channel_id, text, media_list_json)
                    db.add_to_history(sch['post_id'], channel_id)
                except Exception as e:
                    logging.error(f"Ошибка отправки отложенной рекламы {sch['id']} в канал {channel_id}: {e}")
        else:
            # Обычный отложенный пост — используем единую send_post
            post = db.get_post_by_id(sch['post_id']) if sch['post_id'] else None

            # Готовим dict для send_post. Приоритет: sch (могли редактировать текст), затем post.
            text = sch.get('text') or (post['text'] if post else '')
            signature = sch.get('signature')

            # media_urls для scheduled: сначала пробуем sch['media_list'], потом post['media_urls']
            media_urls_source = sch.get('media_list') or (post.get('media_urls') if post else None)

            fake_post = {
                'id': post['id'] if post else None,
                'text': text,
                'title': post.get('title') if post else (text.split('\n')[0][:100] if text else 'Новость'),
                'author': post.get('author') if post else '',
                'image_url': sch.get('image_url') or (post.get('image_url') if post else None),
                'media_urls': media_urls_source,
                'telegraph_url': post.get('telegraph_url') if post else None,
            }

            for channel_id in channel_ids:
                try:
                    ok = await send_post(bot, channel_id, fake_post, signature=signature, db=db)
                    if ok:
                        if post:
                            db.mark_as_posted(post['id'])
                        db.add_to_history(sch['post_id'], channel_id)
                except Exception as e:
                    logging.error(f"Ошибка отправки отложенного поста {sch['id']} в канал {channel_id}: {e}")


def cleanup_old_posts():
    """Комплексная очистка: посты из БД + осиротевшие медиафайлы"""
    import os
    import glob

    result = db.cleanup_old_posts(days=3)

    if result['posts'] > 0 or result['scheduled'] > 0 or result['history'] > 0:
        logging.info(
            f"Очистка БД: постов={result['posts']}, "
            f"scheduled={result['scheduled']}, history={result['history']}"
        )

    # Очистка медиафайлов
    protected = db.get_protected_media_paths()
    deleted_files = 0

    for media_dir in ['media/telegram', 'media/vk']:
        if not os.path.isdir(media_dir):
            continue
        for filepath in glob.glob(os.path.join(media_dir, '*')):
            abs_path = os.path.abspath(filepath)
            if abs_path in protected:
                continue
            # Удаляем файл если его нет в image_url ни одного оставшегося поста/scheduled
            # Проще: удаляем всё что старше 3 дней по mtime
            try:
                age_days = (time.time() - os.path.getmtime(filepath)) / 86400
            except OSError:
                continue
            if age_days > 3:
                try:
                    os.remove(filepath)
                    deleted_files += 1
                except OSError:
                    pass

    if deleted_files > 0:
        logging.info(f"Очистка медиа: удалено {deleted_files} файлов")


def vacuum_database():
    """Сжатие БД — раз в неделю"""
    try:
        db.vacuum()
        logging.info("VACUUM выполнен")
    except Exception as e:
        logging.error(f"Ошибка VACUUM: {e}")


def setup_scheduler():
    scheduler.add_job(parse_vk_and_save, 'interval', seconds=PARSE_INTERVAL, max_instances=1)
    # scheduler.add_job(parse_telegram_and_save, 'interval', seconds=PARSE_INTERVAL, max_instances=1)
    scheduler.add_job(parse_telegram_and_save, 'cron', hour=21, minute=0, max_instances=1)  # 00:00 МСК
    scheduler.add_job(parse_rss_and_save, 'interval', seconds=PARSE_INTERVAL, max_instances=1)
    scheduler.add_job(check_scheduled_posts, 'interval', seconds=60)  # Проверка каждые 60 сек
    scheduler.add_job(cleanup_old_posts, 'interval', hours=1)  # Очистка раз в час
    scheduler.add_job(vacuum_database, 'cron', day_of_week='sun', hour=4)
    scheduler.add_job(run_autopilot_planner, 'interval', minutes=1)
    scheduler.add_job(run_autopilot_reporter, 'interval', minutes=1)
    scheduler.start()