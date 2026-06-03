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
    def __init__(self, feed_url: str, source_name: str):
        self.feed_url = feed_url
        self.source_name = source_name
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        # Настройки SSL для обхода ошибок сертификатов
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
                    logging.error(f"RSS {self.feed_url} вернул {resp.status}")
                    return []
                text = await resp.text()
                feed = feedparser.parse(text)
                if feed.bozo:
                    logging.warning(f"Feedparser warning: {feed.bozo_exception}")
                posts = []
                for entry in feed.entries[:20]:
                    post = self._entry_to_post(entry)
                    if post:
                        posts.append(post)
                return posts
        except Exception as e:
            logging.error(f"Ошибка парсинга RSS {self.feed_url}: {e}")
            return []

    def _entry_to_post(self, entry) -> Optional[RSSPost]:
        try:
            post_id = f"rss_{self.source_name}_{entry.get('id', entry.get('link', ''))}"
            title = entry.get('title', 'Без заголовка')
            # Текст: берём summary или description, удаляем HTML
            text = entry.get('summary', entry.get('description', ''))
            if text:
                text = re.sub(r'<[^>]+>', '', text)
                text = text.strip()
            url = entry.get('link', '')
            # Дата
            published = None
            if 'published_parsed' in entry and entry.published_parsed:
                # published_parsed – это time.struct_time
                published = datetime.fromtimestamp(time.mktime(entry.published_parsed))
            elif 'published' in entry:
                try:
                    published = datetime.strptime(entry.published, "%a, %d %b %Y %H:%M:%S %z")
                except:
                    pass
            # Картинка
            image_url = None
            if 'media_content' in entry:
                image_url = entry.media_content[0].get('url')
            elif 'links' in entry:
                for link in entry.links:
                    if link.get('type', '').startswith('image/'):
                        image_url = link.get('href')
                        break
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
            logging.error(f"Ошибка конвертации RSS записи: {e}")
            return None