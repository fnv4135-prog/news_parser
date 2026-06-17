from typing import Dict, Set, List
from aiogram.fsm.state import State, StatesGroup


# ==================== FSM СОСТОЯНИЯ ====================

class SourceStates(StatesGroup):
    """Состояния для добавления источника"""
    waiting_type = State()      # Ожидание выбора типа (VK/TG/RSS)
    waiting_value = State()     # Ожидание ввода значения


class ChannelStates(StatesGroup):
    """Состояния для управления каналами публикации"""
    waiting_username = State()  # Ожидание username канала
    waiting_signature = State() # Ожидание подписи


class PostEditStates(StatesGroup):
    """Состояния для редактирования поста"""
    waiting_text = State()      # Ожидание нового текста
    waiting_time = State()      # Ожидание времени отложенной публикации
    waiting_image = State()     # Ожидание нового фото
    waiting_signature = State() # Ожидание подписи перед публикацией


class ScheduleEditStates(StatesGroup):
    """Состояния для редактирования отложенных постов"""
    waiting_new_time = State()  # Ожидание нового времени


class FolderStates(StatesGroup):
    """Состояния для управления городами"""
    waiting_name = State()      # Ожидание названия нового города
    waiting_edit_name = State() # Ожидание нового названия города


class AdStates(StatesGroup):
    """Состояния для создания рекламы"""
    waiting_text = State()      # Ожидание текста рекламы
    waiting_media = State()     # Ожидание медиа
    waiting_city = State()      # Ожидание выбора города
    waiting_channels = State()  # Ожидание выбора каналов
    waiting_time = State()      # Ожидание времени


# ==================== ВРЕМЕННЫЕ ДАННЫЕ (не FSM) ====================

# Для постов (пагинация, выбор, редактирование)
user_posts_cache: Dict[int, List[dict]] = {}
user_pages: Dict[int, int] = {}
user_current_post: Dict[int, dict] = {}
user_wm_result: Dict[int, str] = {}  # post_id -> путь к обработанному фото
user_selected_channels: Dict[int, Set[str]] = {}
user_edited_text: Dict[int, str] = {}
user_selected_folder_for_publish: Dict[int, int] = {}
user_custom_signature: Dict[int, str] = {}  # Кастомная подпись для текущего поста
user_source_filter: Dict[int, str] = {}  # Фильтр по источнику: vk, telegram, rss, all
user_preview_msg_ids: Dict[int, List[int]] = {}  # message_id предпросмотров (фото + текст)
user_list_msg_id: Dict[int, int] = {}  # message_id текущего списка постов / меню

# Для рекламных сообщений — УДАЛЕНО, ad.py переведён на FSM (AdStates)

# DEPRECATED - оставляем для совместимости, но не используем
user_schedule_data: Dict[int, dict] = {}
