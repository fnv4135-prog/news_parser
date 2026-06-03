"""
Скрипт авторизации Telegram с детальной диагностикой
"""
import asyncio
import os
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PhoneNumberBannedError, PhoneNumberInvalidError
from dotenv import load_dotenv

load_dotenv()

api_id = os.getenv('TG_API_ID')
api_hash = os.getenv('TG_API_HASH')
phone = os.getenv('TG_PHONE')

print("=" * 50)
print("ДИАГНОСТИКА TELEGRAM АВТОРИЗАЦИИ")
print("=" * 50)
print(f"API_ID: {api_id}")
print(f"API_HASH: {api_hash[:10]}...")
print(f"PHONE: {phone}")
print("=" * 50)

if not all([api_id, api_hash, phone]):
    print("❌ Не все переменные заданы в .env!")
    exit(1)

async def auth():
    # Удаляем старую сессию
    if os.path.exists('parser_session.session'):
        os.remove('parser_session.session')
        print("🗑 Старая сессия удалена")
    
    client = TelegramClient('parser_session', int(api_id), api_hash)
    
    try:
        await client.connect()
        print("✅ Подключение к Telegram установлено")
        
        if await client.is_user_authorized():
            print("✅ Уже авторизован!")
            me = await client.get_me()
            print(f"👤 Аккаунт: {me.first_name} {me.last_name or ''} (@{me.username or 'нет'})")
        else:
            print(f"📱 Отправляю запрос кода на {phone}...")
            
            try:
                sent = await client.send_code_request(phone)
                print(f"✅ Код отправлен! Тип: {sent.type}")
                print(f"   phone_code_hash: {sent.phone_code_hash[:20]}...")
                
                code = input("\n📲 Введи код из Telegram: ").strip()
                
                try:
                    await client.sign_in(phone, code)
                    print("✅ Авторизация успешна!")
                    me = await client.get_me()
                    print(f"👤 Аккаунт: {me.first_name} {me.last_name or ''}")
                except Exception as e:
                    if "password" in str(e).lower() or "2fa" in str(e).lower():
                        print("🔐 Требуется 2FA пароль!")
                        password = input("Введи пароль 2FA: ").strip()
                        await client.sign_in(password=password)
                        print("✅ Авторизация с 2FA успешна!")
                    else:
                        raise e
                        
            except FloodWaitError as e:
                print(f"⏳ FLOOD WAIT! Нужно ждать {e.seconds} секунд ({e.seconds // 60} минут)")
                print(f"   Попробуй снова после: {e.seconds // 3600}ч {(e.seconds % 3600) // 60}м")
                
            except PhoneNumberBannedError:
                print("🚫 Номер телефона ЗАБЛОКИРОВАН в Telegram!")
                
            except PhoneNumberInvalidError:
                print("❌ Неверный формат номера телефона!")
                print("   Должен быть в формате: +79991234567")
                
    except Exception as e:
        print(f"❌ Ошибка: {type(e).__name__}: {e}")
        
    finally:
        await client.disconnect()

asyncio.run(auth())
