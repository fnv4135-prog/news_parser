from config import MAX_POSTS_PER_SOURCE
import asyncio
import aiohttp
import feedparser
import ssl
import time
import re
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger(__name__)


@dataclass
class RSSPost:
    post_id: str
    title: str
    text: str
    url: str
    author: str
    author_url: str
    published_at: Optional[datetime]
    image_url: Optional[str]
    media_urls: List[str] = None
    source: str = "rss"

    def __post_init__(self):
        if self.media_urls is None:
            self.media_urls = [self.image_url] if self.image_url else []


class RSSParser:
    # Минимальная длина текста из фида при которой НЕ идём за полным текстом
    MIN_TEXT_LENGTH = 200

    def __init__(self, feed_url: str, source_name: str, fetch_full_text: bool = True):
        self.feed_url = feed_url
        self.source_name = source_name
        self.fetch_full_text = fetch_full_text
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*"
        }
        self.session = aiohttp.ClientSession(connector=connector, headers=headers)
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def fetch_feed(self) -> List[RSSPost]:
        try:
            async with self.session.get(self.feed_url, timeout=30) as resp:
                if resp.status != 200:
                    log.error(f"RSS {self.feed_url} вернул {resp.status}")
                    return []
                text = await resp.text()
                feed = feedparser.parse(text)
                if feed.bozo:
                    log.warning(f"Feedparser warning: {feed.bozo_exception}")
                posts = []
                for entry in feed.entries[:MAX_POSTS_PER_SOURCE]:
                    post = await self._entry_to_post(entry)
                    if post:
                        posts.append(post)
                return posts
        except Exception as e:
            log.error(f"Ошибка парсинга RSS {self.feed_url}: {e}")
            return []

    async def _fetch_full_text(self, url: str) -> Optional[str]:
        """Получает полный текст статьи по URL через readability."""
        try:
            async with self.session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                try:
                    from readability import Document
                    doc = Document(html)
                    text = re.sub(r'<[^>]+>', ' ', doc.summary())
                    text = re.sub(r'&nbsp;', ' ', text)
                    text = re.sub(r'&amp;', '&', text)
                    text = re.sub(r'&lt;', '<', text)
                    text = re.sub(r'&gt;', '>', text)
                    text = re.sub(r'&quot;', '"', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if len(text) > 100:
                        return text
                except Exception as e:
                    log.warning(f"readability failed for {url}: {e}")
                return None
        except Exception as e:
            log.warning(f"Не удалось получить полный текст {url}: {e}")
            return None

    async def _entry_to_post(self, entry) -> Optional[RSSPost]:
        try:
            post_id = f"rss_{self.source_name}_{entry.get('id', entry.get('link', ''))}"
            title = entry.get('title', 'Без заголовка')

            # Текст из фида
            text = entry.get('summary', entry.get('description', ''))
            if text:
                text = re.sub(r'<[^>]+>', '', text)
                text = re.sub(r'&nbsp;', ' ', text)
                text = re.sub(r'&amp;', '&', text)
                text = re.sub(r'\s+', ' ', text)
                text = text.strip()

            url = entry.get('link', '')

            # Получаем полный текст если фид даёт мало
            if self.fetch_full_text and url and len(text) < self.MIN_TEXT_LENGTH:
                full_text = await self._fetch_full_text(url)
                if full_text and len(full_text) > len(text):
                    text = full_text
                    log.debug(f"Получен полный текст для {url[:60]}: {len(text)} символов")

            # Дата
            published = None
            if 'published_parsed' in entry and entry.published_parsed:
                published = datetime.fromtimestamp(time.mktime(entry.published_parsed))
            elif 'published' in entry:
                try:
                    published = datetime.strptime(entry.published, "%a, %d %b %Y %H:%M:%S %z")
                except Exception:
                    pass

            # Картинка
            image_url = None
            if hasattr(entry, 'media_content') and entry.media_content:
                image_url = entry.media_content[0].get('url')
            if not image_url and hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
                image_url = entry.media_thumbnail[0].get('url')
            if not image_url and 'links' in entry:
                for link in entry.links:
                    if link.get('type', '').startswith('image/'):
                        image_url = link.get('href')
                        break
                    if link.get('rel') == 'enclosure' and 'image' in link.get('type', ''):
                        image_url = link.get('href')
                        break
            if not image_url:
                raw_html = entry.get('summary', entry.get('description', ''))
                img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html)
                if img_match:
                    image_url = img_match.group(1)

            return RSSPost(
                post_id=post_id,
                title=title[:200],
                text=text[:4000],
                url=url,
                author=self.source_name,
                author_url=self.feed_url,
                published_at=published,
                image_url=image_url
            )
        except Exception as e:
            log.error(f"Ошибка конвертации RSS записи: {e}")
            return None
