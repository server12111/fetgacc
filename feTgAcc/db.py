from peewee import *

db = SqliteDatabase('shop.db')


class BaseModel(Model):
    class Meta:
        database = db


class User(BaseModel):
    id = BigIntegerField(primary_key=True)  # Telegram user ID
    balance = FloatField(default=0.0)
    username = CharField(default="")
    registered_at = CharField(default="")
    referred_by = BigIntegerField(default=0)  # ID реферера (0 = немає)

    class Meta:
        table_name = 'users'


class AccountsShop(BaseModel):
    ShopID = BigIntegerField(primary_key=True)  # Уникальный ID товара
    AccountID = BigIntegerField()                # Telegram ID аккаунта
    AccountNumber = CharField()                  # Номер телефона (без +)
    AuthKey = CharField()                        # Telethon session string
    Price = FloatField()                         # Цена в $
    description = TextField(default="")          # Описание

    class Meta:
        table_name = 'accounts_shop'


class Accounts(BaseModel):
    id = BigIntegerField()         # Telegram ID покупателя
    AccountID = BigIntegerField()  # Telegram ID аккаунта
    AccountNumber = CharField()    # Номер телефона (без +)
    AuthKey = CharField()          # Telethon session string

    class Meta:
        table_name = 'accounts'


class LztTransaction(BaseModel):
    """Зберігає транзакції покупки з LZT Market"""
    id = AutoField()
    buyer_id = BigIntegerField()
    lzt_item_id = BigIntegerField()
    lzt_price = FloatField()
    sell_price = FloatField()
    account_data = TextField()
    purchased_at = CharField()

    class Meta:
        table_name = 'lzt_transactions'


class PendingPurchase(BaseModel):
    """Запити юзерів на купівлю, що очікують підтвердження адміна"""
    id = AutoField()
    buyer_id = BigIntegerField()
    buyer_username = CharField(default="")
    lzt_item_id = BigIntegerField()
    lzt_price = FloatField()           # сира ціна в lzt_currency (напр. 16.0 RUB)
    lzt_currency = CharField(default="RUB")  # валюта LZT (RUB/EUR/USD)
    sell_price = FloatField()          # ціна продажу в USD
    item_title = CharField(default="")
    status = CharField(default="pending")  # pending / approved / rejected / failed
    created_at = CharField()

    class Meta:
        table_name = 'pending_purchases'


class ShopSection(BaseModel):
    """Розділи магазину — кнопки з фільтрами LZT"""
    id = AutoField()
    name = CharField()              # Назва кнопки, напр. "🇺🇦 Україна"
    filter_url = TextField(default="")  # URL фільтру з lzt.market
    order = IntegerField(default=0)

    class Meta:
        table_name = 'shop_sections'


class BotSettings(BaseModel):
    """Налаштування бота, що зберігаються між перезапусками"""
    key = CharField(primary_key=True)
    value = TextField(default="")

    class Meta:
        table_name = 'bot_settings'


def get_setting(key: str, default: str = "") -> str:
    s = BotSettings.get_or_none(key=key)
    return s.value if s else default


def set_setting(key: str, value) -> None:
    BotSettings.insert(key=key, value=str(value)).on_conflict_replace().execute()


def initialize_db():
    """Создает таблицы если их нет"""
    db.connect(reuse_if_open=True)
    db.create_tables([User, AccountsShop, Accounts, LztTransaction, PendingPurchase, BotSettings, ShopSection], safe=True)
    # Міграції: додаємо нові колонки якщо їх немає
    for sql in [
        "ALTER TABLE pending_purchases ADD COLUMN lzt_currency VARCHAR(10) DEFAULT 'RUB'",
        "ALTER TABLE users ADD COLUMN referred_by BIGINT DEFAULT 0",
    ]:
        try:
            db.execute_sql(sql)
        except:
            pass
    print("✅ База данных инициализирована")


async def check_db(uid: int, username: str = ""):
    """Проверяет/создает пользователя в БД"""
    import datetime
    user = User.get_or_none(id=uid)
    if user is None:
        User.create(
            id=uid,
            balance=0.0,
            username=username,
            registered_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    return User.get(id=uid)
