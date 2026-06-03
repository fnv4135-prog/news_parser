"""
Telegram Parser — парсинг каналов через Telethon (user account)
С поддержкой скачивания фото
"""

import asyncio
import os
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from telethon import TelegramClient
from telethon.tl.types import Channel, Message, MessageMediaPhoto
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest


@dataclass
class TGPost:
    """Структура поста из Telegram"""
    post_id: str
    title: str
    text: str
    url: str
    author: str
    author_url: str
    published_at: Optional[datetime]
    image_url: Optional[str]
    media_urls: List[str] = None
    source: str = "telegram"

    def __post_init__(self):
        if self.media_urls is None:
            self.media_urls = [self.image_url] if self.image_url else []


class TelegramParser:
    """Парсер Telegram каналов через user account с поддержкой скачивания фото"""

    def __init__(self, api_id: int, api_hash: str, phone: str, 
                 session_name: str = "parser_session",
                 media_path: str = "media/telegram"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_name = session_name
        self.media_path = media_path
        self.client: Optional[TelegramClient] = None
        
        # Создаём папку для медиа
        Path(self.media_path).mkdir(parents=True, exist_ok=True)

    async def connect(self):
        """Подключение к Telegram"""
        self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        await self.client.connect()
        
        # Проверяем, авторизованы ли мы уже
        if await self.client.is_user_authorized():
            print("✅ Telegram: сессия активна")
        else:
            print("⚠️ Telegram: требуется авторизация")
            # Запрашиваем код только если не авторизованы
            await self.client.start(phone=self.phone)
            print("✅ Telegram: авторизация успешна")

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    def _extract_channel_username(self, channel_input: str) -> str:
        """Извлекает username канала из URL или строки"""
        channel_input = channel_input.replace("https://t.me/", "")
        channel_input = channel_input.replace("http://t.me/", "")
        channel_input = channel_input.replace("t.me/", "")
        channel_input = channel_input.replace("@", "")
        channel_input = channel_input.rstrip("/")
        return channel_input

    async def get_channel_posts(self, channel: str, limit: int = 20) -> List[TGPost]:
        """Получить посты из канала с правильной обработкой альбомов (grouped_id)"""
        username = self._extract_channel_username(channel)
        try:
            # Получаем entity канала
            if username.startswith("joinchat/"):
                invite_hash = username.split("/")[-1]
                entity = await self.client.get_entity(f"https://t.me/joinchat/{invite_hash}")
            else:
                entity = await self.client.get_entity(username)

            if not isinstance(entity, Channel):
                print(f"⚠️ {channel} — не канал")
                return []

            channel_name = entity.title
            channel_username = entity.username or str(entity.id)

            # Собираем все сообщения (берём больше лимита, т.к. альбомы = несколько сообщений = 1 пост)
            all_messages = []
            async for message in self.client.iter_messages(entity, limit=limit * 3):
                all_messages.append(message)

            # Группируем: альбомы по grouped_id, одиночки как есть
            albums = {}  # grouped_id -> list[Message]
            singles = []
            for msg in all_messages:
                if msg.grouped_id:
                    albums.setdefault(msg.grouped_id, []).append(msg)
                else:
                    singles.append(msg)

            posts = []
            # Одиночные сообщения
            for msg in singles:
                post = await self._message_to_post(msg, channel_name, channel_username)
                if post:
                    posts.append(post)

            # Альбомы — один пост на группу
            for group_id, msgs in albums.items():
                # Сортируем по id чтобы первое сообщение альбома было первым
                msgs.sort(key=lambda m: m.id)
                post = await self._album_to_post(msgs, channel_name, channel_username)
                if post:
                    posts.append(post)

            # Сортируем финально по дате (новые сверху) и обрезаем
            posts.sort(key=lambda p: p.published_at or datetime.min, reverse=True)
            return posts[:limit]

        except Exception as e:
            print(f"❌ Ошибка парсинга {channel}: {e}")
            return []

    async def _download_message_photo(self, message, post_id: str, idx: int = 0) -> Optional[str]:
        """Скачивает фото из сообщения. Возвращает абсолютный путь или None"""
        if not (message.photo or (message.media and isinstance(message.media, MessageMediaPhoto))):
            return None
        try:
            file_hash = hashlib.md5(f"{post_id}_{idx}".encode()).hexdigest()[:12]
            file_path = os.path.join(self.media_path, f"{file_hash}.jpg")

            if not os.path.exists(file_path):
                downloaded = await self.client.download_media(message.media, file=file_path)
                if downloaded:
                    return os.path.abspath(downloaded)
            else:
                return os.path.abspath(file_path)
        except Exception as e:
            print(f"  ⚠️ Не удалось скачать фото: {e}")
        return None

    async def _message_to_post(self, message: Message, channel_name: str, channel_username: str) -> Optional[TGPost]:
        """Конвертируем одиночное сообщение в TGPost"""
        try:
            if not message.text and not message.media:
                return None

            text = message.text or message.message or ""
            title = text.split("\n")[0][:100] if text else "Медиа"
            url = f"https://t.me/{channel_username}/{message.id}"
            post_id = f"tg_{channel_username}_{message.id}"

            # Скачиваем одно фото
            media_urls = []
            path = await self._download_message_photo(message, post_id, 0)
            if path:
                media_urls.append(path)
                print(f"  📷 Фото скачано: {os.path.basename(path)}")

            image_url = media_urls[0] if media_urls else None
            
            # Проверяем наличие видео
            if message.video and text:
                text = text.rstrip() + "\n\n🎬 К посту прикреплено видео"
            
            return TGPost(
                post_id=post_id,
                title=title,
                text=text[:10000],
                url=url,
                author=channel_name,
                author_url=f"https://t.me/{channel_username}",
                published_at=message.date,
                image_url=image_url,
                media_urls=media_urls
            )
        except Exception as e:
            print(f"Ошибка конвертации сообщения: {e}")
            return None

    async def _album_to_post(self, messages: List[Message], channel_name: str, channel_username: str) -> Optional[TGPost]:
        """Конвертируем альбом (несколько сообщений с одним grouped_id) в ОДИН TGPost"""
        try:
            if not messages:
                return None

            first = messages[0]
            # Ищем сообщение с текстом — обычно первое, но может быть и другое
            text = ""
            for m in messages:
                msg_text = m.text or m.message or ""
                if msg_text:
                    text = msg_text
                    break

            title = text.split("\n")[0][:100] if text else "Альбом"
            url = f"https://t.me/{channel_username}/{first.id}"
            # post_id по первому сообщению альбома
            post_id = f"tg_{channel_username}_{first.id}"

            # Скачиваем все фото (до 10 — лимит Telegram media_group)
            media_urls = []
            for i, msg in enumerate(messages[:10]):
                path = await self._download_message_photo(msg, post_id, i)
                if path:
                    media_urls.append(path)

            if media_urls:
                print(f"  📷 Альбом: {len(media_urls)} фото")

            if not text and not media_urls:
                return None

            # Проверяем наличие видео в альбоме
            has_video = any(m.video for m in messages)
            if has_video and text:
                text = text.rstrip() + "\n\n🎬 К посту прикреплено видео"

            image_url = media_urls[0] if media_urls else None
            return TGPost(
                post_id=post_id,
                title=title,
                text=text[:10000],
                url=url,
                author=channel_name,
                author_url=f"https://t.me/{channel_username}",
                published_at=first.date,
                image_url=image_url,
                media_urls=media_urls
            )
        except Exception as e:
            print(f"Ошибка конвертации альбома: {e}")
            return None

    async def parse_multiple_channels(self, channels: List[str], posts_per_channel: int = 10) -> List[TGPost]:
        all_posts = []
        for channel in channels:
            try:
                posts = await self.get_channel_posts(channel, posts_per_channel)
                all_posts.extend(posts)
                print(f"✓ {channel}: {len(posts)} постов")
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"✗ {channel}: ошибка - {e}")
        return all_posts

    async def join_channel(self, channel: str) -> bool:
        """Подписаться на канал (для приватных)"""
        try:
            username = self._extract_channel_username(channel)
            if username.startswith("joinchat/"):
                invite_hash = username.split("/")[-1]
                await self.client(ImportChatInviteRequest(invite_hash))
            else:
                entity = await self.client.get_entity(username)
                await self.client(JoinChannelRequest(entity))
            print(f"✅ Подписан на {channel}")
            return True
        except Exception as e:
            print(f"❌ Не удалось подписаться на {channel}: {e}")
            return False


# ==================== ТЕСТ ====================

async def test_parser():
    """Тестируем Telegram парсер с поддержкой фото"""
    from dotenv import load_dotenv
    load_dotenv()

    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    phone = os.getenv("TG_PHONE")

    if not all([api_id, api_hash, phone]):
        print("❌ TG_API_ID, TG_API_HASH или TG_PHONE не заданы в .env")
        return

    print("🔍 Тестируем Telegram парсер с поддержкой фото...\n")

    test_channels = [
        "novgorodtop",
        "novgorod_smi",
    ]

    async with TelegramParser(int(api_id), api_hash, phone, media_path="media/telegram") as parser:
        posts = await parser.parse_multiple_channels(test_channels, posts_per_channel=5)

        if not posts:
            print("\n❌ Посты не получены")
            return

        print(f"\n✅ Всего постов: {len(posts)}\n")
        
        photos_count = sum(1 for p in posts if p.image_url)
        print(f"📷 Постов с фото: {photos_count}\n")
        
        for i, post in enumerate(posts[:5], 1):
            print(f"{i}. {post.title[:50]}...")
            print(f"   👤 {post.author}")
            print(f"   📅 {post.published_at}")
            print(f"   🔗 {post.url}")
            if post.image_url:
                print(f"   🖼 {post.image_url}")
            print()


if __name__ == "__main__":
    asyncio.run(test_parser())