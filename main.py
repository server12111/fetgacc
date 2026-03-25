import sys
sys.stdout.reconfigure(encoding='utf-8')

from aiogram import Dispatcher, Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
import asyncio, db, config, os
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


async def ton_watcher(bot: Bot):
    """Фонова задача: кожну хвилину перевіряє вхідні TON транзакції і зараховує баланс."""
    import datetime
    import re
    from toncenter import get_recent_transactions

    CHECK_INTERVAL = 60

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            address = db.get_setting("tonkeeper_address", "") or config.TON_ADDRESS
            ton_rate_str = db.get_setting("ton_usd_rate", "")
            if not address or not ton_rate_str:
                continue

            ton_rate = float(ton_rate_str)
            api_key = os.getenv("TON_CENTER_API_KEY", "")
            txs = await get_recent_transactions(address, api_key)

            for tx in txs:
                tx_hash = tx["hash"]
                if db.TonDeposit.get_or_none(tx_hash=tx_hash):
                    continue  # вже оброблено

                comment = tx["comment"]
                match = re.fullmatch(r"topup_(\d+)", comment)
                if not match:
                    continue

                uid = int(match.group(1))
                user = db.User.get_or_none(id=uid)
                if not user:
                    continue

                amount_ton = tx["amount_ton"]
                amount_usd = round(amount_ton * ton_rate, 4)

                user.balance = round(user.balance + amount_usd, 4)
                user.save()

                db.TonDeposit.create(
                    tx_hash=tx_hash,
                    uid=uid,
                    amount_ton=amount_ton,
                    amount_usd=amount_usd,
                    credited_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )

                print(f"[TON] Зараховано {amount_ton:.4f} TON (${amount_usd:.4f}) → user {uid}")

                try:
                    await bot.send_message(
                        uid,
                        f"✅ <b>Поповнення через TON підтверджено!</b>\n\n"
                        f"💎 Отримано: <code>{amount_ton:.4f} TON</code>\n"
                        f"💵 Зараховано: <code>${amount_usd:.4f}</code>\n"
                        f"💳 Новий баланс: <code>${user.balance:.4f}</code>"
                    )
                except Exception:
                    pass

                for owner_id in config.OWNERS:
                    try:
                        await bot.send_message(
                            owner_id,
                            f"💎 <b>TON депозит</b>\n\n"
                            f"👤 User: <code>{uid}</code>\n"
                            f"💰 {amount_ton:.4f} TON → ${amount_usd:.4f}"
                        )
                    except Exception:
                        pass

        except Exception as e:
            print(f"[TON] Помилка watcher: {e}")


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
    asyncio.create_task(ton_watcher(bot))
    print("💎 TON watcher запущено (кожну хвилину)")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
