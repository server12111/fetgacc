from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.account import GetAuthorizationsRequest
from config import api_id, api_hash
import phonenumbers
import pycountry


def get_country_info(phone: str):
    """Получает флаг и название страны по номеру телефона"""
    try:
        num = phonenumbers.parse(phone, None)
        region = phonenumbers.region_code_for_number(num)
        if not region:
            return "🏳️", "Неизвестно"
        country = pycountry.countries.get(alpha_2=region)
        country_name = country.name if country else "Неизвестно"
        flag = chr(127397 + ord(region[0])) + chr(127397 + ord(region[1]))
        return flag, country_name
    except:
        return "🏳️", "Неизвестно"


async def get_codes(auth_key: str):
    """Получает список активных сессий (кодов входа) аккаунта"""
    codes = []
    try:
        client = TelegramClient(StringSession(auth_key), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return [("❌ Сессия недействительна", "")]

        result = await client(GetAuthorizationsRequest())
        for auth in result.authorizations:
            device = auth.device_model or "Unknown"
            platform = auth.platform or ""
            app = auth.app_name or ""
            date = auth.date_created.strftime("%Y-%m-%d %H:%M") if auth.date_created else "N/A"
            info = f"{device} {platform} {app}".strip()
            codes.append((info, date))

        await client.disconnect()
    except Exception as e:
        codes.append((f"Ошибка: {str(e)}", ""))

    return codes if codes else [("Нет активных сессий", "")]


async def leave(auth_key: str):
    """Завершает сессию (выходит из аккаунта)"""
    try:
        client = TelegramClient(StringSession(auth_key), api_id, api_hash)
        await client.connect()

        if await client.is_user_authorized():
            await client.log_out()
        else:
            await client.disconnect()
    except Exception as e:
        print(f"Ошибка при выходе: {e}")


async def check_account(auth_key: str):
    """Проверяет, жив ли аккаунт"""
    try:
        client = TelegramClient(StringSession(auth_key), api_id, api_hash)
        await client.connect()
        authorized = await client.is_user_authorized()
        await client.disconnect()
        return authorized
    except:
        return False
