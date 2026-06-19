import sqlite3
import json
from datetime import datetime
from typing import List, Optional, Dict
from pathlib import Path


class Database:
    def __init__(self, db_path: str = "data/news.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.init_db()

    def get_conn(self):
        return sqlite3.connect(self.db_path, timeout=30)

    async def run_async(self, func, *args):
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args)
    def update_post_image(self, post_id: int, image_url: str):
        """Обновляет путь к изображению поста."""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE posts SET image_url = ? WHERE id = ?", (image_url, post_id))
        conn.commit()
        conn.close()


    def init_db(self):
        """Создаём таблицы и выполняем миграции"""
        conn = self.get_conn()
        cursor = conn.cursor()

        # Таблица папок (городов)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица источников новостей (VK, Telegram, RSS)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                value TEXT NOT NULL,
                name TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
            )
        ''')

        # Таблица каналов для публикации (привязана к папке)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS publish_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                channel_name TEXT,
                channel_username TEXT,
                signature TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
            )
        ''')

        # Таблица постов – folder_id добавим позже через миграцию
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT UNIQUE,
                source TEXT NOT NULL,
                source_name TEXT,
                title TEXT,
                text TEXT,
                url TEXT,
                author TEXT,
                image_url TEXT,
                published_at TIMESTAMP,
                parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_posted INTEGER DEFAULT 0
            )
        ''')

        # Таблица отложенных постов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                channel_ids TEXT,
                scheduled_at TIMESTAMP,
                text TEXT,
                image_url TEXT,
                video_url TEXT,
                signature TEXT,
                is_ad INTEGER DEFAULT 0,
                folder_id INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (post_id) REFERENCES posts(id),
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL
            )
        ''')

        # История постинга
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS post_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                channel_id TEXT,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_id INTEGER,
                FOREIGN KEY (post_id) REFERENCES posts(id)
            )
        ''')

        # Таблица срочных ключевых слов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS urgent_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица стоп-слов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stop_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Индексы
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_posted ON posts(is_posted)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_status ON scheduled_posts(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sources_folder ON sources(folder_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_publish_channels_folder ON publish_channels(folder_id)')

        conn.commit()

        # Миграции: добавляем недостающие колонки
        self._migrate_posts(cursor)
        self._migrate_scheduled_posts(cursor)
        self._migrate_initial_data(cursor)

        # Индекс на posts.folder_id после миграции
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_folder ON posts(folder_id)')

        conn.commit()
        conn.close()

    def _migrate_posts(self, cursor):
        cursor.execute("PRAGMA table_info(posts)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'folder_id' not in columns:
            cursor.execute("ALTER TABLE posts ADD COLUMN folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL")
        if 'media_urls' not in columns:
            cursor.execute("ALTER TABLE posts ADD COLUMN media_urls TEXT")
            # Мигрируем существующий image_url → media_urls = ["..."]
            cursor.execute("""
                UPDATE posts 
                SET media_urls = '["' || replace(image_url, '"', '\\"') || '"]'
                WHERE image_url IS NOT NULL AND image_url != '' AND media_urls IS NULL
            """)
        if 'telegraph_url' not in columns:
            cursor.execute("ALTER TABLE posts ADD COLUMN telegraph_url TEXT")

    def _migrate_scheduled_posts(self, cursor):
        cursor.execute("PRAGMA table_info(scheduled_posts)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'video_url' not in columns:
            cursor.execute("ALTER TABLE scheduled_posts ADD COLUMN video_url TEXT")
        if 'is_ad' not in columns:
            cursor.execute("ALTER TABLE scheduled_posts ADD COLUMN is_ad INTEGER DEFAULT 0")
        if 'folder_id' not in columns:
            cursor.execute(
                "ALTER TABLE scheduled_posts ADD COLUMN folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL")
        if 'media_list' not in columns:
            cursor.execute("ALTER TABLE scheduled_posts ADD COLUMN media_list TEXT")

    def _migrate_initial_data(self, cursor):
        """Перенос существующих источников и каналов из config.py (если есть)"""
        try:
            from config import VK_GROUPS, TG_CHANNELS, RSS_FEEDS
            # Создаём папку по умолчанию, если её нет
            cursor.execute("SELECT id FROM folders WHERE name = 'Великий Новгород'")
            row = cursor.fetchone()
            if not row:
                cursor.execute("INSERT INTO folders (name) VALUES ('Великий Новгород')")
                folder_id = cursor.lastrowid
            else:
                folder_id = row[0]

            # Переносим источники VK
            for group in VK_GROUPS:
                cursor.execute("SELECT id FROM sources WHERE type='vk' AND value=? AND folder_id=?", (group, folder_id))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO sources (folder_id, type, value, name) VALUES (?, 'vk', ?, ?)",
                                   (folder_id, group, group))

            # Переносим источники Telegram
            for ch in TG_CHANNELS:
                cursor.execute("SELECT id FROM sources WHERE type='telegram' AND value=? AND folder_id=?",
                               (ch, folder_id))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO sources (folder_id, type, value, name) VALUES (?, 'telegram', ?, ?)",
                                   (folder_id, ch, ch))

            # Переносим RSS-источники
            for feed in RSS_FEEDS:
                url = feed.get('url')
                name = feed.get('name', url)
                cursor.execute("SELECT id FROM sources WHERE type='rss' AND value=? AND folder_id=?", (url, folder_id))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO sources (folder_id, type, value, name) VALUES (?, 'rss', ?, ?)",
                                   (folder_id, url, name))

            # Переносим старые каналы публикации (таблица channels) в publish_channels
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='channels'")
            if cursor.fetchone():
                cursor.execute(
                    "SELECT channel_id, channel_name, channel_username, signature FROM channels WHERE is_active=1")
                old_channels = cursor.fetchall()
                for ch_id, ch_name, ch_username, signature in old_channels:
                    cursor.execute("SELECT id FROM publish_channels WHERE channel_id=? AND folder_id=?",
                                   (ch_id, folder_id))
                    if not cursor.fetchone():
                        cursor.execute('''
                            INSERT INTO publish_channels (folder_id, channel_id, channel_name, channel_username, signature)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (folder_id, ch_id, ch_name, ch_username, signature))

            # Обновляем существующие посты: проставляем folder_id по источнику
            cursor.execute('''
                UPDATE posts SET folder_id = (
                    SELECT folder_id FROM sources WHERE sources.value = posts.source_name AND sources.type = posts.source
                    LIMIT 1
                )
                WHERE folder_id IS NULL
            ''')
        except ImportError:
            # Если config.py не содержит этих переменных, просто игнорируем
            pass

    # ==================== ПОСТЫ ====================
    def add_post(self, post_id: str, source: str, title: str, text: str,
                 url: str, author: str, image_url: str = None,
                 source_name: str = None, published_at: datetime = None,
                 folder_id: int = None, media_urls: List[str] = None) -> Optional[int]:
        conn = self.get_conn()
        cursor = conn.cursor()
        try:
            # Обратная совместимость: если передан image_url, а media_urls нет — создаём
            if not media_urls and image_url:
                media_urls = [image_url]
            # Если есть media_urls — первое пишем и в image_url (для старых мест кода)
            if media_urls and not image_url:
                image_url = media_urls[0]
            media_urls_json = json.dumps(media_urls) if media_urls else None

            cursor.execute('''
                INSERT OR IGNORE INTO posts 
                (post_id, source, source_name, title, text, url, author, image_url, media_urls, published_at, folder_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (post_id, source, source_name, title, text, url, author, image_url,
                  media_urls_json, published_at, folder_id))
            conn.commit()
            return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception as e:
            print(f"Ошибка добавления поста: {e}")
            return None
        finally:
            conn.close()

    def get_posts(self, folder_id: int = None, limit: int = 50, only_new: bool = False, source_filter: str = None) -> List[Dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        query = "SELECT * FROM posts WHERE 1=1"
        params = []
        if folder_id is not None:
            query += " AND folder_id = ?"
            params.append(folder_id)
        if only_new:
            query += " AND is_posted = 0"
        if source_filter and source_filter != 'all':
            query += " AND source = ?"
            params.append(source_filter)
        query += " ORDER BY parsed_at DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        posts = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return posts

    def get_post_by_id(self, id: int) -> Optional[Dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM posts WHERE id = ?", (id,))
        row = cursor.fetchone()
        if row:
            columns = [desc[0] for desc in cursor.description]
            post = dict(zip(columns, row))
        else:
            post = None
        conn.close()
        return post

    def mark_as_posted(self, post_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE posts SET is_posted = 1 WHERE id = ?", (post_id,))
        conn.commit()
        conn.close()

    def update_telegraph_url(self, post_id: int, telegraph_url: str):
        """Сохраняет URL Telegraph статьи чтобы переиспользовать её при повторной публикации"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE posts SET telegraph_url = ? WHERE id = ?", (telegraph_url, post_id))
        conn.commit()
        conn.close()

    def update_post_media(self, post_id: int, media_urls: List[str]):
        """Обновляет список медиа поста (например, при замене фото)"""
        conn = self.get_conn()
        cursor = conn.cursor()
        image_url = media_urls[0] if media_urls else None
        cursor.execute(
            "UPDATE posts SET media_urls = ?, image_url = ? WHERE id = ?",
            (json.dumps(media_urls) if media_urls else None, image_url, post_id)
        )
        conn.commit()
        conn.close()

    def post_exists(self, post_id: str) -> bool:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM posts WHERE post_id = ?", (post_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    def cleanup_old_posts(self, days: int = 3) -> dict:
        """Удаляет ВСЕ посты старше N дней (и опубликованные, и нет).
        Возвращает dict со статистикой и списком image_url удалённых постов."""
        conn = self.get_conn()
        cursor = conn.cursor()

        # Собираем image_url удаляемых постов (для очистки медиафайлов)
        cursor.execute("""
            SELECT image_url FROM posts
            WHERE parsed_at < datetime('now', ?)
            AND image_url IS NOT NULL AND image_url != ''
        """, (f'-{days} days',))
        deleted_images = [row[0] for row in cursor.fetchall()]

        # Удаляем посты
        cursor.execute("""
            DELETE FROM posts
            WHERE parsed_at < datetime('now', ?)
        """, (f'-{days} days',))
        deleted_posts = cursor.rowcount

        # Удаляем завершённые scheduled_posts старше N дней
        cursor.execute("""
            DELETE FROM scheduled_posts
            WHERE status != 'pending'
            AND created_at < datetime('now', ?)
        """, (f'-{days} days',))
        deleted_scheduled = cursor.rowcount

        # Удаляем старую историю публикаций
        cursor.execute("""
            DELETE FROM post_history
            WHERE posted_at < datetime('now', ?)
        """, (f'-{days} days',))
        deleted_history = cursor.rowcount

        conn.commit()
        conn.close()
        return {
            'posts': deleted_posts,
            'scheduled': deleted_scheduled,
            'history': deleted_history,
            'images': deleted_images
        }

    def get_protected_media_paths(self) -> set:
        """Возвращает пути медиафайлов, которые нужны pending scheduled_posts."""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT image_url FROM scheduled_posts
            WHERE status = 'pending'
            AND image_url IS NOT NULL AND image_url != ''
        """)
        paths = {row[0] for row in cursor.fetchall()}
        conn.close()
        return paths

    # ------------------------------------------------------------------
    # Стоп-слова
    # ------------------------------------------------------------------

    def get_stop_words(self) -> List[str]:
        """Возвращает список всех стоп-слов."""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT word FROM stop_words ORDER BY word")
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]

    def add_stop_word(self, word: str) -> bool:
        """Добавляет стоп-слово. Возвращает False если уже существует."""
        conn = self.get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO stop_words (word) VALUES (?)", (word.lower().strip(),))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def remove_stop_word(self, word: str) -> bool:
        """Удаляет стоп-слово. Возвращает False если не найдено."""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stop_words WHERE word = ?", (word.lower().strip(),))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def post_has_stop_words(self, text: str) -> Optional[str]:
        """
        Проверяет текст на стоп-слова.
        Возвращает первое найденное стоп-слово или None.
        """
        if not text:
            return None
        words = self.get_stop_words()
        text_lower = text.lower()
        for word in words:
            if word in text_lower:
                return word
        return None

    # ------------------------------------------------------------------
    # Срочные ключевые слова
    # ------------------------------------------------------------------

    def get_urgent_words(self) -> List[str]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT word FROM urgent_words ORDER BY word")
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]

    def add_urgent_word(self, word: str) -> bool:
        conn = self.get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO urgent_words (word) VALUES (?)", (word.lower().strip(),))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def remove_urgent_word(self, word: str) -> bool:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM urgent_words WHERE word = ?", (word.lower().strip(),))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def post_has_urgent_words(self, text: str) -> Optional[str]:
        """Возвращает первое найденное срочное слово или None."""
        if not text:
            return None
        words = self.get_urgent_words()
        text_lower = text.lower()
        for word in words:
            if word in text_lower:
                return word
        return None

    def vacuum(self):
        """Сжимает БД — освобождает место на диске после удалений."""
        conn = self.get_conn()
        conn.execute("VACUUM")
        conn.close()

    def get_posts_count(self) -> Dict[str, int]:
        """Возвращает статистику постов"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM posts WHERE is_posted = 0")
        new_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM posts WHERE is_posted = 1")
        posted_count = cursor.fetchone()[0]
        conn.close()
        return {'new': new_count, 'posted': posted_count}

    def get_posts_by_source_count(self, folder_id: int) -> Dict[str, int]:
        """Возвращает количество новых постов по источникам для папки"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(source, 'unknown') as src, COUNT(*) as cnt 
            FROM posts 
            WHERE folder_id = ? AND is_posted = 0
            GROUP BY source
        """, (folder_id,))
        result = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return result

    def get_sources_stats(self) -> Dict[str, int]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT source, COUNT(*) as count FROM posts GROUP BY source')
        stats = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return stats

    # ==================== ПАПКИ (ГОРОДА) ====================
    def add_folder(self, name: str) -> int:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO folders (name) VALUES (?)", (name,))
        folder_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return folder_id

    def get_folders(self) -> List[Dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM folders ORDER BY name")
        columns = [desc[0] for desc in cursor.description]
        folders = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return folders

    def get_folder_by_id(self, folder_id: int) -> Optional[Dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM folders WHERE id = ?", (folder_id,))
        row = cursor.fetchone()
        if row:
            columns = [desc[0] for desc in cursor.description]
            folder = dict(zip(columns, row))
        else:
            folder = None
        conn.close()
        return folder

    def update_folder_name(self, folder_id: int, new_name: str):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE folders SET name = ? WHERE id = ?", (new_name, folder_id))
        conn.commit()
        conn.close()

    def delete_folder(self, folder_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        conn.commit()
        conn.close()

    # ==================== ИСТОЧНИКИ ====================
    def add_source(self, folder_id: int, source_type: str, value: str, name: str = None) -> int:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sources (folder_id, type, value, name)
            VALUES (?, ?, ?, ?)
        ''', (folder_id, source_type, value, name or value))
        source_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return source_id

    def get_sources_by_folder(self, folder_id: int, active_only: bool = True) -> List[Dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        query = "SELECT * FROM sources WHERE folder_id = ?"
        params = [folder_id]
        if active_only:
            query += " AND is_active = 1"
        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        sources = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return sources

    def get_all_active_sources(self) -> List[Dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sources WHERE is_active = 1")
        columns = [desc[0] for desc in cursor.description]
        sources = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return sources

    def delete_source(self, source_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        conn.commit()
        conn.close()

    # ==================== КАНАЛЫ ПУБЛИКАЦИИ ====================
    def add_publish_channel(self, folder_id: int, channel_id: str, channel_name: str = None,
                            channel_username: str = None, signature: str = None) -> int:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO publish_channels (folder_id, channel_id, channel_name, channel_username, signature)
            VALUES (?, ?, ?, ?, ?)
        ''', (folder_id, channel_id, channel_name, channel_username, signature))
        channel_db_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return channel_db_id

    def get_publish_channels_by_folder(self, folder_id: int, active_only: bool = True) -> List[Dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        query = "SELECT * FROM publish_channels WHERE folder_id = ?"
        params = [folder_id]
        if active_only:
            query += " AND is_active = 1"
        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        channels = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return channels

    def delete_publish_channel(self, channel_db_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM publish_channels WHERE id = ?", (channel_db_id,))
        conn.commit()
        conn.close()

    def update_publish_channel_signature(self, channel_db_id: int, signature: str):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE publish_channels SET signature = ? WHERE id = ?", (signature, channel_db_id))
        conn.commit()
        conn.close()

    def get_publish_channel_signature(self, channel_id: str) -> Optional[str]:
        """Возвращает подпись канала публикации по его channel_id (Telegram chat id)"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT signature FROM publish_channels WHERE channel_id = ?", (channel_id,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def get_publish_channel_by_channel_id(self, channel_id: str) -> Optional[Dict]:
        """Возвращает полную информацию о канале публикации по его channel_id"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM publish_channels WHERE channel_id = ?", (channel_id,))
        row = cursor.fetchone()
        if row:
            columns = [desc[0] for desc in cursor.description]
            channel = dict(zip(columns, row))
        else:
            channel = None
        conn.close()
        return channel

    # ==================== ОТЛОЖЕННЫЕ ПОСТЫ ====================
    def add_scheduled_post(self, post_id: int, channel_ids: List[str],
                           scheduled_at: datetime, text: str = None,
                           image_url: str = None, video_url: str = None,
                           signature: str = None, is_ad: int = 0,
                           folder_id: int = None, media_list: str = None) -> int:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO scheduled_posts 
            (post_id, channel_ids, scheduled_at, text, image_url, video_url, signature, is_ad, folder_id, media_list)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (post_id, json.dumps(channel_ids), scheduled_at, text, image_url, video_url, signature, is_ad, folder_id,
              media_list))
        scheduled_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return scheduled_id

    def get_pending_scheduled(self) -> List[Dict]:
        """Получить посты, время которых уже наступило (для отправки)"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM scheduled_posts 
            WHERE status = 'pending' AND scheduled_at <= ?
        ''', (datetime.now(),))
        columns = [desc[0] for desc in cursor.description]
        posts = []
        for row in cursor.fetchall():
            post = dict(zip(columns, row))
            post['channel_ids'] = json.loads(post['channel_ids'])
            posts.append(post)
        conn.close()
        return posts

    def get_all_scheduled(self) -> List[Dict]:
        """Получить ВСЕ запланированные посты (для показа пользователю)"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM scheduled_posts 
            WHERE status = 'pending'
            ORDER BY scheduled_at ASC
        ''')
        columns = [desc[0] for desc in cursor.description]
        posts = []
        for row in cursor.fetchall():
            post = dict(zip(columns, row))
            post['channel_ids'] = json.loads(post['channel_ids'])
            posts.append(post)
        conn.close()
        return posts

    def get_scheduled_by_folder(self, folder_id: int) -> List[Dict]:
        """Получить запланированные посты для конкретного города"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM scheduled_posts 
            WHERE status = 'pending' AND folder_id = ?
            ORDER BY scheduled_at ASC
        ''', (folder_id,))
        columns = [desc[0] for desc in cursor.description]
        posts = []
        for row in cursor.fetchall():
            post = dict(zip(columns, row))
            post['channel_ids'] = json.loads(post['channel_ids'])
            posts.append(post)
        conn.close()
        return posts

    def get_scheduled_count_by_folder(self) -> Dict[int, int]:
        """Получить количество запланированных постов по городам"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT folder_id, COUNT(*) as cnt 
            FROM scheduled_posts 
            WHERE status = 'pending'
            GROUP BY folder_id
        ''')
        result = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return result

    def mark_scheduled_done(self, scheduled_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE scheduled_posts SET status = 'done' WHERE id = ?", (scheduled_id,))
        conn.commit()
        conn.close()

    def cancel_scheduled(self, scheduled_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scheduled_posts WHERE id = ?", (scheduled_id,))
        conn.commit()
        conn.close()


    def get_posts_for_replacement(self, folder_id: int, exclude_post_id: int, limit: int = 100) -> List[Dict]:
        """Посты для замены — неопубликованные, не в расписании, кроме текущего."""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.* FROM posts p
            WHERE p.folder_id = ?
              AND p.is_posted = 0
              AND p.id != ?
              AND p.id NOT IN (
                  SELECT post_id FROM scheduled_posts
                  WHERE status = 'pending' AND post_id IS NOT NULL AND folder_id = ?
              )
            ORDER BY p.parsed_at DESC
            LIMIT ?
        """, (folder_id, exclude_post_id, folder_id, limit))
        columns = [desc[0] for desc in cursor.description]
        posts = []
        for row in cursor.fetchall():
            post = dict(zip(columns, row))
            posts.append(post)
        conn.close()
        return posts
    def get_scheduled_by_id(self, scheduled_id: int) -> Optional[Dict]:
        """Получить отложенный пост по ID"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scheduled_posts WHERE id = ?", (scheduled_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            columns = [desc[0] for desc in cursor.description]
            post = dict(zip(columns, row))
            post['channel_ids'] = json.loads(post['channel_ids'])
            return post
        return None

    def update_scheduled_time(self, scheduled_id: int, new_time: datetime):
        """Обновить время отложенного поста"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE scheduled_posts SET scheduled_at = ? WHERE id = ?",
            (new_time, scheduled_id)
        )
        conn.commit()
        conn.close()

    # ==================== ИСТОРИЯ ====================
    def add_to_history(self, post_id: int, channel_id: str, message_id: int = None):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO post_history (post_id, channel_id, message_id)
            VALUES (?, ?, ?)
        ''', (post_id, channel_id, message_id))
        conn.commit()
        conn.close()
    # ==================== АВТОПИЛОТ ====================

    def get_autopilot_settings(self, folder_id: int) -> Optional[Dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM autopilot_settings WHERE folder_id = ?", (folder_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            columns = [desc[0] for desc in cursor.description]
            s = dict(zip(columns, row))
            s['slots'] = json.loads(s['slots'])
            return s
        return None

    def get_all_autopilot_settings(self) -> List[Dict]:
        """Все города с включённым автопилотом"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.*, f.name as folder_name
            FROM autopilot_settings a
            JOIN folders f ON f.id = a.folder_id
            WHERE a.is_enabled = 1
        """)
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        for r in rows:
            r['slots'] = json.loads(r['slots'])
        return rows

    def save_autopilot_settings(self, folder_id: int, is_enabled: int = None,
                                 posts_per_day: int = None, slots: list = None,
                                 plan_time: str = None, report_time: str = None):
        conn = self.get_conn()
        cursor = conn.cursor()
        # Upsert
        cursor.execute("SELECT id FROM autopilot_settings WHERE folder_id = ?", (folder_id,))
        exists = cursor.fetchone()
        if not exists:
            cursor.execute("""
                INSERT INTO autopilot_settings (folder_id, is_enabled, posts_per_day, slots, plan_time, report_time)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                folder_id,
                is_enabled if is_enabled is not None else 0,
                posts_per_day if posts_per_day is not None else 6,
                json.dumps(slots) if slots else '["09:00","12:00","15:00","18:00","20:00","22:00"]',
                plan_time or '05:00',
                report_time or '06:00',
            ))
        else:
            fields = []
            params = []
            if is_enabled is not None:
                fields.append("is_enabled = ?"); params.append(is_enabled)
            if posts_per_day is not None:
                fields.append("posts_per_day = ?"); params.append(posts_per_day)
            if slots is not None:
                fields.append("slots = ?"); params.append(json.dumps(slots))
            if plan_time is not None:
                fields.append("plan_time = ?"); params.append(plan_time)
            if report_time is not None:
                fields.append("report_time = ?"); params.append(report_time)
            if fields:
                fields.append("updated_at = CURRENT_TIMESTAMP")
                params.append(folder_id)
                cursor.execute(
                    f"UPDATE autopilot_settings SET {', '.join(fields)} WHERE folder_id = ?",
                    params
                )
        conn.commit()
        conn.close()

    def get_posts_for_autopilot(self, folder_id: int, limit: int = 50) -> List[Dict]:
        """
        Посты для автопилота:
        - не опубликованы (is_posted = 0)
        - не стоят в расписании
        - свежие (по parsed_at DESC)
        """
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.* FROM posts p
            WHERE p.folder_id = ?
              AND p.is_posted = 0
              AND p.id NOT IN (
                  SELECT post_id FROM scheduled_posts
                  WHERE status = 'pending' AND post_id IS NOT NULL
              )
            ORDER BY p.parsed_at DESC
            LIMIT ?
        """, (folder_id, limit))
        columns = [desc[0] for desc in cursor.description]
        posts = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return posts

    def get_scheduled_slots_today(self, folder_id: int) -> List[str]:
        """Уже занятые слоты сегодня для города (формат HH:MM)"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT scheduled_at FROM scheduled_posts
            WHERE folder_id = ? AND status = 'pending'
              AND date(scheduled_at) = date('now')
        """, (folder_id,))
        rows = cursor.fetchall()
        conn.close()
        return [row[0][11:16] for row in rows if row[0]]
