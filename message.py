from aiogram import types, F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from telethon import TelegramClient
from telethon.sessions import StringSession
import db, accounts, cryptobot, datetime, json, asyncio
from urllib.parse import urlparse, parse_qs
from config import *
from lzt_api import LztAPI
import phonenumbers
import pycountry

rt = Router()


# ==================== FSM стани (мають бути першими) ====================

class Form(StatesGroup):
    phone = State()
    code = State()
    price = State()
    desc = State()


class AdminInput(StatesGroup):
    markup = State()
    price_range = State()
    filter_url = State()
    lzt_token = State()
    broadcast = State()
    add_balance = State()
    balance_threshold = State()
    rub_rate = State()
    eur_rate = State()
    section_name = State()
    section_filter = State()
    section_editname = State()
    section_editfilter = State()
    referral_percent = State()
    review_channel = State()
    required_channel = State()   # канал обязательной подписки
    card_text = State()          # текст оплати на карту
    tonkeeper_addr = State()     # адреса Tonkeeper
    ton_rate = State()           # курс: скільки USD за 1 TON
    support_url = State()        # ссылка на поддержку


class ReviewInput(StatesGroup):
    text = State()


class TopupInput(StatesGroup):
    amount = State()  # юзер вводить суму вручну


# ==================== LZT клієнт ====================

def get_lzt_client():
    token = db.get_setting("lzt_token", LZT_TOKEN)
    return LztAPI(token) if token else None


# ==================== Обязательная подписка ====================

async def _check_subscription(bot, uid: int) -> bool:
    """Возвращает True если подписка не требуется или юзер подписан."""
    channel = db.get_setting("required_channel", "")
    if not channel:
        return True
    try:
        member = await bot.get_chat_member(channel, uid)
        return member.status not in ("left", "kicked", "banned")
    except:
        return True  # если не удалось проверить — пропускаем


def _sub_wall(channel: str) -> tuple[str, InlineKeyboardMarkup]:
    """Возвращает текст и клавиатуру для стены подписки."""
    text = (
        "🔒 <b>Доступ ограничен</b>\n\n"
        "Для использования бота необходимо подписаться на наш канал.\n\n"
        "После подписки нажмите кнопку ниже 👇"
    )
    bild = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{channel.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")]
    ])
    return text, bild


# Кеш акаунтів: {uid: {"items": [...], "by_country": {safe_name: [items]}}}
_shop_cache: dict = {}

# Кеш локального магазину: {uid: {country_display: [AccountsShop, ...]}}
_local_cache: dict = {}

# Глобальний кеш пошуку LZT — спільний для всіх юзерів
# {cache_key: {"ts": timestamp, "items": [...], "markup": float}}
_search_cache: dict = {}
_SEARCH_CACHE_TTL = 300  # 5 хвилин


# ==================== Конвертація валют ====================

def convert_to_usd(price: float, currency: str) -> float:
    """Конвертує ціну в долари за поточним курсом з налаштувань"""
    currency = (currency or "").upper()
    if currency in ("USD", "$", ""):
        return round(price, 2)
    if currency in ("RUB", "RUR", "₽", "RUBLE", "RUBLES"):
        rate = float(db.get_setting("rate_rub_usd", "0.011"))
        return round(price * rate, 2)
    if currency in ("EUR", "€"):
        rate = float(db.get_setting("rate_eur_usd", "1.08"))
        return round(price * rate, 2)
    # Якщо невідома — вважаємо рублі
    rate = float(db.get_setting("rate_rub_usd", "0.011"))
    return round(price * rate, 2)


def get_item_price_usd(item: dict, markup: float) -> tuple[float, float]:
    """
    Повертає (ціна_закупівлі_usd, ціна_продажу_usd).
    Визначає валюту з поля currency або з назви цінових полів.
    """
    # Спробуємо отримати ціну в USD напряму
    if item.get("price_usd"):
        lzt_usd = float(item["price_usd"])
        return lzt_usd, round(lzt_usd * markup, 2)

    price = float(item.get("price", 0))
    currency = item.get("currency", "RUB")

    lzt_usd = convert_to_usd(price, currency)
    return lzt_usd, round(lzt_usd * markup, 2)


# ==================== Допоміжні функції ====================

def get_country_info(phone):
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


# Маппінг числових ID країн LZT → назва (доповнюється при потребі)
_LZT_COUNTRY_MAP = {
    "1": "🇷🇺 Россия", "2": "🇺🇦 Украина", "3": "🇧🇾 Беларусь",
    "4": "🇰🇿 Казахстан", "5": "🇺🇿 Узбекистан", "6": "🇰🇬 Киргизстан",
    "7": "🇦🇿 Азербайджан", "8": "🇦🇲 Армения", "9": "🇬🇪 Грузия",
    "10": "🇲🇩 Молдова", "11": "🇹🇯 Таджикистан", "12": "🇹🇲 Туркменистан",
    "13": "🇺🇸 США", "14": "🇩🇪 Германия", "15": "🇫🇷 Франция",
    "16": "🇬🇧 Великобритания", "17": "🇮🇹 Италия", "18": "🇪🇸 Испания",
    "19": "🇵🇱 Польша", "20": "🇨🇿 Чехия",
    "ru": "🇷🇺 Россия", "ua": "🇺🇦 Украина", "by": "🇧🇾 Беларусь",
    "kz": "🇰🇿 Казахстан", "uz": "🇺🇿 Узбекистан", "us": "🇺🇸 США",
    "de": "🇩🇪 Германия", "fr": "🇫🇷 Франция", "gb": "🇬🇧 Великобритания",
    "it": "🇮🇹 Италия", "es": "🇪🇸 Испания", "pl": "🇵🇱 Польша",
}


def extract_country_name(item: dict) -> str:
    """Витягує читабельну назву країни з LZT item"""
    # Спочатку шукаємо текстову назву
    for field in ("country_title", "region_title"):
        val = item.get(field)
        if val and isinstance(val, str) and not val.isdigit():
            return val

    # Якщо country — об'єкт зі словником
    country_obj = item.get("country")
    if isinstance(country_obj, dict):
        return country_obj.get("title") or country_obj.get("name") or "Другое"

    # Якщо country — рядок або число → шукаємо в маппінгу
    if country_obj is not None:
        key = str(country_obj).lower()
        if key in _LZT_COUNTRY_MAP:
            return _LZT_COUNTRY_MAP[key]
        if not str(country_obj).isdigit():
            return str(country_obj)  # повертаємо як є (напр. "ru")

    return "🏳️ Другое"


def _is_ukraine(item: dict) -> bool:
    """Перевіряє чи акаунт з України."""
    name = extract_country_name(item).lower()
    return "украин" in name or "ukraine" in name or "🇺🇦" in name


def parse_lzt_filter_url(url: str) -> dict:
    try:
        parsed = urlparse(url)
        params = {}
        for k, v in parse_qs(parsed.query).items():
            params[k] = v[0]
        return params
    except:
        return {}


async def build_admin_panel_text(bot) -> tuple[str, InlineKeyboardMarkup]:
    lzt = get_lzt_client()
    lzt_balance_str = "—"
    if lzt:
        try:
            bal, currency = await lzt.get_balance()
            lzt_balance_str = f"{bal:.2f}{currency}"
        except:
            lzt_balance_str = "❌ ошибка"

    markup_val = db.get_setting("lzt_markup", str(LZT_MARKUP))
    pmin_val = db.get_setting("lzt_pmin", str(LZT_MIN_PRICE))
    pmax_val = db.get_setting("lzt_pmax", str(LZT_MAX_PRICE))
    filter_set = db.get_setting("lzt_filter", "")
    confirm_mode = db.get_setting("lzt_confirm", "1")
    token_set = bool(db.get_setting("lzt_token", LZT_TOKEN))
    balance_threshold = db.get_setting("balance_alert_threshold", "500")
    ref_percent = db.get_setting("referral_percent", "5")
    req_channel = db.get_setting("required_channel", "")

    pending_count = db.PendingPurchase.select().where(db.PendingPurchase.status == "pending").count()
    total_users = db.User.select().count()
    total_lzt = db.LztTransaction.select().count()
    total_local = db.Accounts.select().count()
    total_revenue = sum(t.sell_price for t in db.LztTransaction.select())
    markup_val_adm = float(db.get_setting("lzt_markup", str(LZT_MARKUP)))
    total_net_adm = sum(
        t.sell_price - (t.sell_price / markup_val_adm if markup_val_adm > 0 else 0)
        for t in db.LztTransaction.select()
    )

    text = (
        "🛡️ <b>АДМИН ПАНЕЛЬ</b>\n\n"
        f"💰 Баланс LZT: <b>{lzt_balance_str}</b>\n"
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"🛒 Продано (LZT): <b>{total_lzt}</b> | (локал): <b>{total_local}</b>\n"
        f"💵 Доход LZT: <b>${total_revenue:.2f}</b>\n"
        f"💚 Чистый доход: <b>${total_net_adm:.2f}</b>\n\n"
        "⚙️ <b>Настройки LZT Market:</b>\n"
        f"  • Множитель: <code>{markup_val}x</code> (+{(float(markup_val)-1)*100:.0f}%)\n"
        f"  • Цена закупки: <code>${pmin_val} — ${pmax_val}</code>\n"
        f"  • Курс RUB→USD: <code>{db.get_setting('rate_rub_usd', '0.011')}</code> <i>(авто)</i>\n"
        f"  • Курс EUR→USD: <code>{db.get_setting('rate_eur_usd', '1.08')}</code> <i>(авто)</i>\n"
        f"  • Фильтр URL: {'<code>✅ есть</code>' if filter_set else '<code>❌ нет</code>'}\n"
        f"  • LZT Token: {'<code>✅ есть</code>' if token_set else '<code>❌ нет</code>'}\n"
        f"  • Подтверждение: {'<code>🔴 ручное</code>' if confirm_mode == '1' else '<code>🟢 авто</code>'}\n"
        f"  • Порог баланса: <code>{balance_threshold}₽</code>\n"
        f"  • Рефералы: <code>{ref_percent}%</code>\n"
        f"  • ОП (обяз. подписка): <code>{req_channel or '❌ выкл.'}</code>\n"
        f"  • В ожидании: <code>{pending_count}</code>\n\n"
        "💳 <b>Оплата:</b>\n"
        f"  • Карта: <code>{'✅ задана' if db.get_setting('card_payment_text', '') else '❌ не задана'}</code>\n"
        f"  • Tonkeeper: <code>{'✅ задан' if db.get_setting('tonkeeper_address', '') else '❌ не задан'}</code>\n"
        f"  • Курс TON: <code>{'1 TON = $' + db.get_setting('ton_usd_rate', '') if db.get_setting('ton_usd_rate', '') else '❌ не задан'}</code>"
    )

    confirm_btn_text = "🔴 Ручное подтв." if confirm_mode == "1" else "🟢 Авто подтв."

    bild = InlineKeyboardBuilder()
    bild.row(
        InlineKeyboardButton(text="💰 Баланс LZT", callback_data="adm_lzt_bal"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")
    )
    bild.row(
        InlineKeyboardButton(text="⚙️ Множитель", callback_data="adm_set_markup"),
        InlineKeyboardButton(text="💲 Цены", callback_data="adm_set_prices")
    )
    bild.row(
        InlineKeyboardButton(text="🔗 Фильтр URL", callback_data="adm_set_filter"),
        InlineKeyboardButton(text="🔑 LZT Token", callback_data="adm_set_token")
    )
    bild.row(
        InlineKeyboardButton(text=confirm_btn_text, callback_data="adm_toggle_confirm"),
        InlineKeyboardButton(text=f"⏳ Ожидают ({pending_count})", callback_data="adm_pending")
    )
    bild.row(
        InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast"),
        InlineKeyboardButton(text="👤 Баланс юзера", callback_data="adm_user_balance")
    )
    bild.row(
        InlineKeyboardButton(text=f"👥 Рефералы ({ref_percent}%)", callback_data="adm_set_ref_percent"),
        InlineKeyboardButton(text=f"🔔 Порог ({balance_threshold}₽)", callback_data="adm_set_threshold")
    )
    sections_count = db.ShopSection.select().count()
    bild.row(
        InlineKeyboardButton(text=f"📂 Разделы магазина ({sections_count})", callback_data="adm_sections")
    )
    req_channel = db.get_setting("required_channel", "")
    sub_btn_text = f"🔒 ОП: {req_channel}" if req_channel else "🔓 ОП: выкл."
    bild.row(
        InlineKeyboardButton(text=sub_btn_text, callback_data="adm_set_req_channel"),
        InlineKeyboardButton(text="📢 Канал отзывов", callback_data="adm_set_review_channel")
    )
    card_set = "✅" if db.get_setting("card_payment_text", "") else "❌"
    ton_set = "✅" if db.get_setting("tonkeeper_address", "") else "❌"
    bild.row(
        InlineKeyboardButton(text=f"💳 Оплата карта {card_set}", callback_data="adm_set_card_text"),
        InlineKeyboardButton(text=f"💎 Tonkeeper {ton_set}", callback_data="adm_set_tonkeeper")
    )
    bild.row(
        InlineKeyboardButton(text="💬 Ссылка поддержки", callback_data="adm_set_support_url")
    )
    bild.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"))

    return text, bild.as_markup()


# ==================== FSM: Додавання акаунту ====================

@rt.message(Form.phone)  # type: ignore
async def get_phone(m: Message, state: FSMContext):
    phone = m.text.strip()
    try:
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(phone)
        await state.update_data(phone=phone, hash=sent.phone_code_hash, session=client.session.save())
        await client.disconnect()
        await m.answer("Введите код из Telegram:")
        await state.set_state(Form.code)
    except Exception as e:
        await m.answer(f"❌ Ошибка: {e}")
        await state.clear()


@rt.message(Form.code)
async def get_code(m: Message, state: FSMContext):
    d = await state.get_data()
    try:
        client = TelegramClient(StringSession(d["session"]), api_id, api_hash)
        await client.connect()
        await client.sign_in(d["phone"], m.text.strip(), phone_code_hash=d["hash"])
        me = await client.get_me()
        s = client.session.save()
        await client.disconnect()
        await state.update_data(accid=me.id, auth=s)
        await m.answer("Введите цену (в $):")
        await state.set_state(Form.price)
    except Exception as e:
        await m.answer(f"❌ Ошибка: {e}")
        await state.clear()


@rt.message(Form.price)
async def set_price(m: Message, state: FSMContext):
    try:
        p = float(m.text.replace(",", "."))
        await state.update_data(price=p)
        await m.answer("Введите описание:")
        await state.set_state(Form.desc)
    except ValueError:
        await m.answer("⚠️ Введите число")


@rt.message(Form.desc)
async def set_desc(m: Message, state: FSMContext):
    d = await state.get_data()
    db.AccountsShop.create(
        ShopID=int(datetime.datetime.now().timestamp()),
        AccountID=d["accid"],
        AccountNumber=d["phone"].replace(' ', '').replace('+', ''),
        AuthKey=d["auth"],
        Price=d["price"],
        description=m.text
    )
    await m.answer("✅ Аккаунт сохранён в магазин!")
    await state.clear()


# ==================== FSM: Адмін-панель ====================

@rt.message(AdminInput.markup)
async def admin_input_markup(m: Message, state: FSMContext):
    try:
        val = float(m.text.replace(",", "."))
        if val < 1.0:
            return await m.answer("⚠️ Минимум 1.0")
        db.set_setting("lzt_markup", val)
        await m.answer(f"✅ Множитель: <code>{val}x</code> (+{(val-1)*100:.0f}%)")
    except:
        await m.answer("⚠️ Введите число, например: <code>1.5</code>")
    await state.clear()


@rt.message(AdminInput.price_range)
async def admin_input_prices(m: Message, state: FSMContext):
    try:
        parts = m.text.strip().split()
        pmin, pmax = float(parts[0]), float(parts[1])
        db.set_setting("lzt_pmin", pmin)
        db.set_setting("lzt_pmax", pmax)
        await m.answer(f"✅ Цены: <code>${pmin} — ${pmax}</code>")
    except:
        await m.answer("⚠️ Формат: <code>0.5 5.0</code>")
    await state.clear()


@rt.message(AdminInput.filter_url)
async def admin_input_filter(m: Message, state: FSMContext):
    url = m.text.strip()
    if url.lower() in ("очистити", "clear", "-"):
        db.set_setting("lzt_filter", "")
        await m.answer("✅ Фильтр очищен")
    else:
        params = parse_lzt_filter_url(url)
        if not params:
            return await m.answer("⚠️ Не удалось разобрать URL. Отправьте <code>-</code> чтобы сбросить.")
        db.set_setting("lzt_filter", url)
        params_str = "\n".join(f"  • {k}: {v}" for k, v in params.items())
        await m.answer(f"✅ Фильтр сохранён!\n\nПараметры:\n{params_str}")
    await state.clear()


@rt.message(AdminInput.lzt_token)
async def admin_input_token(m: Message, state: FSMContext):
    db.set_setting("lzt_token", m.text.strip())
    await m.answer("✅ LZT Token сохранён!")
    try:
        await m.delete()
    except:
        pass
    await state.clear()


@rt.message(AdminInput.broadcast)
async def admin_input_broadcast(m: Message, state: FSMContext):
    await state.clear()
    count = 0
    users = list(db.User.select())
    status_msg = await m.answer(f"⏳ Рассылаю {len(users)} пользователям...")
    for user in users:
        try:
            if m.photo:
                await m.bot.send_photo(
                    user.id, m.photo[-1].file_id,
                    caption=m.caption or "",
                    parse_mode="HTML"
                )
            elif m.text:
                await m.bot.send_message(user.id, f"📢 {m.text}", parse_mode="HTML")
            count += 1
        except:
            pass
    try:
        await status_msg.edit_text(f"✅ Рассылка завершена: {count}/{len(users)} получили")
    except:
        await m.answer(f"✅ Разослано {count}/{len(users)}")


@rt.message(AdminInput.referral_percent)
async def admin_input_ref_percent(m: Message, state: FSMContext):
    try:
        val = float(m.text.replace(",", "."))
        if val < 0 or val > 100:
            return await m.answer("⚠️ Введите от 0 до 100")
        db.set_setting("referral_percent", val)
        await m.answer(f"✅ Реферальный %: <code>{val}%</code>\nРеферы будут получать {val}% от каждой покупки")
    except:
        await m.answer("⚠️ Введите число, например: <code>5</code>")
    await state.clear()


@rt.message(AdminInput.review_channel)
async def admin_input_review_channel(m: Message, state: FSMContext):
    val = m.text.strip()
    if val == "-":
        db.set_setting("review_channel", "")
        await m.answer("✅ Канал отзывов отключён")
    else:
        db.set_setting("review_channel", val)
        await m.answer(f"✅ Канал отзывов: <code>{val}</code>")
    await state.clear()


@rt.message(AdminInput.required_channel)
async def admin_input_required_channel(m: Message, state: FSMContext):
    val = m.text.strip()
    if val == "-":
        db.set_setting("required_channel", "")
        await m.answer("✅ Обязательная подписка <b>отключена</b> — бот доступен всем.")
    else:
        # Проверяем что бот может читать участников этого канала
        try:
            member = await m.bot.get_chat_member(val, m.from_user.id)
            db.set_setting("required_channel", val)
            await m.answer(
                f"✅ ОП включена!\n\nКанал: <code>{val}</code>\n\n"
                "Теперь все пользователи обязаны подписаться перед использованием бота."
            )
        except Exception as e:
            await m.answer(
                f"⚠️ Не удалось проверить канал: <code>{e}</code>\n\n"
                "Убедитесь что:\n"
                "• Бот добавлен как <b>администратор</b> канала\n"
                "• Username указан правильно (с @)\n\n"
                "Всё равно сохранить? Введите канал ещё раз или <code>-</code> для отмены."
            )
            # Сохраняем несмотря на ошибку проверки (может быть публичный канал)
            db.set_setting("required_channel", val)
    await state.clear()


@rt.message(AdminInput.card_text)
async def admin_input_card_text(m: Message, state: FSMContext):
    val = m.text.strip()
    if val == "-":
        db.set_setting("card_payment_text", "")
        await m.answer("✅ Оплата на карту отключена.")
    else:
        db.set_setting("card_payment_text", val)
        await m.answer(f"✅ Текст оплаты на карту сохранён.")
    await state.clear()


@rt.message(AdminInput.tonkeeper_addr)
async def admin_input_tonkeeper(m: Message, state: FSMContext):
    val = m.text.strip()
    if val == "-":
        db.set_setting("tonkeeper_address", "")
        await m.answer("✅ Tonkeeper отключён.")
    else:
        db.set_setting("tonkeeper_address", val)
        await m.answer(f"✅ Адрес Tonkeeper сохранён:\n<code>{val}</code>")
    await state.clear()


@rt.message(AdminInput.ton_rate)
async def admin_input_ton_rate(m: Message, state: FSMContext):
    try:
        rate = float(m.text.strip().replace(",", "."))
        if rate <= 0:
            raise ValueError
        db.set_setting("ton_usd_rate", str(rate))
        await m.answer(f"✅ Курс сохранён: <code>1 TON = ${rate}</code>")
    except ValueError:
        await m.answer("❌ Неверный формат. Введите число, например: <code>5.2</code>")
    await state.clear()


@rt.message(AdminInput.support_url)
async def admin_input_support_url(m: Message, state: FSMContext):
    url = m.text.strip()
    db.set_setting("support_url", url)
    await m.answer(f"✅ Ссылка поддержки сохранена: <code>{url}</code>")
    await state.clear()


@rt.message(ReviewInput.text)
async def handle_review(m: Message, state: FSMContext):
    await state.clear()
    review_channel = db.get_setting("review_channel", "")
    if not review_channel:
        await m.answer("✅ Спасибо за отзыв!")
        return
    user_display = f"@{m.from_user.username}" if m.from_user.username else f"#{m.from_user.id}"
    try:
        await m.bot.send_message(
            review_channel,
            f"⭐ <b>Новый отзыв</b>\n\n"
            f"👤 {user_display}\n\n"
            f"💬 {m.text}",
            parse_mode="HTML"
        )
        await m.answer(
            "✅ <b>Спасибо за отзыв!</b>\n\nВаше мнение очень важно для нас 🙏",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
            ])
        )
    except Exception as e:
        await m.answer("✅ Спасибо за отзыв!")


@rt.message(AdminInput.add_balance)
async def admin_input_add_balance(m: Message, state: FSMContext):
    try:
        parts = m.text.strip().split()
        target_id, amount = int(parts[0]), float(parts[1])
        user = db.User.get_or_none(id=target_id)
        if not user:
            await m.answer("❌ Пользователь не найден")
        else:
            user.balance += amount
            user.save()
            await m.answer(f"✅ Баланс <code>{target_id}</code> +<code>${amount}</code> → <code>${user.balance:.2f}</code>")
            try:
                await m.bot.send_message(target_id, f"✅ Ваш баланс пополнен на <code>${amount}</code>!")
            except:
                pass
    except:
        await m.answer("⚠️ Формат: <code>ID сумма</code>")
    await state.clear()


@rt.message(AdminInput.balance_threshold)
async def admin_input_threshold(m: Message, state: FSMContext):
    try:
        val = float(m.text.replace(",", "."))
        db.set_setting("balance_alert_threshold", val)
        db.set_setting("balance_alert_sent", "0")
        await m.answer(f"✅ Порог: <code>{val:.0f}₽</code>")
    except:
        await m.answer("⚠️ Введите число")
    await state.clear()


@rt.message(AdminInput.rub_rate)
async def admin_input_rub_rate(m: Message, state: FSMContext):
    try:
        val = float(m.text.replace(",", "."))
        db.set_setting("rate_rub_usd", val)
        await m.answer(f"✅ Курс RUB→USD: <code>{val}</code>\n1000₽ = ${1000*val:.2f}")
    except:
        await m.answer("⚠️ Введите число, например: <code>0.011</code>")
    await state.clear()


@rt.message(AdminInput.eur_rate)
async def admin_input_eur_rate(m: Message, state: FSMContext):
    try:
        val = float(m.text.replace(",", "."))
        db.set_setting("rate_eur_usd", val)
        await m.answer(f"✅ Курс EUR→USD: <code>{val}</code>")
    except:
        await m.answer("⚠️ Введите число, например: <code>1.08</code>")
    await state.clear()


# ==================== FSM: Розділи магазину ====================

@rt.message(AdminInput.section_name)
async def admin_section_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if not name:
        return await m.answer("⚠️ Название не может быть пустым")
    await state.update_data(new_section_name=name)
    await m.answer(
        f"📂 Раздел: <b>{name}</b>\n\nТеперь отправьте URL фильтра с lzt.market\n"
        "Или <code>пропустить</code> — раздел будет без фильтра (все аккаунты):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="adm_sections")]
        ])
    )
    await state.set_state(AdminInput.section_filter)


@rt.message(AdminInput.section_filter)
async def admin_section_filter(m: Message, state: FSMContext):
    data = await state.get_data()
    name = data.get("new_section_name", "Без названия")
    url = m.text.strip()
    filter_url = "" if url.lower() in ("пропустити", "пропустить", "skip", "-") else url

    if filter_url:
        params = parse_lzt_filter_url(filter_url)
        if not params:
            return await m.answer("⚠️ Не удалось разобрать URL. Отправьте <code>пропустить</code> чтобы оставить пустым.")

    max_order = db.ShopSection.select().count()
    db.ShopSection.create(name=name, filter_url=filter_url, order=max_order)
    await m.answer(f"✅ Раздел <b>{name}</b> добавлен!")
    await state.clear()


@rt.message(AdminInput.section_editname)
async def admin_section_editname(m: Message, state: FSMContext):
    data = await state.get_data()
    sec_id = data.get("edit_section_id")
    section = db.ShopSection.get_or_none(id=sec_id)
    if not section:
        await m.answer("❌ Раздел не найден")
        await state.clear()
        return
    section.name = m.text.strip()
    section.save()
    await m.answer(f"✅ Название изменено на <b>{section.name}</b>")
    await state.clear()


@rt.message(AdminInput.section_editfilter)
async def admin_section_editfilter(m: Message, state: FSMContext):
    data = await state.get_data()
    sec_id = data.get("edit_section_id")
    section = db.ShopSection.get_or_none(id=sec_id)
    if not section:
        await m.answer("❌ Раздел не найден")
        await state.clear()
        return
    url = m.text.strip()
    if url.lower() in ("очистити", "очистить", "clear", "-"):
        section.filter_url = ""
        section.save()
        await m.answer("✅ Фильтр очищен")
    else:
        params = parse_lzt_filter_url(url)
        if not params:
            return await m.answer("⚠️ Не удалось разобрать URL. Отправьте <code>-</code> чтобы сбросить.")
        section.filter_url = url
        section.save()
        params_str = "\n".join(f"  • {k}: {v}" for k, v in params.items())
        await m.answer(f"✅ Фильтр сохранён!\n\nПараметры:\n{params_str}")
    await state.clear()


# ==================== FSM: Поповнення балансу ====================

@rt.message(TopupInput.amount)
async def topup_input_amount(m: Message, state: FSMContext):
    try:
        amount = float(m.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Введите сумму больше 0, например: <code>5</code>")
        return
    await state.clear()

    bild = InlineKeyboardBuilder()
    bild.button(text="💎 CryptoBot", callback_data=f"popol-{amount}")
    if TON_ADDRESS:
        bild.button(text="💎 Tonkeeper", callback_data=f"pay_ton-{amount}")
    if db.get_setting("card_payment_text", ""):
        bild.button(text="💳 На карту", callback_data="pay_card")
    bild.button(text="⬅️ Назад", callback_data="profile")
    bild.adjust(1)
    await m.answer(
        f"💰 Сумма: <code>${amount:.2f}</code>\n\nВыберите способ оплаты:",
        reply_markup=bild.as_markup()
    )


# ==================== Текстові команди ====================

@rt.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    command = message.text.strip()
    await db.check_db(uid, message.from_user.username or "")

    # Проверка обязательной подписки (кроме /start и владельцев)
    if not command.startswith("/start") and uid not in OWNERS:
        if not await _check_subscription(message.bot, uid):
            channel = db.get_setting("required_channel", "")
            text, kbd = _sub_wall(channel)
            await message.answer(text, reply_markup=kbd)
            return

    if command.startswith("/start"):
        # Реферальна система: /start ref_12345678
        parts_cmd = command.split()
        if len(parts_cmd) > 1:
            arg = parts_cmd[1]
            if arg.startswith("ref_"):
                try:
                    ref_id = int(arg[4:])
                    if ref_id != uid:
                        user_obj = db.User.get_or_none(id=uid)
                        if user_obj and user_obj.referred_by == 0:
                            referrer = db.User.get_or_none(id=ref_id)
                            if referrer:
                                user_obj.referred_by = ref_id
                                user_obj.save()
                except:
                    pass

        bild = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Купить аккаунт", callback_data="shop")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
             InlineKeyboardButton(text="💬 Поддержка", url=db.get_setting("support_url", "t.me/sierafimv"))]
        ])
        await message.answer_sticker("CAACAgIAAxkBAAEPpnlo_3e8cyvIWZHNtwnzJSZG-LThYQACHYMAAjlScUl8oSOd4RZu2DYE")
        await message.answer(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Здесь вы можете купить Telegram-аккаунты быстро и безопасно.\n\n"
            "Выберите действие ниже 👇",
            reply_markup=bild
        )

    elif command.lower().startswith("/cancel"):
        await state.clear()
        await message.answer("✅ Отменено")

    elif command.lower() == "/admin":
        if uid in OWNERS:
            text, kbd = await build_admin_panel_text(message.bot)
            await message.answer(text, reply_markup=kbd)
        else:
            await message.answer("❌ Нет доступа")

    elif command.lower().startswith("/add_account"):
        if uid in OWNERS:
            await message.reply("Введите номер телефона:")
            await state.set_state(Form.phone)
        else:
            await message.answer("❌ Нет доступа")

    elif command.lower().startswith("/add_balance"):
        if uid in OWNERS:
            try:
                parts = command.split()
                user = db.User.get_or_none(id=int(parts[1]))
                if user:
                    user.balance += float(parts[2])
                    user.save()
                    await message.answer(f"✅ Пополнено на ${parts[2]}")
                else:
                    await message.answer("❌ Не найдено")
            except:
                await message.answer("⚠️ /add_balance ID сумма")

    elif command.lower().startswith("/stats"):
        if uid in OWNERS:
            await message.answer(
                f"📊 Пользователей: <code>{db.User.select().count()}</code>\n"
                f"🏪 В магазине: <code>{db.AccountsShop.select().count()}</code>\n"
                f"✅ Продано: <code>{db.Accounts.select().count()}</code>\n"
                f"🌐 Через LZT: <code>{db.LztTransaction.select().count()}</code>"
            )

    elif command.lower().startswith("/broadcast"):
        if uid in OWNERS:
            text = command.replace("/broadcast", "", 1).strip()
            if text:
                count = 0
                for u in db.User.select():
                    try:
                        await message.bot.send_message(u.id, text, parse_mode="HTML")
                        count += 1
                    except Exception:
                        pass
                await message.answer(f"✅ Розсилка відправлена: {count} користувачів")


# ==================== Допоміжна функція пошуку в магазині ====================

async def _get_account_text(lzt, item_data: dict, notify_msg=None) -> tuple[str, str | None]:
    """Форматує текст акаунту; якщо немає структурованих даних — логін через hex key або TData.
    Повертає (display_text, session_str або None)."""
    text = await lzt.format_account_data(item_data)
    if "⚠️" not in text:
        return text, None

    if notify_msg:
        try:
            await notify_msg.edit_text("⏳ Захожу в аккаунт...")
        except:
            pass

    result = None
    try:
        result = await asyncio.wait_for(lzt.try_login_with_key(item_data), timeout=20)
    except:
        pass

    if not result:
        if notify_msg:
            try:
                await notify_msg.edit_text("⏳ Загружаю TData...")
            except:
                pass
        try:
            result = await asyncio.wait_for(lzt.try_extract_tdata(item_data), timeout=30)
        except:
            pass

    if result:
        parts_r = result.split("|")
        session_str = parts_r[0].replace("session:", "")
        phone = parts_r[1].replace("phone:", "") if len(parts_r) > 1 else "неизвестно"
        display = (
            "📱 <b>Данные аккаунта</b>\n\n"
            f"📞 Телефон: <code>+{phone}</code>"
        )
        return display, session_str

    return text, None


async def _pay_referral(bot, buyer_id: int, sell_usd: float):
    """Виплачує реферальний бонус, якщо юзер прийшов по реферальному посиланню."""
    try:
        ref_percent = float(db.get_setting("referral_percent", "5"))
        if ref_percent <= 0:
            return
        buyer = db.User.get_or_none(id=buyer_id)
        if not buyer or not buyer.referred_by:
            return
        referrer = db.User.get_or_none(id=buyer.referred_by)
        if not referrer:
            return
        bonus = round(sell_usd * ref_percent / 100, 4)
        if bonus <= 0:
            return
        referrer.balance += bonus
        referrer.save()
        try:
            await bot.send_message(
                referrer.id,
                f"💎 <b>Реферальный бонус!</b>\n\nВаш реферал совершил покупку.\n"
                f"Начислено: <code>+${bonus:.4f}</code>"
            )
        except:
            pass
    except:
        pass


async def _send_review_request(bot, buyer_id: int):
    review_channel = db.get_setting("review_channel", "")
    if not review_channel:
        return
    try:
        await bot.send_message(
            buyer_id,
            "📝 <b>Оставьте отзыв!</b>\n\nРасскажите о своём опыте: что понравилось, что стоит улучшить?\n\n"
            "Ваш отзыв поможет нам стать лучше 🙏",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Написать отзыв", callback_data="review_start")],
                [InlineKeyboardButton(text="❌ Пропустить", callback_data="e")]
            ])
        )
    except:
        pass


async def _do_shop_search(cb: types.CallbackQuery, uid: int, page: int = 1,
                          filter_url: str = None, back_cb: str = "shop",
                          section_name: str = None):
    """Шукає акаунти в LZT (або показує локальні) і відображає розбивку по країнах."""
    lzt = get_lzt_client()
    if not lzt:
        # LZT не налаштований — показуємо локальний магазин
        await cb.message.edit_text("⏳ Загружаю аккаунты...")
        local_items = list(db.AccountsShop.select())
        if not local_items:
            return await cb.message.edit_text(
                "😔 Аккаунтов не найдено.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                ])
            )
        countries: dict = {}
        for item in local_items:
            flag, country_name = accounts.get_country_info(item.AccountNumber)
            display = f"{flag} {country_name}"
            countries.setdefault(display, []).append(item)
        _local_cache[uid] = countries
        bild = InlineKeyboardBuilder()
        for country, c_items in sorted(countries.items(), key=lambda x: -len(x[1])):
            min_price = min(it.Price for it in c_items)
            idx = list(countries.keys()).index(country)
            bild.button(
                text=f"{country} ({len(c_items)}) от ${min_price:.2f}",
                callback_data=f"local_country-{idx}"
            )
        bild.adjust(1)
        bild.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb),
                 InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))
        return await cb.message.edit_text("🌍 <b>Выберите страну</b>", reply_markup=bild.as_markup())

    # Визначаємо параметри фільтрації
    if filter_url:
        filter_params = parse_lzt_filter_url(filter_url) if filter_url else {}
    else:
        global_filter = db.get_setting("lzt_filter", "")
        filter_params = parse_lzt_filter_url(global_filter) if global_filter else {}

    pmin = float(filter_params.get("pmin", db.get_setting("lzt_pmin", str(LZT_MIN_PRICE))))
    pmax = float(filter_params.get("pmax", db.get_setting("lzt_pmax", str(LZT_MAX_PRICE))))
    markup_val = float(db.get_setting("lzt_markup", str(LZT_MARKUP)))

    # Глобальний кеш — один запит на всіх юзерів
    import time as _time
    cache_key = f"{filter_url or 'global'}_{page}_{pmin}_{pmax}"
    cached = _search_cache.get(cache_key)
    if cached and (_time.time() - cached["ts"]) < _SEARCH_CACHE_TTL:
        items = cached["items"]
        api_error = None
    else:
        await cb.message.edit_text("⏳ Загружаю аккаунты...")
        try:
            items, api_error = await lzt.search_telegram(pmin=pmin, pmax=pmax, page=page, extra_params=filter_params)
        except Exception:
            return await cb.message.edit_text(
                "❌ Ошибка загрузки. Попробуйте ещё раз.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать ещё раз", callback_data="shop_all")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                ])
            )
        if not api_error and items:
            _search_cache[cache_key] = {"ts": _time.time(), "items": items}

    if api_error:
        return await cb.message.edit_text(
            "❌ Ошибка загрузки. Попробуйте ещё раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать ещё раз", callback_data="shop_all")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
            ])
        )

    if not items:
        return await cb.message.edit_text(
            "😔 Аккаунтов не найдено. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
            ])
        )

    countries: dict[str, list] = {}
    for item in items:
        country_name = extract_country_name(item)
        countries.setdefault(country_name, []).append(item)

    _shop_cache[uid] = {"by_country": countries, "markup": markup_val, "back_cb": back_cb}

    bild = InlineKeyboardBuilder()
    for country, c_items in sorted(countries.items(), key=lambda x: -len(x[1])):
        prices = [get_item_price_usd(it, markup_val)[1] for it in c_items]
        min_price = min(prices) if prices else 0
        idx = list(countries.keys()).index(country)
        bild.button(
            text=f"🌍 {country} ({len(c_items)}) от ${min_price:.2f}",
            callback_data=f"lzt_country-{idx}"
        )

    nav = []
    page_prefix = "shop_all" if back_cb == "shop" else f"shop-"
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"shop_all-{page-1}"))
    nav.append(InlineKeyboardButton(text=f"📄 {page}", callback_data="e"))
    if len(items) >= LZT_PAGE_SIZE:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"shop_all-{page+1}"))
    if nav:
        bild.row(*nav)
    bild.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb),
             InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))
    bild.adjust(1)

    title = f"🌍 <b>{section_name}</b>" if section_name else "🌍 <b>Выберите страну</b>"
    await cb.message.edit_text(
        f"{title}\n💰 Цены в $ с наценкой +{(markup_val-1)*100:.0f}%",
        reply_markup=bild.as_markup()
    )


# ==================== Уніфіковане меню оплати ====================

def _payment_kbd(back_cb: str = "menu", ton_amount: float = None) -> InlineKeyboardMarkup:
    """Повертає клавіатуру вибору способу поповнення/оплати."""
    bild = InlineKeyboardBuilder()
    bild.button(text="💎 CryptoBot", callback_data=f"popol-{ton_amount}" if ton_amount else "pay_method_cryptobot")
    if TON_ADDRESS:
        ton_cb = f"pay_ton-{ton_amount}" if ton_amount else "popolnit"
        bild.button(text="💎 Tonkeeper", callback_data=ton_cb)
    if db.get_setting("card_payment_text", ""):
        bild.button(text="💳 Оплата на карту", callback_data="pay_card")
    bild.button(text="⬅️ Назад", callback_data=back_cb)
    bild.adjust(1)
    return bild.as_markup()

# ==================== Прямий показ аккаунтів України ====================

async def _do_section_direct(cb: types.CallbackQuery, uid: int, section, page: int = 1):
    """Загружает аккаунты раздела и показывает напрямую без группировки по странам."""
    lzt = get_lzt_client()
    markup_val = float(db.get_setting("lzt_markup", str(LZT_MARKUP)))

    if not lzt:
        # Локальный магазин — показываем все без фильтра по разделу
        await cb.message.edit_text("⏳ Загружаю аккаунты...")
        all_items = list(db.AccountsShop.select())
        if not all_items:
            return await cb.message.edit_text(
                "😔 Аккаунтов пока нет.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="shop")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                ])
            )
        bild = InlineKeyboardBuilder()
        for item in sorted(all_items, key=lambda x: x.Price)[:16]:
            bild.button(text=f"#{item.ShopID} ${item.Price:.2f}", callback_data=f"local_item-{item.ShopID}")
        bild.adjust(2)
        bild.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop"),
                 InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))
        return await cb.message.edit_text(
            f"📱 <b>{section.name}</b> — {len(all_items)} шт.",
            reply_markup=bild.as_markup()
        )

    filter_params = parse_lzt_filter_url(section.filter_url) if section.filter_url else {}
    pmin = float(filter_params.get("pmin", db.get_setting("lzt_pmin", str(LZT_MIN_PRICE))))
    pmax = float(filter_params.get("pmax", db.get_setting("lzt_pmax", str(LZT_MAX_PRICE))))

    import time as _time
    cache_key = f"sec_{section.id}_{page}_{pmin}_{pmax}"
    cached = _search_cache.get(cache_key)
    if cached and (_time.time() - cached["ts"]) < _SEARCH_CACHE_TTL:
        items = cached["items"]
        api_error = None
    else:
        await cb.message.edit_text("⏳ Загружаю аккаунты...")
        try:
            items, api_error = await lzt.search_telegram(pmin=pmin, pmax=pmax, page=page, extra_params=filter_params)
        except Exception:
            return await cb.message.edit_text(
                "❌ Ошибка загрузки. Попробуйте ещё раз.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Повторить", callback_data=f"shop_section-{section.id}")],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="shop")]
                ])
            )
        if not api_error and items:
            _search_cache[cache_key] = {"ts": _time.time(), "items": items}

    if api_error or not items:
        return await cb.message.edit_text(
            "❌ Ошибка загрузки." if api_error else "😔 Аккаунтов не найдено. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="shop")]
            ])
        )

    _shop_cache[uid] = {"by_country": {section.name: items}, "markup": markup_val, "back_cb": "shop"}

    display = sorted(items, key=lambda x: float(x.get("price", 0)))[:16]
    bild = InlineKeyboardBuilder()
    for item in display:
        item_id = item.get("item_id") or item.get("id")
        lzt_price_raw = float(item.get("price", 0))
        currency = item.get("currency", "RUB")
        _, sell_usd = get_item_price_usd(item, markup_val)
        title = (item.get("title") or f"#{item_id}")[:14]
        bild.button(
            text=f"{title} ${sell_usd:.2f}",
            callback_data=f"lzt_item-{item_id}-{lzt_price_raw}-{currency}"
        )
    bild.adjust(2)

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"sec_page-{section.id}-{page-1}"))
    nav.append(InlineKeyboardButton(text=f"📄 {page}", callback_data="e"))
    if len(items) >= LZT_PAGE_SIZE:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"sec_page-{section.id}-{page+1}"))
    if nav:
        bild.row(*nav)
    bild.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop"),
             InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))

    await cb.message.edit_text(
        f"🛒 <b>Выберите аккаунт</b>",
        reply_markup=bild.as_markup()
    )


async def _do_ukraine_shop(cb: types.CallbackQuery, uid: int, page: int = 1):
    """Завантажує акаунти та одразу показує список України (6 в ряд, 12 шт.)."""
    lzt = get_lzt_client()
    markup_val = float(db.get_setting("lzt_markup", str(LZT_MARKUP)))

    # ---- Локальний магазин (LZT не налаштовано) ----
    if not lzt:
        await cb.message.edit_text("⏳ Загружаю аккаунты...")
        all_items = list(db.AccountsShop.select())
        ua_items = [
            it for it in all_items
            if "украин" in accounts.get_country_info(it.AccountNumber)[1].lower()
            or accounts.get_country_info(it.AccountNumber)[0] == "🇺🇦"
        ] or all_items  # fallback: все если Украина не найдена

        PAGE_SIZE = 16
        start = (page - 1) * PAGE_SIZE
        page_items = sorted(ua_items, key=lambda x: x.Price)[start:start + PAGE_SIZE]

        bild = InlineKeyboardBuilder()
        for item in page_items:
            bild.button(
                text=f"#{item.ShopID} ${item.Price:.2f}",
                callback_data=f"local_item-{item.ShopID}"
            )
        bild.adjust(2)
        bild.row(InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))
        return await cb.message.edit_text(
            f"🇺🇦 <b>Аккаунты Украина</b> — {len(ua_items)} шт.",
            reply_markup=bild.as_markup()
        )

    # ---- LZT: шукаємо фільтр України (якщо є розділ) або глобальний ----
    ukraine_filter = ""
    for s in db.ShopSection.select():
        if "украин" in s.name.lower() or "🇺🇦" in s.name:
            ukraine_filter = s.filter_url
            break
    filter_url = ukraine_filter or db.get_setting("lzt_filter", "")
    filter_params = parse_lzt_filter_url(filter_url) if filter_url else {}
    pmin = float(filter_params.get("pmin", db.get_setting("lzt_pmin", str(LZT_MIN_PRICE))))
    pmax = float(filter_params.get("pmax", db.get_setting("lzt_pmax", str(LZT_MAX_PRICE))))

    import time as _time
    cache_key = f"ua_direct_{page}_{pmin}_{pmax}"
    cached = _search_cache.get(cache_key)
    if cached and (_time.time() - cached["ts"]) < _SEARCH_CACHE_TTL:
        items = cached["items"]
        api_error = None
    else:
        await cb.message.edit_text("⏳ Загружаю аккаунты...")
        try:
            items, api_error = await lzt.search_telegram(pmin=pmin, pmax=pmax, page=page, extra_params=filter_params)
        except Exception:
            return await cb.message.edit_text(
                "❌ Ошибка загрузки. Попробуйте позже.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Повторить", callback_data="shop")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                ])
            )
        if not api_error and items:
            _search_cache[cache_key] = {"ts": _time.time(), "items": items}

    if api_error or not items:
        return await cb.message.edit_text(
            "❌ Ошибка загрузки." if api_error else "😔 Аккаунтов не найдено. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
            ])
        )

    # Фільтруємо Україну (якщо немає спеціального фільтру розділу)
    ua_items = ([it for it in items if _is_ukraine(it)] or items) if not ukraine_filter else items

    # Зберігаємо в кеш для покупки
    _shop_cache[uid] = {"by_country": {"🇺🇦 Украина": ua_items}, "markup": markup_val, "back_cb": "shop"}

    # Показываем первые 16, по 2 в ряд
    display = sorted(ua_items, key=lambda x: float(x.get("price", 0)))[:16]
    bild = InlineKeyboardBuilder()
    for item in display:
        item_id = item.get("item_id") or item.get("id")
        lzt_price_raw = float(item.get("price", 0))
        currency = item.get("currency", "RUB")
        _, sell_usd = get_item_price_usd(item, markup_val)
        title = (item.get("title") or f"#{item_id}")[:14]
        bild.button(
            text=f"{title} ${sell_usd:.2f}",
            callback_data=f"lzt_item-{item_id}-{lzt_price_raw}-{currency}"
        )
    bild.adjust(2)
    bild.row(InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))
    await cb.message.edit_text(
        f"🇺🇦 <b>Аккаунты Украина</b>\n💰 Цены в $ с наценкой +{(markup_val - 1) * 100:.0f}%",
        reply_markup=bild.as_markup()
    )


# ==================== Callback handlers ====================

@rt.callback_query(F.data)
async def handle_callbacks(cb: types.CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    command = cb.data

    # Проверка подписки: пропускаем check_sub и владельцев
    if command != "check_sub" and uid not in OWNERS:
        if not await _check_subscription(cb.bot, uid):
            channel = db.get_setting("required_channel", "")
            text, kbd = _sub_wall(channel)
            try:
                await cb.message.edit_text(text, reply_markup=kbd)
            except:
                await cb.answer("Подпишитесь на канал!", show_alert=True)
            return

    # ==================== ГОЛОВНЕ МЕНЮ ====================

    if command == "menu":
        bild = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Купить аккаунт", callback_data="shop")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
             InlineKeyboardButton(text="💬 Поддержка", url=db.get_setting("support_url", "t.me/sierafimv"))]
        ])
        await cb.message.edit_text(
            "👋 <b>Главное меню</b>\n\n"
            "Выберите действие 👇",
            reply_markup=bild
        )

    # ==================== МАГАЗИН: ВИБІР КРАЇНИ ====================

    elif command == "shop":
        # Показываем разделы (страны), если они есть — иначе все аккаунты
        sections = list(db.ShopSection.select().order_by(db.ShopSection.order, db.ShopSection.id))
        if sections:
            bild = InlineKeyboardBuilder()
            for s in sections:
                bild.button(text=s.name, callback_data=f"shop_section-{s.id}")
            bild.adjust(2)
            bild.row(InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))
            return await cb.message.edit_text(
                "🛒 <b>Магазин аккаунтов</b>\n\n"
                "Выберите нужную страну 👇",
                reply_markup=bild.as_markup()
            )
        # Нет разделов — показываем все аккаунты
        return await _do_ukraine_shop(cb, uid, page=1)

    elif command == "shop_all" or command.startswith("shop-") or command.startswith("shop_all-"):
        page = int(command.split("-")[1]) if "-" in command else 1
        return await _do_shop_search(cb, uid, page=page)

    elif command.startswith("ua_shop-"):
        page = int(command.split("-")[1])
        return await _do_ukraine_shop(cb, uid, page=page)

    elif command.startswith("shop_section-"):
        section_id = int(command.split("-")[1])
        section = db.ShopSection.get_or_none(id=section_id)
        if not section:
            return await cb.answer("❌ Раздел не найден", show_alert=True)
        return await _do_section_direct(cb, uid, section, page=1)

    elif command.startswith("sec_page-"):
        parts = command.split("-")
        section_id, page = int(parts[1]), int(parts[2])
        section = db.ShopSection.get_or_none(id=section_id)
        if not section:
            return await cb.answer("❌ Раздел не найден", show_alert=True)
        return await _do_section_direct(cb, uid, section, page=page)


    # ==================== МАГАЗИН: АКАУНТИ КРАЇНИ ====================

    elif command.startswith("lzt_country-"):
        idx = int(command.split("-")[1])

        # Беремо з кешу — без повторного запиту
        cache = _shop_cache.get(uid)
        if not cache:
            return await cb.answer("⏳ Кеш устарел. Вернитесь в магазин.", show_alert=True)

        countries = cache["by_country"]
        markup_val = cache["markup"]
        country_keys = list(countries.keys())

        if idx >= len(country_keys):
            return await cb.answer("❌ Ошибка. Вернитесь в магазин.", show_alert=True)

        country_name = country_keys[idx]
        filtered = countries[country_name]

        bild = InlineKeyboardBuilder()
        for item in sorted(filtered, key=lambda x: float(x.get("price", 0)))[:16]:
            item_id = item.get("item_id") or item.get("id")
            lzt_price_raw = float(item.get("price", 0))
            currency = item.get("currency", "RUB")
            _, sell_usd = get_item_price_usd(item, markup_val)
            title = (item.get("title") or f"#{item_id}")[:14]
            bild.button(
                text=f"{title} ${sell_usd:.2f}",
                callback_data=f"lzt_item-{item_id}-{lzt_price_raw}-{currency}"
            )

        back = cache.get("back_cb", "shop")
        bild.adjust(2)
        bild.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back))

        await cb.message.edit_text(
            f"📱 <b>Аккаунты — {country_name}</b>",
            reply_markup=bild.as_markup()
        )

    # ==================== ЛОКАЛЬНИЙ МАГАЗИН: АКАУНТИ КРАЇНИ ====================

    elif command.startswith("local_country-"):
        idx = int(command.split("-")[1])
        cache = _local_cache.get(uid)
        if not cache:
            return await cb.answer("⏳ Кеш устарел. Вернитесь в магазин.", show_alert=True)
        country_keys = list(cache.keys())
        if idx >= len(country_keys):
            return await cb.answer("❌ Ошибка. Вернитесь в магазин.", show_alert=True)
        country_name = country_keys[idx]
        c_items = sorted(cache[country_name], key=lambda x: x.Price)
        bild = InlineKeyboardBuilder()
        for item in c_items[:16]:
            bild.button(
                text=f"#{item.ShopID} ${item.Price:.2f}",
                callback_data=f"local_item-{item.ShopID}"
            )
        bild.adjust(2)
        bild.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="shop"))
        await cb.message.edit_text(
            f"📱 <b>Аккаунты — {country_name}</b>",
            reply_markup=bild.as_markup()
        )

    # ==================== ЛОКАЛЬНИЙ МАГАЗИН: ДЕТАЛІ АКАУНТУ ====================

    elif command.startswith("local_item-"):
        shop_id = int(command.split("-")[1])
        item = db.AccountsShop.get_or_none(ShopID=shop_id)
        if not item:
            return await cb.answer("❌ Аккаунт уже продан или не существует.", show_alert=True)
        flag, country_name = accounts.get_country_info(item.AccountNumber)
        user = db.User.get_or_none(id=uid)
        balance = user.balance if user else 0
        text = (
            f"📱 <b>Аккаунт #{item.ShopID}</b>\n\n"
            f"🌍 Страна: <code>{flag} {country_name}</code>\n"
            f"💰 Цена: <code>${item.Price:.2f}</code>\n"
        )
        if item.description:
            text += f"📝 <i>{item.description}</i>\n"
        text += f"\n💳 Ваш баланс: <code>${balance:.2f}</code>"

        # Знайти country idx для кнопки назад
        cache = _local_cache.get(uid, {})
        country_display = f"{flag} {country_name}"
        back_idx = list(cache.keys()).index(country_display) if country_display in cache else 0

        bild = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🛒 Купить — ${item.Price:.2f}", callback_data=f"local_buy-{shop_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"local_country-{back_idx}"),
             InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
        ])
        await cb.message.edit_text(text, reply_markup=bild)

    # ==================== ЛОКАЛЬНИЙ МАГАЗИН: КУПІВЛЯ ====================

    elif command.startswith("local_buy-"):
        shop_id = int(command.split("-")[1])
        item = db.AccountsShop.get_or_none(ShopID=shop_id)
        if not item:
            return await cb.answer("❌ Аккаунт уже продан или не существует.", show_alert=True)
        user = db.User.get_or_none(id=uid)
        if not user or user.balance < item.Price:
            need = round(item.Price - (user.balance if user else 0), 2)
            balance_now = user.balance if user else 0
            return await cb.message.edit_text(
                f"💸 <b>Недостаточно средств</b>\n\n"
                f"💰 Нужно: <code>${item.Price:.2f}</code>\n"
                f"💳 Ваш баланс: <code>${balance_now:.2f}</code>\n"
                f"📉 Не хватает: <code>${need:.2f}</code>\n\n"
                "Выберите способ пополнения:",
                reply_markup=_payment_kbd(back_cb="shop", ton_amount=need)
            )
        # Списуємо баланс і передаємо акаунт
        user.balance -= item.Price
        user.save()
        db.Accounts.create(
            id=uid,
            AccountID=item.AccountID,
            AccountNumber=item.AccountNumber,
            AuthKey=item.AuthKey
        )
        item.delete_instance()
        # Очищаємо кеш щоб наступного разу дані були актуальні
        _local_cache.pop(uid, None)
        await cb.message.edit_text(
            f"✅ <b>Аккаунт куплен!</b>\n\n"
            f"📱 Номер: <code>+{item.AccountNumber}</code>\n"
            f"🔑 Session: <code>{item.AuthKey}</code>\n\n"
            f"💳 Остаток: <code>${user.balance:.2f}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
            ])
        )

    # ==================== МАГАЗИН: ДЕТАЛІ АКАУНТУ ====================

    elif command.startswith("lzt_item-"):
        parts = command.split("-")
        item_id = int(parts[1])
        lzt_price_raw = float(parts[2])
        currency = parts[3] if len(parts) > 3 else "RUB"

        lzt = get_lzt_client()
        if not lzt:
            return await cb.answer("❌ LZT не настроен", show_alert=True)

        markup_val = float(db.get_setting("lzt_markup", str(LZT_MARKUP)))
        lzt_usd = convert_to_usd(lzt_price_raw, currency)
        sell_usd = round(lzt_usd * markup_val, 2)

        try:
            item = await lzt.get_item(item_id)
        except Exception as e:
            return await cb.answer(f"❌ {e}", show_alert=True)

        title = (item.get("title") or f"#{item_id}")[:60]
        desc = (item.get("description") or "")[:400]
        country_raw = item.get("country_title") or item.get("country") or "—"
        if isinstance(country_raw, dict):
            country_raw = country_raw.get("title", "—")

        user = db.User.get_or_none(id=uid)
        balance = user.balance if user else 0
        confirm_mode = db.get_setting("lzt_confirm", "1")
        buy_suffix = " (требует подтверждения)" if confirm_mode == "1" else ""

        text = (
            f"📱 <b>{title}</b>\n\n"
            f"🌍 Страна: <code>{country_raw}</code>\n"
            f"💰 Цена: <code>${sell_usd:.2f}</code>{buy_suffix}\n"
        )
        if desc:
            text += f"📝 <i>{desc}</i>\n"
        text += f"\n💳 Ваш баланс: <code>${balance:.2f}</code>"

        bild = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"🛒 Купить — ${sell_usd:.2f}",
                callback_data=f"lzt_buy-{item_id}-{lzt_price_raw}-{currency}"
            )],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="shop"),
             InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
        ])
        await cb.message.edit_text(text, reply_markup=bild)

    # ==================== КУПІВЛЯ ====================

    elif command.startswith("lzt_buy-"):
        parts = command.split("-")
        item_id = int(parts[1])
        lzt_price_raw = float(parts[2])
        currency = parts[3] if len(parts) > 3 else "RUB"

        lzt = get_lzt_client()
        if not lzt:
            return await cb.answer("❌ LZT не настроен", show_alert=True)

        markup_val = float(db.get_setting("lzt_markup", str(LZT_MARKUP)))
        lzt_usd = convert_to_usd(lzt_price_raw, currency)
        sell_usd = round(lzt_usd * markup_val, 2)
        confirm_mode = db.get_setting("lzt_confirm", "1")

        user = db.User.get_or_none(id=uid)
        if not user:
            return await cb.answer("❌ Ошибка")

        if user.balance < sell_usd:
            need = round(sell_usd - user.balance, 2)
            return await cb.message.edit_text(
                f"💸 <b>Недостаточно средств</b>\n\n"
                f"💰 Нужно: <code>${sell_usd:.2f}</code>\n"
                f"💳 Ваш баланс: <code>${user.balance:.2f}</code>\n"
                f"📉 Не хватает: <code>${need:.2f}</code>\n\n"
                "Выберите способ пополнения:",
                reply_markup=_payment_kbd(back_cb="shop", ton_amount=need)
            )

        user.balance -= sell_usd
        user.save()

        if confirm_mode == "1":
            item_title = f"#{item_id}"
            try:
                item_info = await lzt.get_item(item_id)
                item_title = (item_info.get("title") or f"#{item_id}")[:60]
            except:
                pass

            pending = db.PendingPurchase.create(
                buyer_id=uid,
                buyer_username=cb.from_user.username or str(uid),
                lzt_item_id=item_id,
                lzt_price=lzt_price_raw,    # сира ціна для fast_buy
                lzt_currency=currency,
                sell_price=sell_usd,
                item_title=item_title,
                status="pending",
                created_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )

            admin_text = (
                f"🔔 <b>Запрос на покупку</b>\n\n"
                f"👤 @{cb.from_user.username or '—'} (<code>{uid}</code>)\n"
                f"📦 <code>{item_title}</code>\n"
                f"🆔 LZT: <code>{item_id}</code>\n"
                f"💵 Закупка: <code>{lzt_price_raw} {currency}</code> (≈${lzt_usd:.2f})\n"
                f"💰 Продажа: <code>${sell_usd:.2f}</code>\n"
                f"💎 Прибыль: <code>${sell_usd - lzt_usd:.2f}</code>"
            )
            admin_kbd = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Купить", callback_data=f"lzt_confirm-{pending.id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"lzt_reject-{pending.id}")
            ]])

            for owner_id in OWNERS:
                try:
                    await cb.bot.send_message(owner_id, admin_text, reply_markup=admin_kbd)
                except:
                    pass

            asyncio.create_task(auto_buy_timeout(cb.bot, pending.id))

            await cb.message.edit_text(
                f"⏳ <b>Запрос отправлен администратору!</b>\n\n"
                f"📦 <code>{item_title}</code>\n"
                f"💰 Зарезервировано: <code>${sell_usd:.2f}</code>\n\n"
                f"Ожидайте — мы уведомим вас.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                ])
            )
        else:
            try:
                await cb.message.edit_text("⏳ Покупаю аккаунт...")
                await execute_lzt_purchase(cb.bot, cb.message, uid, item_id, lzt_price_raw, sell_usd, lzt)
            except Exception:
                user.balance += sell_usd
                user.save()
                try:
                    await cb.bot.send_message(uid, f"❌ Помилка покупки. ${sell_usd:.2f} повернено на баланс.")
                except Exception:
                    pass

    # ==================== ПІДТВЕРДЖЕННЯ АДМІНОМ ====================

    elif command.startswith("lzt_confirm-"):
        if uid not in OWNERS:
            return await cb.answer("❌ Нет доступа", show_alert=True)

        pending_id = int(command.split("-")[1])
        pending = db.PendingPurchase.get_or_none(id=pending_id, status="pending")
        if not pending:
            return await cb.answer("❌ Уже обработано", show_alert=True)

        lzt = get_lzt_client()
        if not lzt:
            return await cb.answer("❌ LZT Token не настроен!", show_alert=True)

        await cb.message.edit_text(f"⏳ Обрабатываю...\n📦 {pending.item_title}")

        try:
            success, item_data = await lzt.fast_buy(pending.lzt_item_id, pending.lzt_price)
        except Exception as e:
            user = db.User.get_or_none(id=pending.buyer_id)
            if user:
                user.balance += pending.sell_price
                user.save()
            pending.status = "failed"
            pending.save()
            await cb.message.edit_text(f"❌ Ошибка: <code>{e}</code>\nСредства возвращены.")
            try:
                await cb.bot.send_message(pending.buyer_id, f"❌ Не удалось оформить покупку. ${pending.sell_price:.2f} возвращено на баланс.")
            except:
                pass
            return

        if not success:
            user = db.User.get_or_none(id=pending.buyer_id)
            if user:
                user.balance += pending.sell_price
                user.save()
            pending.status = "failed"
            pending.save()
            await cb.message.edit_text(f"❌ Отклонено: <code>{item_data.get('error', '?')}</code>\nСредства возвращены.")
            try:
                await cb.bot.send_message(pending.buyer_id, f"❌ Не удалось оформить покупку. ${pending.sell_price:.2f} возвращено на баланс.")
            except:
                pass
            return

        pending.status = "approved"
        pending.save()

        txn = db.LztTransaction.create(
            buyer_id=pending.buyer_id,
            lzt_item_id=pending.lzt_item_id,
            lzt_price=pending.lzt_price,
            sell_price=pending.sell_price,
            account_data=json.dumps(item_data, ensure_ascii=False),
            purchased_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        account_text, session_str = await _get_account_text(lzt, item_data)
        if session_str:
            try:
                ad = json.loads(txn.account_data)
                ad["_session"] = session_str
                txn.account_data = json.dumps(ad, ensure_ascii=False)
                txn.save()
            except:
                pass
        btn_rows = []
        if session_str:
            btn_rows.append([InlineKeyboardButton(text="📲 Отримати код", callback_data=f"get_code-{txn.id}")])
        btn_rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="menu")])
        buyer_kbd = InlineKeyboardMarkup(inline_keyboard=btn_rows)
        buyer_notified = False
        try:
            await cb.bot.send_message(pending.buyer_id, f"✅ <b>Аккаунт куплен!</b>\n\n{account_text}",
                                      reply_markup=buyer_kbd)
            buyer_notified = True
        except Exception as send_err:
            pass

        notify_status = "" if buyer_notified else "\n⚠️ Не удалось отправить покупателю — проверьте вручную"
        try:
            await cb.message.edit_text(
                f"✅ Успешно!\n👤 <code>{pending.buyer_id}</code>\n"
                f"💵 Продажа: <code>${pending.sell_price:.2f}</code>"
                f"{notify_status}"
            )
        except:
            await cb.answer("✅ Куплено", show_alert=True)
        # Реферальный бонус
        await _pay_referral(cb.bot, pending.buyer_id, pending.sell_price)
        await _send_review_request(cb.bot, pending.buyer_id)

    elif command.startswith("lzt_reject-"):
        if uid not in OWNERS:
            return await cb.answer("❌ Нет доступа", show_alert=True)

        pending_id = int(command.split("-")[1])
        pending = db.PendingPurchase.get_or_none(id=pending_id, status="pending")
        if not pending:
            return await cb.answer("❌ Уже обработано", show_alert=True)

        user = db.User.get_or_none(id=pending.buyer_id)
        if user:
            user.balance += pending.sell_price
            user.save()
        pending.status = "rejected"
        pending.save()

        try:
            await cb.bot.send_message(
                pending.buyer_id,
                f"❌ Запрос отклонён.\n💸 <code>${pending.sell_price:.2f}</code> возвращено на баланс."
            )
        except:
            pass
        await cb.message.edit_text(f"❌ Отклонено #{pending_id}. Средства возвращены <code>{pending.buyer_id}</code>.")

    # ==================== МЕНЮ ОПЛАТИ ====================

    elif command.startswith("pay_ton-"):
        amount = command.split("-")[1]
        if not TON_ADDRESS:
            return await cb.answer("❌ TON адрес не настроен. Обратитесь к администратору.", show_alert=True)
        comment = f"topup_{uid}"
        await cb.message.edit_text(
            "💎 <b>Оплата через Tonkeeper</b>\n\n"
            f"📍 Адрес: <code>{TON_ADDRESS}</code>\n"
            f"💰 Сумма: <code>{amount}</code>\n"
            f"📝 Комментарий: <code>{comment}</code>\n\n"
            "<i>Скопируйте адрес и отправьте указанную сумму. "
            "После оплаты обратитесь к администратору для зачисления баланса.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="popolnit"),
                 InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
            ])
        )

    elif command == "pay_card":
        card_text = db.get_setting("card_payment_text", "")
        if not card_text:
            return await cb.answer("❌ Оплата на карту временно недоступна", show_alert=True)
        await cb.message.edit_text(
            f"💳 <b>Оплата на карту</b>\n\n{card_text}\n\n"
            "После пополнения обратитесь к администратору для зачисления баланса.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ В магазин", callback_data="shop")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
            ])
        )

    # ==================== АДМІН ПАНЕЛЬ ====================

    elif command == "admin":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        await state.clear()
        text, kbd = await build_admin_panel_text(cb.bot)
        await cb.message.edit_text(text, reply_markup=kbd)

    elif command == "adm_lzt_bal":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        lzt = get_lzt_client()
        if not lzt:
            return await cb.answer("❌ LZT Token не настроен", show_alert=True)
        try:
            bal, currency = await lzt.get_balance()
            await cb.answer(f"💰 Баланс LZT: {bal:.2f}{currency}", show_alert=True)
        except Exception as e:
            await cb.answer(f"❌ {e}", show_alert=True)

    elif command == "adm_stats":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        total_users = db.User.select().count()
        total_referrals = db.User.select().where(db.User.referred_by > 0).count()
        total_lzt = db.LztTransaction.select().count()
        total_local = db.Accounts.select().count()
        total_revenue = sum(t.sell_price for t in db.LztTransaction.select())
        total_user_balance = sum(u.balance for u in db.User.select())
        pending_count = db.PendingPurchase.select().where(db.PendingPurchase.status == "pending").count()
        failed_count = db.PendingPurchase.select().where(db.PendingPurchase.status == "failed").count()
        week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        week_sales = db.LztTransaction.select().where(db.LztTransaction.purchased_at >= week_ago).count()
        week_users = db.User.select().where(db.User.registered_at >= week_ago).count()
        week_revenue = sum(
            t.sell_price for t in db.LztTransaction.select()
            if t.purchased_at >= week_ago
        )
        top_ref = (db.User.select(db.User.referred_by, db.fn.COUNT(db.User.id).alias("cnt"))
                   .where(db.User.referred_by > 0)
                   .group_by(db.User.referred_by)
                   .order_by(db.fn.COUNT(db.User.id).desc())
                   .limit(1))
        top_ref_str = ""
        for r in top_ref:
            top_ref_str = f"\n  • Топ реферер: <code>{r.referred_by}</code> ({r.cnt} рефералов)"
        markup_val = float(db.get_setting("lzt_markup", str(LZT_MARKUP)))
        total_net = sum(
            t.sell_price - (t.sell_price / markup_val if markup_val > 0 else 0)
            for t in db.LztTransaction.select()
        )
        week_net = sum(
            t.sell_price - (t.sell_price / markup_val if markup_val > 0 else 0)
            for t in db.LztTransaction.select()
            if t.purchased_at >= week_ago
        )
        text = (
            "📊 <b>Полная статистика</b>\n\n"
            "👥 <b>Пользователи:</b>\n"
            f"  • Всего: <code>{total_users}</code>\n"
            f"  • За 7 дней: <code>+{week_users}</code>\n"
            f"  • По рефералам: <code>{total_referrals}</code>{top_ref_str}\n\n"
            "🛒 <b>Продажи:</b>\n"
            f"  • LZT Market: <code>{total_lzt}</code>\n"
            f"  • Локальные: <code>{total_local}</code>\n"
            f"  • За 7 дней: <code>{week_sales}</code>\n"
            f"  • В ожидании: <code>{pending_count}</code>\n"
            f"  • Неудачных: <code>{failed_count}</code>\n\n"
            "💰 <b>Финансы:</b>\n"
            f"  • Доход всего: <code>${total_revenue:.2f}</code>\n"
            f"  • Доход за 7 дней: <code>${week_revenue:.2f}</code>\n"
            f"  • 💚 Чистый доход: <code>${total_net:.2f}</code>\n"
            f"  • 💚 Чистый за 7 дн: <code>${week_net:.2f}</code>\n"
            f"  • Баланс пользователей: <code>${total_user_balance:.2f}</code>"
        )
        bild = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
        ])
        await cb.message.edit_text(text, reply_markup=bild)

    elif command == "adm_set_markup":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("lzt_markup", str(LZT_MARKUP))
        await cb.message.edit_text(
            f"⚙️ <b>Множитель наценки</b>\nТекущий: <code>{current}x</code>\n\nВведите новый (1.5 = +50%):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.markup)

    elif command == "adm_set_prices":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        await cb.message.edit_text(
            f"💲 <b>Цены закупки (в $)</b>\nТекущие: <code>${db.get_setting('lzt_pmin', str(LZT_MIN_PRICE))} — ${db.get_setting('lzt_pmax', str(LZT_MAX_PRICE))}</code>\n\nВведите: <code>мин макс</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.price_range)

    elif command == "adm_set_rub_rate":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("rate_rub_usd", "0.011")
        await cb.message.edit_text(
            f"💱 <b>Курс RUB → USD</b>\nТекущий: <code>{current}</code> (1000₽ = ${float(current)*1000:.2f})\n\nВведите новый курс:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.rub_rate)

    elif command == "adm_set_eur_rate":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("rate_eur_usd", "1.08")
        await cb.message.edit_text(
            f"💱 <b>Курс EUR → USD</b>\nТекущий: <code>{current}</code>\n\nВведите новый курс:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.eur_rate)

    elif command == "adm_set_filter":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("lzt_filter", "")
        await cb.message.edit_text(
            f"🔗 <b>Фильтр URL</b>\n{'<code>' + current[:100] + '</code>' if current else 'Не установлен'}\n\nОтправьте URL с lzt.market.\nЧтобы очистить: <code>-</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.filter_url)

    elif command == "adm_set_token":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        await cb.message.edit_text(
            "🔑 <b>LZT Token</b>\n\nВведите токен:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.lzt_token)

    elif command == "adm_toggle_confirm":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("lzt_confirm", "1")
        db.set_setting("lzt_confirm", "0" if current == "1" else "1")
        new = "🔴 ручное" if current == "0" else "🟢 авто"
        await cb.answer(f"Режим: {new}", show_alert=True)
        text, kbd = await build_admin_panel_text(cb.bot)
        await cb.message.edit_text(text, reply_markup=kbd)

    elif command == "adm_pending":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        pending_list = list(
            db.PendingPurchase.select()
            .where(db.PendingPurchase.status == "pending")
            .order_by(db.PendingPurchase.id.desc())
            .limit(10)
        )
        if not pending_list:
            return await cb.answer("✅ Нет запросов в ожидании", show_alert=True)
        bild = InlineKeyboardBuilder()
        for p in pending_list:
            bild.row(InlineKeyboardButton(text=f"#{p.id} @{p.buyer_username} — ${p.sell_price:.2f}", callback_data="e"))
            bild.row(
                InlineKeyboardButton(text="✅", callback_data=f"lzt_confirm-{p.id}"),
                InlineKeyboardButton(text="❌", callback_data=f"lzt_reject-{p.id}")
            )
        bild.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin"))
        await cb.message.edit_text(f"⏳ <b>Ожидают ({len(pending_list)})</b>:", reply_markup=bild.as_markup())

    elif command == "adm_broadcast":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        await cb.message.edit_text(
            "📢 <b>Рассылка</b>\n\nОтправьте текст или <b>фото с подписью</b>:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.broadcast)

    elif command == "adm_set_ref_percent":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("referral_percent", "5")
        await cb.message.edit_text(
            f"👥 <b>Реферальный процент</b>\nТекущий: <code>{current}%</code>\n\nВведите новый % (0 = отключить):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.referral_percent)

    elif command == "adm_user_balance":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        await cb.message.edit_text(
            "👤 <b>Пополнение баланса</b>\n\nФормат: <code>ID сумма</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.add_balance)

    elif command == "adm_set_threshold":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("balance_alert_threshold", "500")
        await cb.message.edit_text(
            f"🔔 <b>Порог баланса</b>\nТекущий: <code>{current}₽</code>\n\nВведите новый порог:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.balance_threshold)

    # ==================== АДМІН: РОЗДІЛИ МАГАЗИНУ ====================

    elif command == "adm_sections":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        sections = list(db.ShopSection.select().order_by(db.ShopSection.order, db.ShopSection.id))
        bild = InlineKeyboardBuilder()
        for s in sections:
            bild.button(text=f"✏️ {s.name}", callback_data=f"adm_sec_edit-{s.id}")
        bild.button(text="➕ Добавить раздел", callback_data="adm_sec_add")
        bild.adjust(1)
        bild.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin"))
        text = "📂 <b>Разделы магазина</b>\n\n"
        if not sections:
            text += "<i>Разделов пока нет.</i>"
        else:
            for s in sections:
                has_filter = "✅" if s.filter_url else "❌"
                text += f"• <b>{s.name}</b> — фильтр: {has_filter}\n"
        await cb.message.edit_text(text, reply_markup=bild.as_markup())

    elif command == "adm_sec_add":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        await cb.message.edit_text(
            "📂 <b>Новый раздел</b>\n\nВведите название кнопки:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="adm_sections")]
            ])
        )
        await state.set_state(AdminInput.section_name)

    elif command.startswith("adm_sec_edit-"):
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        sec_id = int(command.split("-")[1])
        section = db.ShopSection.get_or_none(id=sec_id)
        if not section:
            return await cb.answer("❌ Раздел не найден", show_alert=True)
        has_filter = section.filter_url[:60] + "..." if len(section.filter_url) > 60 else (section.filter_url or "нет")
        bild = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"adm_sec_rename-{sec_id}"),
             InlineKeyboardButton(text="🔗 Изменить фильтр", callback_data=f"adm_sec_setfilter-{sec_id}")],
            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"adm_sec_del-{sec_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_sections")]
        ])
        await cb.message.edit_text(
            f"✏️ <b>Раздел: {section.name}</b>\n\n🔗 Фильтр: <code>{has_filter}</code>",
            reply_markup=bild
        )

    elif command.startswith("adm_sec_rename-"):
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        sec_id = int(command.split("-")[1])
        await state.update_data(edit_section_id=sec_id)
        await cb.message.edit_text(
            "✏️ Введите новое название раздела:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="adm_sections")]
            ])
        )
        await state.set_state(AdminInput.section_editname)

    elif command.startswith("adm_sec_setfilter-"):
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        sec_id = int(command.split("-")[1])
        await state.update_data(edit_section_id=sec_id)
        await cb.message.edit_text(
            "🔗 Введите URL фильтра с lzt.market\nЧтобы очистить: <code>-</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="adm_sections")]
            ])
        )
        await state.set_state(AdminInput.section_editfilter)

    elif command.startswith("adm_sec_del-"):
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        sec_id = int(command.split("-")[1])
        section = db.ShopSection.get_or_none(id=sec_id)
        if section:
            name = section.name
            section.delete_instance()
            await cb.answer(f"🗑️ Раздел «{name}» удалён", show_alert=True)
        sections = list(db.ShopSection.select().order_by(db.ShopSection.order, db.ShopSection.id))
        bild = InlineKeyboardBuilder()
        for s in sections:
            bild.button(text=f"✏️ {s.name}", callback_data=f"adm_sec_edit-{s.id}")
        bild.button(text="➕ Добавить раздел", callback_data="adm_sec_add")
        bild.adjust(1)
        bild.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin"))
        info = "📂 <b>Разделы магазина</b>\n\n" + ("".join(f"• {s.name}\n" for s in sections) or "<i>Нет разделов</i>")
        await cb.message.edit_text(info, reply_markup=bild.as_markup())

    # ==================== ПРОФІЛЬ ====================

    elif command == "profile":
        user = db.User.get_or_none(id=uid)
        if not user:
            await db.check_db(uid)
            user = db.User.get(id=uid)
        lzt_count = db.LztTransaction.select().where(db.LztTransaction.buyer_id == uid).count()
        my_count = db.Accounts.select().where(db.Accounts.id == uid).count() + lzt_count
        ref_count = db.User.select().where(db.User.referred_by == uid).count()
        ref_percent = db.get_setting("referral_percent", "5")
        bot_info = await cb.bot.get_me()
        ref_link = f"t.me/{bot_info.username}?start=ref_{uid}"
        bild = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Мои аккаунты", callback_data="my_accounts"),
             InlineKeyboardButton(text="💳 Пополнить", callback_data="popolnit")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
        ])
        await cb.message.edit_text(
            f"👤 <b>Профиль</b>\n"
            f"<code>ID: {uid}</code>\n\n"
            f"💰 <b>Баланс:</b> <code>${user.balance:.2f}</code>\n"
            f"📦 <b>Куплено аккаунтов:</b> <code>{my_count}</code>\n"
            f"👥 <b>Рефералов:</b> <code>{ref_count}</code> (+{ref_percent}% с покупок)\n\n"
            f"🔗 <b>Реферальная ссылка:</b>\n"
            f"<code>t.me/{bot_info.username}?start=ref_{uid}</code>",
            reply_markup=bild
        )

    # ==================== ПОПОВНЕННЯ ====================

    elif command == "popolnit":
        await cb.message.edit_text(
            "💳 <b>Пополнение баланса</b>\n\n"
            "Введите сумму в долларах, например: <code>5</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile")]
            ])
        )
        await state.set_state(TopupInput.amount)

    elif command.startswith("popol-"):
        summ = float(cb.data.split("-")[1])
        try:
            bot_info = await cb.bot.get_me()
            check_url, check_id = await cryptobot.create_invoice(
                amount=summ, description=f"Пополнение @{bot_info.username}"
            )
            await cb.message.edit_text(
                f"💎 <b>Счёт CryptoBot на ${summ:.2f}</b>\n\nОплатите по кнопке ниже.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Оплатить", url=check_url),
                     InlineKeyboardButton(text="✅ Проверить", callback_data=f"check_popol-{check_id}")],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="popolnit")]
                ])
            )
        except Exception:
            await cb.message.edit_text(
                "❌ <b>CryptoBot недоступен</b>\n\nВведите сумму снова и выберите другой способ:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="popolnit")]
                ])
            )

    elif command.startswith("check_popol-"):
        check_id = int(cb.data.split("-")[1])
        try:
            check, summ = await cryptobot.is_invoice_paid(check_id)
            if check:
                user = db.User.get_or_none(id=uid)
                if user:
                    user.balance += summ
                    user.save()
                await cb.message.edit_text(
                    f"✅ Пополнено на <code>${summ}</code>!",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                    ])
                )
            else:
                await cb.answer("⏳ Ещё не оплачено", show_alert=False)
        except Exception as e:
            await cb.answer(f"❌ {e}", show_alert=True)

    elif command == "my_accounts":
        bild = InlineKeyboardBuilder()
        # Локальные аккаунты (куплены из локального магазина)
        local_list = list(db.Accounts.select().where(db.Accounts.id == uid))
        for acc in local_list:
            flag, _ = accounts.get_country_info("+" + acc.AccountNumber)
            bild.button(text=f"{flag} +{acc.AccountNumber}", callback_data=f"my_account-{acc.AccountID}")
        # LZT аккаунты (куплены через LZT Market)
        lzt_list = list(db.LztTransaction.select().where(db.LztTransaction.buyer_id == uid).order_by(db.LztTransaction.id.desc()))
        for txn in lzt_list:
            try:
                ad = json.loads(txn.account_data)
                phone = ad.get("phone") or ad.get("account_phone") or ""
                if not phone:
                    # Ищем телефон в полях данных
                    for k, v in ad.items():
                        if "phone" in k.lower() and isinstance(v, str) and v:
                            phone = v
                            break
                label = f"📦 +{phone}" if phone else f"📦 LZT #{txn.lzt_item_id}"
            except:
                label = f"📦 LZT #{txn.lzt_item_id}"
            bild.button(text=label, callback_data=f"my_lzt_acc-{txn.id}")
        if not local_list and not lzt_list:
            bild.button(text="Нет аккаунтов 🤷🏼‍♂️", callback_data="e")
        bild.button(text="🏠 Меню", callback_data="menu")
        bild.adjust(1)
        total = len(local_list) + len(lzt_list)
        await cb.message.edit_text(f"📱 <b>Мои аккаунты</b> ({total} шт.):", reply_markup=bild.as_markup())

    elif command.startswith("my_account-"):
        await show_my_account(cb, int(cb.data.split("-")[1]), uid)

    elif command.startswith("my_lzt_acc-"):
        txn_id = int(command.split("-")[1])
        txn = db.LztTransaction.get_or_none(id=txn_id, buyer_id=uid)
        if not txn:
            return await cb.answer("❌ Не найдено", show_alert=True)
        try:
            ad = json.loads(txn.account_data)
        except:
            ad = {}
        phone = ""
        for k, v in ad.items():
            if "phone" in k.lower() and isinstance(v, str) and v:
                phone = v
                break
        session_str = ad.get("_session")
        text = (
            f"📦 <b>Аккаунт LZT #{txn.lzt_item_id}</b>\n\n"
            f"📞 Телефон: <code>{'+' + phone if phone else '—'}</code>\n"
            f"💰 Куплен за: <code>${txn.sell_price:.2f}</code>\n"
            f"📅 Дата: <code>{txn.purchased_at}</code>"
        )
        btn_rows = []
        if session_str:
            btn_rows.append([InlineKeyboardButton(text="📲 Получить код", callback_data=f"get_code-{txn.id}")])
        btn_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="my_accounts"),
                         InlineKeyboardButton(text="🏠 Меню", callback_data="menu")])
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btn_rows))

    elif command.startswith("codes-"):
        acc_id = int(cb.data.split("-")[1])
        info = db.Accounts.get_or_none(AccountID=acc_id, id=uid)
        if info:
            await cb.answer("⏳ Загружаю...")
            codes = await accounts.get_codes(auth_key=info.AuthKey)
            fcodes = "\n".join(f"<code>{c}</code> <i>({d})</i>" for c, d in codes)
            await cb.message.edit_text(
                f"🔢 Сессии #{info.AccountID}\n\n{fcodes}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"my_account-{acc_id}")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                ])
            )
        else:
            await cb.answer("❌ Не найдено")

    elif command.startswith("leave-"):
        AccountID = int(cb.data.split("-")[1])
        info = db.Accounts.get_or_none(AccountID=AccountID, id=uid)
        if info:
            await accounts.leave(info.AuthKey)
            info.AuthKey = ""
            info.save()
            await cb.message.edit_text(
                "☺️ Вышли из аккаунта.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                ])
            )
        else:
            await cb.answer("❌ Не найдено!")

    # ==================== ОТРИМАТИ КОД ====================

    elif command.startswith("get_code-"):
        import re as _re
        txn_id = int(command.split("-")[1])
        txn = db.LztTransaction.get_or_none(id=txn_id, buyer_id=uid)
        if not txn:
            return await cb.answer("❌ Транзакция не найдена", show_alert=True)
        session_str = None
        try:
            ad = json.loads(txn.account_data)
            session_str = ad.get("_session")
        except:
            pass
        if not session_str:
            lzt = get_lzt_client()
            if lzt:
                try:
                    item_data = json.loads(txn.account_data)
                    result = await asyncio.wait_for(lzt.try_login_with_key(item_data), timeout=25)
                    if result:
                        parts_r = result.split("|")
                        session_str = parts_r[0].replace("session:", "")
                        item_data["_session"] = session_str
                        txn.account_data = json.dumps(item_data, ensure_ascii=False)
                        txn.save()
                except:
                    pass
        if not session_str:
            return await cb.answer("❌ Не удалось подключиться к аккаунту", show_alert=True)
        await cb.answer("⏳ Ищу код...")
        try:
            client = TelegramClient(StringSession(session_str), api_id, api_hash)
            await asyncio.wait_for(client.connect(), timeout=15)
            if not await client.is_user_authorized():
                await client.disconnect()
                return await cb.answer("❌ Сессия больше не активна", show_alert=True)
            code = None
            messages = await client.get_messages(777000, limit=5)
            for msg in messages:
                if msg.text:
                    match = _re.search(r'\b(\d{5,6})\b', msg.text)
                    if match:
                        code = match.group(1)
                        break
            await client.disconnect()
            if code:
                await cb.message.edit_text(
                    f"📲 <b>Код авторизации</b>\n\n🔑 Код: <code>{code}</code>",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"get_code-{txn_id}")],
                        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                    ])
                )
            else:
                await cb.message.edit_text(
                    "📲 <b>Код не найден</b>\n\nИнициируйте вход и нажмите «Обновить».",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"get_code-{txn_id}")],
                        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
                    ])
                )
        except asyncio.TimeoutError:
            await cb.answer("⏳ Таймаут. Попробуйте ещё раз.", show_alert=True)
        except Exception as e:
            await cb.answer(f"❌ Ошибка: {str(e)[:100]}", show_alert=True)

    elif command == "review_start":
        review_channel = db.get_setting("review_channel", "")
        if not review_channel:
            return await cb.answer("Отзывы временно отключены", show_alert=True)
        await cb.message.edit_text(
            "✍️ <b>Напишите ваш отзыв</b>\n\nОтправьте текст:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="menu")]
            ])
        )
        await state.set_state(ReviewInput.text)

    elif command == "adm_set_review_channel":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("review_channel", "")
        await cb.message.edit_text(
            f"📢 <b>Канал для отзывов</b>\nТекущий: <code>{current or 'не задан'}</code>\n\n"
            "Введите @username канала. Чтобы отключить: <code>-</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.review_channel)

    elif command == "adm_set_req_channel":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("required_channel", "")
        await cb.message.edit_text(
            f"🔒 <b>Обязательная подписка (ОП)</b>\nТекущий: <code>{current or 'не задан (выкл.)'}</code>\n\n"
            "Введите @username канала. Чтобы отключить: <code>-</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]])
        )
        await state.set_state(AdminInput.required_channel)

    elif command == "adm_set_card_text":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("card_payment_text", "")
        await cb.message.edit_text(
            f"💳 <b>Текст оплаты на карту</b>\nТекущий: <code>{current[:200] if current else 'не задан'}</code>\n\n"
            "Введите новый текст. Чтобы отключить: <code>-</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]
            ])
        )
        await state.set_state(AdminInput.card_text)

    elif command == "adm_set_tonkeeper":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        await cb.message.edit_text(
            f"💎 <b>Tonkeeper</b>\n\nТекущий адрес из config.py:\n<code>{TON_ADDRESS or 'не задан'}</code>\n\n"
            "Адрес задаётся в <code>config.py</code> → поле <code>TON_ADDRESS</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
            ])
        )

    elif command == "adm_set_support_url":
        if uid not in OWNERS:
            return await cb.answer("❌", show_alert=True)
        current = db.get_setting("support_url", "t.me/sierafimv")
        await cb.message.edit_text(
            f"💬 <b>Ссылка поддержки</b>\n\n"
            f"Текущая: <code>{current}</code>\n\n"
            "Введите новую ссылку (например: <code>t.me/username</code>):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]
            ])
        )
        await state.set_state(AdminInput.support_url)

    # ==================== ПРОВЕРКА ПОДПИСКИ ====================

    elif command == "check_sub":
        if await _check_subscription(cb.bot, uid):
            bild = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛒 Купить аккаунт", callback_data="shop")],
                [InlineKeyboardButton(text="🎨 Профиль", callback_data="profile"),
                 InlineKeyboardButton(text="💬 Поддержка", url=db.get_setting("support_url", "t.me/sierafimv"))]
            ])
            await cb.message.edit_text(
                "✅ <b>Подписка подтверждена!</b>\n\n"
                "Добро пожаловать! Выберите действие 👇",
                reply_markup=bild
            )
        else:
            await cb.answer("❌ Вы ещё не подписались на канал!", show_alert=True)

    elif command == "e":
        await cb.answer("🤷🏼‍♂️")


# ==================== Допоміжні функції ====================

async def auto_buy_timeout(bot, pending_id: int, timeout_sec: int = 600):
    await asyncio.sleep(timeout_sec)
    pending = db.PendingPurchase.get_or_none(id=pending_id, status="pending")
    if not pending:
        return
    lzt = get_lzt_client()
    if not lzt:
        return
    for owner_id in OWNERS:
        try:
            await bot.send_message(
                owner_id,
                f"⏰ <b>Авто-покупка</b> (10 мин прошло)\n📦 {pending.item_title}\n👤 <code>{pending.buyer_id}</code>"
            )
        except:
            pass
    try:
        success, item_data = await lzt.fast_buy(pending.lzt_item_id, pending.lzt_price)
    except Exception as e:
        user = db.User.get_or_none(id=pending.buyer_id)
        if user:
            user.balance += pending.sell_price
            user.save()
        pending.status = "failed"
        pending.save()
        try:
            await bot.send_message(pending.buyer_id, f"❌ Покупка не удалась. ${pending.sell_price:.2f} возвращено.")
        except:
            pass
        return
    if not success:
        user = db.User.get_or_none(id=pending.buyer_id)
        if user:
            user.balance += pending.sell_price
            user.save()
        pending.status = "failed"
        pending.save()
        try:
            await bot.send_message(pending.buyer_id, f"❌ Не удалось оформить покупку. ${pending.sell_price:.2f} возвращено на баланс.")
        except:
            pass
        return
    pending.status = "approved"
    pending.save()
    txn = db.LztTransaction.create(
        buyer_id=pending.buyer_id,
        lzt_item_id=pending.lzt_item_id,
        lzt_price=pending.lzt_price,
        sell_price=pending.sell_price,
        account_data=json.dumps(item_data, ensure_ascii=False),
        purchased_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    account_text, session_str = await _get_account_text(lzt, item_data)
    if session_str:
        try:
            ad = json.loads(txn.account_data)
            ad["_session"] = session_str
            txn.account_data = json.dumps(ad, ensure_ascii=False)
            txn.save()
        except:
            pass
    btn_rows = []
    if session_str:
        btn_rows.append([InlineKeyboardButton(text="📲 Получить код", callback_data=f"get_code-{txn.id}")])
    btn_rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="menu")])
    buyer_kbd = InlineKeyboardMarkup(inline_keyboard=btn_rows)
    try:
        await bot.send_message(pending.buyer_id, f"✅ <b>Аккаунт куплен!</b>\n\n{account_text}",
                               reply_markup=buyer_kbd)
    except:
        pass
    await _pay_referral(bot, pending.buyer_id, pending.sell_price)
    await _send_review_request(bot, pending.buyer_id)


async def execute_lzt_purchase(bot, message, buyer_id, item_id, lzt_price_raw, sell_usd, lzt):
    """lzt_price_raw — ціна в оригінальній валюті LZT (для fast_buy), sell_usd — ціна продажу в $."""
    refund_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
    ])
    try:
        success, item_data = await lzt.fast_buy(item_id, lzt_price_raw)
    except Exception as e:
        user = db.User.get_or_none(id=buyer_id)
        if user:
            user.balance += sell_usd
            user.save()
        await message.edit_text(f"❌ Ошибка: <code>{e}</code>\n💸 ${sell_usd:.2f} возвращено.", reply_markup=refund_markup)
        return
    if not success:
        user = db.User.get_or_none(id=buyer_id)
        if user:
            user.balance += sell_usd
            user.save()
        error = item_data.get('error', '?') if isinstance(item_data, dict) else str(item_data)
        await message.edit_text(f"❌ Не удалось: <code>{error}</code>\n💸 ${sell_usd:.2f} возвращено.", reply_markup=refund_markup)
        return
    txn = db.LztTransaction.create(
        buyer_id=buyer_id,
        lzt_item_id=item_id,
        lzt_price=lzt_price_raw,
        sell_price=sell_usd,
        account_data=json.dumps(item_data, ensure_ascii=False),
        purchased_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    account_text, session_str = await _get_account_text(lzt, item_data)
    if session_str:
        try:
            ad = json.loads(txn.account_data)
            ad["_session"] = session_str
            txn.account_data = json.dumps(ad, ensure_ascii=False)
            txn.save()
        except:
            pass
    btn_rows = []
    if session_str:
        btn_rows.append([InlineKeyboardButton(text="📲 Получить код", callback_data=f"get_code-{txn.id}")])
    btn_rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="menu")])
    buyer_kbd = InlineKeyboardMarkup(inline_keyboard=btn_rows)
    await message.edit_text(f"✅ <b>Аккаунт куплен!</b>\n\n{account_text}", reply_markup=buyer_kbd)
    await _pay_referral(bot, buyer_id, sell_usd)
    await _send_review_request(bot, buyer_id)


async def show_my_account(cb: types.CallbackQuery, account_id: int, uid: int):
    info = db.Accounts.get_or_none(AccountID=account_id, id=uid)
    if not info:
        return await cb.answer("❌ Не найдено")
    flag, country = accounts.get_country_info("+" + info.AccountNumber)
    bild = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Коды", callback_data=f"codes-{account_id}"),
         InlineKeyboardButton(text="🚪 Выйти", callback_data=f"leave-{account_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_accounts")]
    ])
    await cb.message.edit_text(
        f"📱 <b>Аккаунт #{account_id}</b>\n\n"
        f"🌍 Страна: <code>{flag} {country}</code>\n"
        f"📞 Номер: <code>+{info.AccountNumber}</code>",
        reply_markup=bild
    )
