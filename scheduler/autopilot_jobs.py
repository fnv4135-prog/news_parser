import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict

from database import Database
from bot_instance import get_bot
from config import ADMIN_IDS

db = Database()

# Хранилище message_id сводки: admin_id -> message_id
# Сбрасывается при новом дне
_plan_summary: dict = {}   # admin_id -> message_id
_plan_summary_date: str = ""  # дата последней сводки YYYY-MM-DD
_plan_cities_count: int = 0   # сколько городов уже в сводке


def _normalize_title(text: str) -> str:
    """Первые 50 символов текста для сравнения дублей"""
    if not text:
        return ""
    return text.strip()[:50].lower()


def _build_plan(folder_id: int, posts_per_day: int, slots: List[str]) -> List[Dict]:
    """
    Подбирает посты для плана:
    - берём свежие посты из БД
    - убираем дубли по заголовку
    - возвращаем не больше posts_per_day
    """
    candidates = db.get_posts_for_autopilot(folder_id, limit=posts_per_day * 5)
    seen_titles = set()
    selected = []
    for post in candidates:
        norm = _normalize_title(post.get('text') or post.get('title') or '')
        if norm and norm in seen_titles:
            continue
        seen_titles.add(norm)
        selected.append(post)
        if len(selected) >= posts_per_day:
            break
    return selected


def _slots_to_datetimes(slots: List[str], now_utc: datetime) -> List[datetime]:
    """
    Переводит список слотов ['09:00', '12:00', ...] в datetime UTC на сегодня.
    Слоты в МСК (UTC+3).
    """
    result = []
    for slot in slots:
        h, m = map(int, slot.split(":"))
        # Слот в МСК (UTC+3) → UTC
        dt_utc = now_utc.replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(hours=3)
        # Корректируем дату если слот уже прошёл — ставим на завтра
        if dt_utc <= now_utc:
            dt_utc += timedelta(days=1)
        result.append(dt_utc)
    return result


async def build_autopilot_plan(folder_id: int):
    """Формирует план публикаций для одного города"""
    settings = db.get_autopilot_settings(folder_id)
    if not settings or not settings['is_enabled']:
        return

    folder = db.get_folder_by_id(folder_id)
    folder_name = folder['name'] if folder else f"Город #{folder_id}"

    posts_per_day = settings['posts_per_day']
    slots = settings['slots']

    if not slots:
        logging.warning(f"Автопилот [{folder_name}]: нет слотов, пропускаем")
        return

    # Занятые слоты сегодня
    occupied = db.get_scheduled_slots_today(folder_id)

    # Свободные слоты
    free_slots = [s for s in slots if s not in occupied]
    if not free_slots:
        logging.info(f"Автопилот [{folder_name}]: все слоты заняты")
        return

    # Подбираем посты
    needed = min(len(free_slots), posts_per_day)
    posts = _build_plan(folder_id, needed, free_slots)

    if not posts:
        logging.warning(f"Автопилот [{folder_name}]: нет подходящих постов")
        # Уведомляем админов
        bot = get_bot()
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ Автопилот — {folder_name}\n\nНе найдено постов для плана на сегодня."
                )
            except Exception as e:
                logging.error(f"Ошибка уведомления админа {admin_id}: {e}")
        return

    # Получаем каналы публикации
    channels = db.get_publish_channels_by_folder(folder_id)
    if not channels:
        logging.warning(f"Автопилот [{folder_name}]: нет каналов публикации")
        return
    channel_ids = [c['channel_id'] for c in channels]
    signature = channels[0].get('signature') if channels else None

    # Распределяем по слотам
    now_utc = datetime.utcnow()
    slot_times = _slots_to_datetimes(free_slots[:len(posts)], now_utc)

    scheduled_ids = []
    for post, slot_time in zip(posts, slot_times):
        sid = db.add_scheduled_post(
            post_id=post['id'],
            channel_ids=channel_ids,
            scheduled_at=slot_time,
            text=post.get('text'),
            image_url=post.get('image_url'),
            signature=signature,
            folder_id=folder_id,
            media_list=post.get('media_urls'),
        )
        scheduled_ids.append((sid, post, slot_time))

    logging.info(f"Автопилот [{folder_name}]: запланировано {len(scheduled_ids)} постов")
    # Сразу отправляем сводку
    await send_morning_report(folder_id)
    return scheduled_ids, folder_name


async def send_morning_report(folder_id: int):
    """Обновляет единую сводку плана для всех городов."""
    global _plan_summary, _plan_summary_date, _plan_cities_count

    settings = db.get_autopilot_settings(folder_id)
    if not settings or not settings['is_enabled']:
        return

    scheduled = db.get_scheduled_by_folder(folder_id)
    if not scheduled:
        return

    bot = get_bot()
    today = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")

    # Сброс при новом дне
    if _plan_summary_date != today:
        _plan_summary = {}
        _plan_summary_date = today
        _plan_cities_count = 0

    _plan_cities_count += 1

    # Считаем общее число городов и постов в плане на сегодня
    all_settings = db.get_all_autopilot_settings()
    total_posts = 0
    for s in all_settings:
        if s['is_enabled']:
            posts = db.get_scheduled_by_folder(s['folder_id'])
            total_posts += len(posts)

    text = (
        f"📅 Сегодня запланированы публикации в <b>{_plan_cities_count}</b> город(ах)\n"
        f"Всего постов: <b>{total_posts}</b>"
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Подробнее", switch_inline_query_current_chat="/today")]
    ])

    for admin_id in ADMIN_IDS:
        try:
            if admin_id in _plan_summary:
                # Редактируем существующее
                try:
                    await bot.edit_message_text(
                        text, chat_id=admin_id,
                        message_id=_plan_summary[admin_id],
                        reply_markup=kb, parse_mode="HTML"
                    )
                except Exception:
                    # Если не удалось отредактировать — шлём новое
                    msg = await bot.send_message(admin_id, text, reply_markup=kb, parse_mode="HTML")
                    _plan_summary[admin_id] = msg.message_id
            else:
                msg = await bot.send_message(admin_id, text, reply_markup=kb, parse_mode="HTML")
                _plan_summary[admin_id] = msg.message_id
        except Exception as e:
            logging.error(f"Ошибка отправки сводки админу {admin_id}: {e}")


async def run_autopilot_planner():
    """
    Джоба планировщика — запускается каждую минуту,
    проверяет у каких городов пришло время формировать план.
    """
    now_msk = datetime.utcnow() + timedelta(hours=3)
    current_time = now_msk.strftime("%H:%M")

    all_settings = db.get_all_autopilot_settings()
    for settings in all_settings:
        if settings['plan_time'] == current_time:
            try:
                await build_autopilot_plan(settings['folder_id'])
            except Exception as e:
                logging.error(f"Ошибка планировщика для папки {settings['folder_id']}: {e}")


async def run_autopilot_reporter():
    """
    Джоба отправки сводки — запускается каждую минуту,
    проверяет у каких городов пришло время слать сводку.
    """
    now_msk = datetime.utcnow() + timedelta(hours=3)
    current_time = now_msk.strftime("%H:%M")

    all_settings = db.get_all_autopilot_settings()
    for settings in all_settings:
        if settings['report_time'] == current_time:
            try:
                await send_morning_report(settings['folder_id'])
            except Exception as e:
                logging.error(f"Ошибка отправки сводки для папки {settings['folder_id']}: {e}")
