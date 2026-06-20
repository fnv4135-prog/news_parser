from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router()


async def show_main_menu(message: Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text='📰 Посты', callback_data='menu_posts')
    kb.button(text='⚡️ Срочные', callback_data='menu_urgent')
    kb.button(text='📅 План на сегодня', callback_data='menu_today')
    kb.button(text='🤖 Автопилот', callback_data='menu_autopilot')
    kb.button(text='🕒 Отложенные', callback_data='menu_scheduled')
    kb.button(text='🔄 Парсинг', callback_data='menu_parse')
    kb.button(text='🏙 Города', callback_data='menu_cities')
    kb.button(text='📡 Источники', callback_data='menu_sources')
    kb.button(text='📢 Каналы', callback_data='menu_channels')
    kb.button(text='🛑 Стоп-слова', callback_data='menu_stopwords')
    kb.button(text='📣 Реклама', callback_data='menu_ad')
    kb.button(text='❓ Помощь', callback_data='menu_help')
    kb.adjust(2)
    await state.clear()
    await message.answer(
        '👋 Привет! Выберите действие:',
        reply_markup=kb.as_markup()
    )


@router.message(Command('start'))
async def cmd_start(message: Message, state: FSMContext):
    await show_main_menu(message, state)
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(lambda c: c.data and c.data.startswith('menu_'))
async def menu_callback(callback: CallbackQuery, state: FSMContext):
    from handlers import posts, urgent, autopilot, schedule, parse_now, folders, sources, publish_channels, stopwords, ad, help

    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass

    msg = callback.message

    handlers_map = {
        'menu_posts':      (posts,            'cmd_posts'),
        'menu_urgent':     (urgent,           'cmd_urgent'),
        'menu_today':      (autopilot,        'cmd_today'),
        'menu_autopilot':  (autopilot,        'cmd_autopilot'),
        'menu_scheduled':  (schedule,         'cmd_scheduled'),
        'menu_parse':      (parse_now,        'cmd_parse_now'),
        'menu_cities':     (folders,          'cmd_cities'),
        'menu_sources':    (sources,          'cmd_sources'),
        'menu_channels':   (publish_channels, 'cmd_channels'),
        'menu_stopwords':  (stopwords,        'cmd_stopwords'),
        'menu_ad':         (ad,               'cmd_ad'),
        'menu_help':       (help,             'cmd_help'),
    }

    entry = handlers_map.get(callback.data)
    if not entry:
        return

    module, func_name = entry
    handler = getattr(module, func_name, None)
    if handler is None:
        await msg.answer(f'⚠️ Хендлер {func_name} не найден')
        return

    try:
        await handler(msg, state)
    except TypeError:
        try:
            await handler(msg)
        except Exception as e:
            await msg.answer(f'⚠️ Ошибка: {e}')
    except Exception as e:
        await msg.answer(f'⚠️ Ошибка: {e}')
