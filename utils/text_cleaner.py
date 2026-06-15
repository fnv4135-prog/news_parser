"""
utils/text_cleaner.py — очистка текста постов от чужих подписей.
"""

import re
from typing import List


# ---------------------------------------------------------------------------
# Паттерны
# ---------------------------------------------------------------------------

_RE_URL = re.compile(
    r'https?://\S+|t\.me/\S+|vk\.com/\S+|vk\.ru/\S+|vk\.cc/\S+|max\.ru/\S+|dzen\.ru/\S+',
    re.IGNORECASE
)

# Markdown-ссылки [текст](url) и [текст|url]
_RE_MD_LINK_PAREN  = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_RE_MD_LINK_PIPE   = re.compile(r'\[([^\]]+)\|[^\]]+\]')

_SIGNATURE_PHRASES = [
    r'источник\s*:',
    r'мы в макс', r'мы в max',
    r'подписаться', r'подписывайтесь',
    r'поделиться новостью',
    r'читать далее', r'читать полностью',
    r'наш канал', r'наш сайт', r'главный канал', r'без цензуры',
    r'присоединяйтесь', r'переходите',
]
_RE_SIGNATURE = re.compile('|'.join(_SIGNATURE_PHRASES), re.IGNORECASE)

_AD_PHRASES = [
    r'акция', r'скидк[аи]', r'промокод',
    r'бесплатный замер', r'расчёт стоимости', r'расчет стоимости',
    r'запись на', r'звоните', r'купить', r'продам', r'сдам',
]
_RE_AD = re.compile('|'.join(_AD_PHRASES), re.IGNORECASE)

_RE_PHONE = re.compile(r'[\+7|8][\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}')
_RE_HASHTAG_LINE = re.compile(r'^(\s*#\w+\s*)+$')

# Строка заканчивается стрелкой/каналом — «Вам на А🔽», «Жми 👇» и т.п.
_RE_ARROW_TAIL = re.compile(r'[🔽🔼👇👆➡⬇⬆▼▲]+\s*$')

# Защитные паттерны
_RE_PHOTO_CREDIT = re.compile(r'^📸')
_RE_LOCATION     = re.compile(r'^📍')


def _is_protected(line: str) -> bool:
    s = line.strip()
    return bool(_RE_PHOTO_CREDIT.match(s) or _RE_LOCATION.match(s))


def _line_has_url(line: str) -> bool:
    return bool(_RE_URL.search(line) or _RE_MD_LINK_PAREN.search(line) or _RE_MD_LINK_PIPE.search(line))


def _line_word_count(line: str) -> int:
    """Количество слов в строке после удаления ссылок и спецсимволов."""
    clean = _RE_MD_LINK_PAREN.sub('', line)
    clean = _RE_MD_LINK_PIPE.sub('', clean)
    clean = _RE_URL.sub('', clean)
    return len(clean.split())


def _should_remove_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _is_protected(s):
        return False

    # Маркеры подписей и рекламы — удаляем всю строку
    if _RE_SIGNATURE.search(s):
        return True
    if _RE_AD.search(s):
        return True
    if _RE_PHONE.search(s):
        return True
    if _RE_HASHTAG_LINE.match(s):
        return True

    # Строка заканчивается стрелкой/направлением без полезного контента
    if _RE_ARROW_TAIL.search(s) and _line_word_count(s) <= 4:
        return True

    # Строка содержит ссылку — проверяем есть ли смысловой контент БЕЗ ссылки
    if _line_has_url(s):
        words_without_url = _line_word_count(s)
        if words_without_url <= 3:
            # Мало слов без ссылки — удаляем всю строку
            return True
        # Есть смысловой контент — оставляем строку, ссылку уберём inline

    return False


def _strip_links_inline(line: str) -> str:
    """Убирает ссылки из строки оставляя текст."""
    # [текст](url) → текст
    line = _RE_MD_LINK_PAREN.sub(r'\1', line)
    # [текст|url] → текст
    line = _RE_MD_LINK_PIPE.sub(r'\1', line)
    # Прямые URL
    line = _RE_URL.sub('', line)
    # Хэштеги
    line = re.sub(r'#\w+', '', line)
    return line.strip()


def clean_text(text: str) -> str:
    """Очищает текст поста от подписей, ссылок, рекламы."""
    if not text:
        return text

    lines = text.split('\n')
    cleaned: List[str] = []

    for line in lines:
        if _should_remove_line(line):
            continue
        # Убираем ссылки inline для оставшихся строк
        line = _strip_links_inline(line)
        cleaned.append(line)

    # Схлопываем множественные пустые строки
    result_lines: List[str] = []
    empty_count = 0
    for line in cleaned:
        if line.strip() == '':
            empty_count += 1
            if empty_count <= 1:
                result_lines.append('')
        else:
            empty_count = 0
            result_lines.append(line)

    return '\n'.join(result_lines).strip()


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    tests = [
        (
            "музыкальный сухой фонтан\n\nИсточник: max.ru/gorod_53",
            "музыкальный сухой фонтан"
        ),
        (
            "рассказал [в своём канале в мессенджере «Макс»](https://max.ru/dronovnov/AZ6Ez5N1AS8) губернатор Новгородской области Александр Дронов.",
            "рассказал в своём канале в мессенджере «Макс» губернатор Новгородской области Александр Дронов."
        ),
        (
            "Мы начнем — «Анапа».\n\nВам на А🔽\n\n📲 [Мы в МАКС ](https://max.ru/join/KgDp)\n😉 [Подписаться](https://t.me/+ApNE3) | [Поделиться новостью](https://t.me/andry_smi)",
            "Мы начнем — «Анапа»."
        ),
        (
            "царство Бабы Яги. \n \n📍 Новгородская область, Солецкий муниципальный округ, д. Каменка \n \n📸Русь Новгородская \n \nМы в Max: https://max.ru/pro_velikiy",
            "царство Бабы Яги.\n\n📍 Новгородская область, Солецкий муниципальный округ, д. Каменка\n\n📸Русь Новгородская"
        ),
        (
            "Никаких:\n✖ падающих штор\n\nСейчас действует акция на установку карниза\n\nЗАПИСЬ НА БЕСПЛАТНЫЙ ЗАМЕР\n8(952)480-53-60",
            "Никаких:\n✖ падающих штор"
        ),
    ]

    passed = 0
    print("=== Тесты text_cleaner ===\n")
    for i, (inp, exp) in enumerate(tests, 1):
        res = clean_text(inp)
        if res == exp:
            passed += 1
            print(f"✅ Тест {i}")
        else:
            print(f"❌ Тест {i}")
            print(f"   Ожидал:  {repr(exp)}")
            print(f"   Получил: {repr(res)}")
    print(f"\n{passed}/{len(tests)} тестов прошло")
