"""
Работа с Telegraph API (telegra.ph).
Используется для публикации длинных постов — Telegram показывает нативное Instant View.

Access token получается один раз автоматически при первом вызове и сохраняется в БД.
"""
import os
import json
import logging
import aiohttp
from typing import Optional, List
from urllib.parse import urlparse

from database import Database

TELEGRAPH_API = "https://api.telegra.ph"
_db = Database()
_cached_token: Optional[str] = None


def _ensure_settings_table():
    """Создаёт таблицу settings если нет (для хранения telegraph_token)"""
    conn = _db.get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()


def _get_setting(key: str) -> Optional[str]:
    conn = _db.get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def _set_setting(key: str, value: str):
    conn = _db.get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()


async def _create_account(short_name: str = "NewsBot") -> Optional[str]:
    """Создаёт анонимный Telegraph аккаунт, возвращает access_token"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{TELEGRAPH_API}/createAccount",
                data={
                    "short_name": short_name,
                    "author_name": short_name
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                if data.get('ok'):
                    return data['result']['access_token']
                logging.error(f"Telegraph createAccount fail: {data}")
                return None
    except Exception as e:
        logging.error(f"Telegraph createAccount error: {e}")
        return None


async def get_access_token() -> Optional[str]:
    """Получает access_token (из кэша / БД / создаёт новый)"""
    global _cached_token
    if _cached_token:
        return _cached_token

    _ensure_settings_table()
    token = _get_setting('telegraph_access_token')
    if token:
        _cached_token = token
        return token

    # Токена нет — создаём
    logging.info("Telegraph: создаю новый аккаунт...")
    token = await _create_account()
    if token:
        _set_setting('telegraph_access_token', token)
        _cached_token = token
        logging.info("Telegraph: аккаунт создан, токен сохранён в БД")
    return token


def _text_to_nodes(text: str) -> List[dict]:
    """Преобразует текст (с возможным HTML) в массив Telegraph Node'ов (параграфы).
    Поддерживает: <a href>, <b>, <i>, <strong>, <em>, <code>"""
    import re
    
    def _parse_inline(s: str) -> list:
        """Парсит строку с HTML-тегами в список Telegraph children"""
        children = []
        # Паттерн для HTML-тегов: <a href="...">, <b>, <i>, <strong>, <em>, <code>
        pattern = re.compile(
            r'<a\s+href=["\']([^"\']+)["\']>(.*?)</a>'
            r'|<(b|strong)>(.*?)</\3>'
            r'|<(i|em)>(.*?)</\5>'
            r'|<code>(.*?)</code>',
            re.DOTALL
        )
        last_end = 0
        for m in pattern.finditer(s):
            # Текст до тега
            if m.start() > last_end:
                children.append(s[last_end:m.start()])
            
            if m.group(1) is not None:
                # <a href="...">text</a>
                children.append({
                    "tag": "a",
                    "attrs": {"href": m.group(1)},
                    "children": [m.group(2)]
                })
            elif m.group(4) is not None:
                # <b> / <strong>
                children.append({"tag": "b", "children": [m.group(4)]})
            elif m.group(6) is not None:
                # <i> / <em>
                children.append({"tag": "i", "children": [m.group(6)]})
            elif m.group(7) is not None:
                # <code>
                children.append({"tag": "code", "children": [m.group(7)]})
            
            last_end = m.end()
        
        # Остаток текста после последнего тега
        if last_end < len(s):
            children.append(s[last_end:])
        
        return children if children else [s]
    
    nodes = []
    paragraphs = text.split('\n\n')
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        lines = para.split('\n')
        children = []
        for i, line in enumerate(lines):
            if i > 0:
                children.append({"tag": "br"})
            if line:
                parsed = _parse_inline(line)
                children.extend(parsed)
        nodes.append({"tag": "p", "children": children})
    return nodes


def _image_to_node(image_url: str) -> Optional[dict]:
    """Готовит Node для картинки.
    Для локальных файлов возвращает None (нужна предварительная загрузка на telegra.ph)."""
    if not image_url:
        return None
    # Только HTTP/HTTPS картинки (локальные файлы нельзя встроить без загрузки)
    parsed = urlparse(image_url)
    if parsed.scheme not in ('http', 'https'):
        return None
    return {
        "tag": "figure",
        "children": [
            {"tag": "img", "attrs": {"src": image_url}}
        ]
    }


async def _upload_local_image(file_path: str) -> Optional[str]:
    """Загружает локальный файл на telegra.ph и возвращает URL.
    Использует недокументированный endpoint /upload."""
    if not os.path.isfile(file_path):
        return None
    try:
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(file_path),
                               content_type='image/jpeg')
                async with session.post(
                    "https://telegra.ph/upload",
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    data = await resp.json()
                    if isinstance(data, list) and data and 'src' in data[0]:
                        return f"https://telegra.ph{data[0]['src']}"
                    logging.warning(f"Telegraph upload fail: {data}")
                    return None
    except Exception as e:
        logging.error(f"Telegraph upload error: {e}")
        return None


async def create_page(
    title: str,
    text: str,
    image_urls: Optional[List[str]] = None,
    author_name: str = ""
) -> Optional[str]:
    """
    Создаёт Telegraph статью. Возвращает URL или None при ошибке.

    :param title: заголовок (до 256 симв)
    :param text: полный текст статьи
    :param image_urls: список путей или URL картинок (локальные будут загружены)
    :param author_name: имя автора (показывается в статье)
    """
    token = await get_access_token()
    if not token:
        logging.error("Telegraph: нет access_token")
        return None

    # Готовим контент
    content = []

    # Сначала все фото
    if image_urls:
        for img in image_urls:
            if not img:
                continue
            # Локальные файлы — загружаем на telegra.ph
            if os.path.isfile(img):
                uploaded = await _upload_local_image(img)
                if uploaded:
                    node = _image_to_node(uploaded)
                    if node:
                        content.append(node)
            else:
                node = _image_to_node(img)
                if node:
                    content.append(node)

    # Потом текст
    content.extend(_text_to_nodes(text))

    if not content:
        return None

    # Заголовок должен быть непустым
    clean_title = (title or "Новость").strip()
    if not clean_title:
        clean_title = "Новость"
    clean_title = clean_title[:256]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{TELEGRAPH_API}/createPage",
                data={
                    "access_token": token,
                    "title": clean_title,
                    "author_name": (author_name or "")[:128],
                    "content": json.dumps(content, ensure_ascii=False),
                    "return_content": "false"
                },
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                data = await resp.json()
                if data.get('ok'):
                    return data['result']['url']
                logging.error(f"Telegraph createPage fail: {data}")
                return None
    except Exception as e:
        logging.error(f"Telegraph createPage error: {e}")
        return None
