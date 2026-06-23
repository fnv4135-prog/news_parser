"""
VK Parser — парсинг групп через VK API
С поддержкой скачивания фото локально
"""

import asyncio
import aiohttp
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class VKPost:
    """Структура поста из ВК"""
    post_id: str
    title: str
    text: str
    url: str
    author: str
    author_url: str
    published_at: Optional[datetime]
    image_url: Optional[str]  # первое фото (для обратной совместимости)
    media_urls: List[str] = None  # все фото
    source: str = "vk"

    def __post_init__(self):
        if self.media_urls is None:
            self.media_urls = [self.image_url] if self.image_url else []


class VKParser:
    """Парсер групп ВКонтакте через API с локальным сохранением фото"""
    
    API_URL = "https://api.vk.com/method"
    API_VERSION = "5.131"
    
    def __init__(self, token: str, media_path: str = "media/vk"):
        self.token = token
        self.media_path = media_path
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Создаём папку для медиа
        Path(self.media_path).mkdir(parents=True, exist_ok=True)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def _download_vk_video(self, owner_id: int, video_id: int, access_key: str,
                                   post_id: str, max_duration: int = 90, max_size_mb: int = 50) -> Optional[str]:
        """Скачивает видео из VK. Возвращает локальный путь или None."""
        try:
            # Получаем прямые ссылки через video.get
            videos_str = f"{owner_id}_{video_id}"
            params = {"videos": videos_str, "extended": 1}
            if access_key:
                params["access_key"] = access_key
            response = await self._api_call("video.get", params)
            if not response:
                return None
            items = response.get("items", [])
            if not items:
                return None
            video = items[0]
            duration = video.get("duration", 0)
            if duration > max_duration:
                print(f"  ⏭ VK видео слишком длинное: {duration}с > {max_duration}с")
                return None
            files = video.get("files", {})
            # Берём лучшее доступное качество до 480p
            video_url = (files.get("mp4_480") or files.get("mp4_360") or
                        files.get("mp4_240") or files.get("mp4_144"))
            if not video_url:
                print(f"  ⚠️ VK видео: нет прямых ссылок")
                return None
            # Скачиваем
            file_hash = __import__('hashlib').md5(f"{post_id}_vkvideo".encode()).hexdigest()[:12]
            existing = next((
                __import__('os').path.join(self.media_path, f)
                for f in __import__('os').listdir(self.media_path)
                if f.startswith(file_hash)
            ), None)
            if existing:
                return __import__('os').path.abspath(existing)
            file_path = __import__('os').path.join(self.media_path, f"{file_hash}.mp4")
            print(f"  🎬 Скачиваю VK видео {duration}с...")
            async with self.session.get(video_url, timeout=120) as resp:
                if resp.status != 200:
                    print(f"  ⚠️ VK видео HTTP {resp.status}")
                    return None
                size = 0
                with open(file_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        size += len(chunk)
                        if size > max_size_mb * 1024 * 1024:
                            print(f"  ⏭ VK видео слишком большое: >  {max_size_mb}MB")
                            __import__('os').remove(file_path)
                            return None
                        f.write(chunk)
            print(f"  ✅ VK видео скачано: {__import__('os').path.basename(file_path)}")
            return __import__('os').path.abspath(file_path)
        except Exception as e:
            print(f"  ⚠️ Не удалось скачать VK видео: {e}")
            return None

    async def _api_call(self, method: str, params: dict, retries: int = 3) -> Optional[dict]:
        """Вызов VK API с обработкой rate limit"""
        params["access_token"] = self.token
        params["v"] = self.API_VERSION
        
        url = f"{self.API_URL}/{method}"
        
        for attempt in range(retries):
            try:
                async with self.session.get(url, params=params, timeout=30) as resp:
                    data = await resp.json()
                    
                    if "error" in data:
                        error = data["error"]
                        error_code = error.get('error_code')
                        
                        # Rate limit — ждём и повторяем
                        if error_code == 6:
                            wait_time = 1.5 * (attempt + 1)
                            print(f"VK rate limit, жду {wait_time} сек...")
                            await asyncio.sleep(wait_time)
                            continue
                        
                        print(f"VK API ошибка: {error_code} - {error.get('error_msg')}")
                        return None
                    
                    return data.get("response")
            except Exception as e:
                print(f"Ошибка запроса VK API: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(1)
                    continue
                return None
        
        return None

    async def _download_photo(self, photo_url: str, post_id: str) -> Optional[str]:
        """Скачивает фото и возвращает локальный путь"""
        try:
            # Генерируем уникальное имя файла
            file_hash = hashlib.md5(post_id.encode()).hexdigest()[:12]
            file_path = os.path.join(self.media_path, f"{file_hash}.jpg")
            
            # Проверяем, не скачан ли уже
            if os.path.exists(file_path):
                return os.path.abspath(file_path)
            
            async with self.session.get(photo_url, timeout=30) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    with open(file_path, 'wb') as f:
                        f.write(content)
                    print(f"  📷 VK фото скачано: {os.path.basename(file_path)}")
                    return os.path.abspath(file_path)
        except Exception as e:
            print(f"  ⚠️ Не удалось скачать VK фото: {e}")
        return None

    def _extract_group_id(self, group_url: str) -> str:
        """Извлекает ID/screen_name группы из URL или строки"""
        group_url = group_url.replace("https://vk.com/", "")
        group_url = group_url.replace("https://vk.ru/", "")
        group_url = group_url.replace("vk.com/", "")
        group_url = group_url.replace("vk.ru/", "")
        group_url = group_url.rstrip("/")
        return group_url

    async def get_group_info(self, group_id: str) -> Optional[dict]:
        """Получить информацию о группе"""
        screen_name = self._extract_group_id(group_id)
        
        result = await self._api_call("groups.getById", {
            "group_id": screen_name,
            "fields": "description,members_count"
        })
        
        if result and len(result) > 0:
            return result[0]
        return None

    async def get_wall_posts(self, group_id: str, count: int = 20) -> List[VKPost]:
        """Получить посты со стены группы с повторными попытками при ошибке 6"""
        screen_name = self._extract_group_id(group_id)

        group_info = await self.get_group_info(screen_name)
        if not group_info:
            print(f"Группа {group_id} не найдена")
            return []

        owner_id = -group_info["id"]
        group_name = group_info.get("name", screen_name)

        max_retries = 3
        for attempt in range(max_retries):
            result = await self._api_call("wall.get", {
                "owner_id": owner_id,
                "count": count,
                "filter": "owner"
            })

            if result is not None:
                break

            if attempt < max_retries - 1:
                print(f"Повторная попытка для {group_id} через 2 секунды...")
                await asyncio.sleep(2.0)
            else:
                print(f"Не удалось получить посты для {group_id} после {max_retries} попыток")
                return []

        if not result or "items" not in result:
            return []

        posts = []
        for item in result["items"]:
            post = await self._item_to_post(item, group_name, screen_name)
            if post:
                posts.append(post)

        return posts

    async def _item_to_post(self, item: dict, group_name: str, group_screen_name: str) -> Optional[VKPost]:
        """Конвертируем item VK в VKPost со скачиванием ВСЕХ фото"""
        try:
            post_id = f"vk_{item['owner_id']}_{item['id']}"
            text = item.get("text", "")
            title = text.split("\n")[0][:100] if text else "Без заголовка"
            url = f"https://vk.com/wall{item['owner_id']}_{item['id']}"
            pub_date = datetime.fromtimestamp(item["date"]) if "date" in item else None
            
            # Картинки — берём ВСЕ фото (до 10, лимит Telegram media_group)
            media_urls = []
            attachments = item.get("attachments", [])
            photo_idx = 0
            for att in attachments:
                if att["type"] == "photo":
                    sizes = att["photo"].get("sizes", [])
                    if sizes:
                        sizes.sort(key=lambda x: x.get("width", 0), reverse=True)
                        remote_url = sizes[0].get("url")
                        if remote_url:
                            # Уникальный ID для каждого фото в посте
                            local_path = await self._download_photo(
                                remote_url, f"{post_id}_{photo_idx}"
                            )
                            if local_path:
                                media_urls.append(local_path)
                                photo_idx += 1
                                if len(media_urls) >= 10:
                                    break
            
            # Пропускаем репосты без текста
            if not text and "copy_history" in item:
                return None

            # Скачиваем видео если есть
            for att in attachments:
                if att["type"] == "video":
                    v = att["video"]
                    video_path = await self._download_vk_video(
                        owner_id=v.get("owner_id"),
                        video_id=v.get("id"),
                        access_key=v.get("access_key", ""),
                        post_id=post_id
                    )
                    if video_path:
                        media_urls.append({'type': 'video', 'path': video_path})
                    break
            image_url = None
            for m in media_urls:
                if isinstance(m, dict):
                    image_url = m.get('path')
                else:
                    image_url = m
                break
            return VKPost(
                post_id=post_id,
                title=title,
                text=text[:10000],
                url=url,
                author=group_name,
                author_url=f"https://vk.com/{group_screen_name}",
                published_at=pub_date,
                image_url=image_url,
                media_urls=media_urls
            )
        except Exception as e:
            print(f"Ошибка конвертации VK поста: {e}")
            return None

    async def parse_multiple_groups(self, groups: List[str], posts_per_group: int = 10) -> List[VKPost]:
        """Парсим несколько групп"""
        all_posts = []
        
        for group in groups:
            try:
                posts = await self.get_wall_posts(group, posts_per_group)
                all_posts.extend(posts)
                print(f"✓ {group}: {len(posts)} постов")
                await asyncio.sleep(1.0)  # Задержка между группами
            except Exception as e:
                print(f"✗ {group}: ошибка - {e}")
        
        return all_posts


# ==================== ТЕСТ ====================

async def test_parser():
    """Тестируем VK парсер с локальным сохранением фото"""
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    token = os.getenv("VK_TOKEN")
    if not token:
        print("❌ VK_TOKEN не задан в .env")
        print("   Получить: https://vk.com/apps?act=manage")
        return
    
    print("🔍 Тестируем VK парсер с локальным сохранением фото...\n")
    
    test_groups = [
        "novgorod_life",
        "region53",
    ]
    
    async with VKParser(token, media_path="media/vk") as parser:
        posts = await parser.parse_multiple_groups(test_groups, posts_per_group=5)
        
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
