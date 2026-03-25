import aiohttp
from config import crypto_bot_token

# CryptoBot (Crypto Pay) API
API_URL = "https://pay.crypt.bot/api"
HEADERS = {"Crypto-Pay-API-Token": crypto_bot_token}


async def create_invoice(amount: float, description: str = "Пополнение баланса"):
    """
    Создает счёт в CryptoBot.
    Возвращает (pay_url, invoice_id)
    """
    async with aiohttp.ClientSession() as session:
        payload = {
            "asset": "USDT",
            "amount": str(amount),
            "description": description,
            "paid_btn_name": "callback",
            "paid_btn_url": "https://t.me"  # можно заменить на свою ссылку
        }
        async with session.post(
            f"{API_URL}/createInvoice",
            headers=HEADERS,
            json=payload
        ) as resp:
            data = await resp.json()

            if data.get("ok"):
                result = data["result"]
                return result["pay_url"], result["invoice_id"]
            else:
                raise Exception(f"CryptoBot API error: {data}")


async def is_invoice_paid(invoice_id: int):
    """
    Проверяет, оплачен ли счёт.
    Возвращает (True/False, сумма)
    """
    async with aiohttp.ClientSession() as session:
        params = {"invoice_ids": str(invoice_id)}
        async with session.get(
            f"{API_URL}/getInvoices",
            headers=HEADERS,
            params=params
        ) as resp:
            data = await resp.json()

            if data.get("ok") and data["result"]["items"]:
                invoice = data["result"]["items"][0]
                if invoice["status"] == "paid":
                    return True, float(invoice["amount"])
                else:
                    return False, 0
            return False, 0


async def get_balance():
    """Получает баланс CryptoBot кошелька"""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_URL}/getBalance",
            headers=HEADERS
        ) as resp:
            data = await resp.json()
            if data.get("ok"):
                return data["result"]
            return []
