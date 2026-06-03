import os
from dotenv import load_dotenv

load_dotenv()

# ==================== TELEGRAM ====================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# ==================== VK ====================

VK_TOKEN = os.getenv("VK_TOKEN")  # Сервисный ключ или токен пользователя

VK_GROUPS = [
    "club215921691",
    "otzyvy_chp_velikiynovgorod",
    "pro_velikiy",
    "novru",
    "blacklist_53",
    "novgorod_life",
    "region53",
    "club225447769",
    "club82399590",
    "harmony_of_the_forest",
    "silva23111978",
    "vnpfoto",
    "ilovenov",
    "53gor53",
    "gorod_53",
    "club83371885",
]

# ==================== TELEGRAM КАНАЛЫ ====================

TG_CHANNELS = [
    "novgorodtop",
    "novgorod_smi",
    "news53_53",
    "freeportalvn",
    "NovgorodchinaNEWS",
    "womanvn",
    "pvn53",
    "regionvn53",
    "chudomamacom",
    "novgorodproc",
]

# ==================== RSS ИСТОЧНИКИ ====================
RSS_FEEDS = [
    {"url": "http://novved.ru/rss.xml", "name": "Новгородские ведомости"},  # работает
    {"url": "https://vnnews.ru/rss/", "name": "ВНовгороде.ру"},               # работает
]

# ==================== HTML-ИСТОЧНИКИ (новостные сайты) ====================
NEWS_SITES = [
    {
        "url": "https://novvedomosti.ru/news/",
        "name": "Новгородские ведомости",
        "selectors": {
            "container": "div.news-item",
            "title": "h2 a",
            "link": "h2 a",
            "date": "span.date",
            "description": "div.description",
            "image": "img"
        }
    },
]

# ==================== ПАРСИНГ ====================

PARSE_INTERVAL = 900  # 15 минут в секундах
MAX_POSTS_PER_SOURCE = 10  # Максимум постов за один парсинг

# ==================== БАЗА ДАННЫХ ====================

DATABASE_PATH = "data/news.db"

# ==================== МЕДИА ====================

MEDIA_PATH = "media/telegram"  # Папка для скачанных фото из Telegram
MEDIA_PATH_VK = "media/vk"     # Папка для скачанных фото из VK
