import sys
sys.stdout.reconfigure(encoding='utf-8')

from aiogram import Dispatcher, Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
import asyncio, db, config
import aiohttp


async def update_exchange_rates():
    """
    Оновлює курси RUB/USD та EUR/USD з публічного API.
    Використовує api.exchangerate-api.com (безкоштовно, без ключа).
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.exchangerate-api.com/v4/latest/USD",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
                rates = data.get("rates", {})
                rub = rates.get("RUB")
                eur = rates.get("EUR")
                if rub and rub > 0:
                    rub_usd = round(1 / rub, 6)
                    db.set_setting("rate_rub_usd", rub_usd)
                    print(f"[RATES] RUB→USD: {rub_usd:.6f} (1$ = {rub:.2f}₽)")
                if eur and eur > 0:
                    eur_usd = round(1 / eur, 6)
                    db.set_setting("rate_eur_usd", eur_usd)
                    print(f"[RATES] EUR→USD: {eur_usd:.6f}")
    except Exception as e:
        print(f"[RATES] Помилка оновлення курсів: {e}")


async def rates_updater():
    """Фонова задача: оновлює курси валют кожну годину."""
    UPDATE_INTERVAL = 60 * 60  # 1 година
    await update_exchange_rates()  # одразу при старті
    while True:
        await asyncio.sleep(UPDATE_INTERVAL)
        await update_exchange_rates()


async def balance_monitor(bot: Bot):
    """
    Фонова задача: перевіряє баланс LZT кожні 30 хв.
    Якщо баланс < порогу (за замовчуванням 500₽) — сповіщає адмінів.
    Повторне сповіщення надсилається тільки після того як баланс знову піднявся і впав.
    """
    CHECK_INTERVAL = 30 * 60  # 30 хвилин

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            from message import get_lzt_client
            lzt = get_lzt_client()
            if not lzt:
                continue

            balance, currency = await lzt.get_balance()
            threshold = float(db.get_setting("balance_alert_threshold", "500"))
            already_notified = db.get_setting("balance_alert_sent", "0")

            if balance < threshold:
                if already_notified == "0":
                    for owner_id in config.OWNERS:
                        try:
                            await bot.send_message(
                                owner_id,
                                f"⚠️ <b>Увага! Баланс LZT Market низький</b>\n\n"
                                f"💰 Поточний баланс: <code>{balance:.2f}{currency}</code>\n"
                                f"🔴 Поріг: <code>{threshold:.0f}{currency}</code>\n\n"
                                f"Поповніть баланс щоб бот міг купувати акаунти."
                            )
                        except:
                            pass
                    db.set_setting("balance_alert_sent", "1")
                    print(f"[MONITOR] Баланс LZT низький: {balance:.2f}₽")
            else:
                # Баланс в нормі — скидаємо флаг щоб наступного разу знову сповістити
                if already_notified == "1":
                    db.set_setting("balance_alert_sent", "0")

        except Exception as e:
            print(f"[MONITOR] Помилка перевірки балансу: {e}")


async def main():
    import message
    db.initialize_db()
    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(message.rt)
    print("🤖 Бот запущений!")

    # Запускаємо фонові задачі
    asyncio.create_task(balance_monitor(bot))
    print("📡 Моніторинг балансу LZT запущено (кожні 30 хв)")
    asyncio.create_task(rates_updater())
    print("💱 Автооновлення курсів валют запущено (кожну годину)")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
