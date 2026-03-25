import aiohttp
import os
import zipfile
import tempfile

LZT_BASE = "https://api.lzt.market"


class LztAPI:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
        }

    async def _get(self, path: str, params: dict = None, retries: int = 3) -> dict:
        import asyncio
        last_err = None
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{LZT_BASE}{path}",
                        headers=self.headers,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as r:
                        return await r.json()
            except Exception as e:
                last_err = e
                if attempt < retries - 1:
                    await asyncio.sleep(2)
        raise last_err

    async def _post(self, path: str, data: dict = None, retries: int = 2) -> dict:
        import asyncio
        last_err = None
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{LZT_BASE}{path}",
                        headers=self.headers,
                        json=data or {},
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as r:
                        return await r.json()
            except Exception as e:
                last_err = e
                if attempt < retries - 1:
                    await asyncio.sleep(2)
        raise last_err

    async def get_balance(self) -> tuple[float, str]:
        """
        Повертає (баланс, валюта).
        LZT повертає баланс в рублях.
        """
        data = await self._get("/me")
        user = data.get("user", data)
        # Перебираємо можливі поля балансу
        for field in ("balance", "user_balance", "balanceInt", "money"):
            val = user.get(field)
            if val is not None:
                return float(val), "₽"
        return 0.0, "₽"

    async def search_telegram(
        self,
        pmin: float = None,
        pmax: float = None,
        country: str = None,
        page: int = 1,
        extra_params: dict = None
    ) -> tuple[list[dict], str | None]:
        """
        Шукає Telegram акаунти на LZT Market.
        Повертає (список акаунтів, текст помилки або None).
        items може бути dict {id: item} або list — обробляємо обидва.
        """
        params = {
            "page": page,
            "order_by": "price_asc",
        }
        if pmin is not None:
            params["pmin"] = pmin
        if pmax is not None:
            params["pmax"] = pmax
        if country:
            params["country"] = country

        # Параметри з фільтр-URL перекривають базові
        if extra_params:
            for k, v in extra_params.items():
                if k not in ("page",):
                    params[k] = v

        data = await self._get("/telegram", params=params)

        # Перевіряємо на помилку API
        if "errors" in data:
            err = data["errors"]
            msg = err if isinstance(err, str) else str(list(err.values())[0]) if isinstance(err, dict) else str(err)
            return [], msg

        raw_items = data.get("items", [])

        # LZT повертає items як dict {item_id: {...}} АБО як list
        if isinstance(raw_items, dict):
            items = list(raw_items.values())
        elif isinstance(raw_items, list):
            items = raw_items
        else:
            items = []

        return items, None

    async def get_item(self, item_id: int) -> dict:
        """Повертає повну інформацію про лот"""
        data = await self._get(f"/{item_id}")
        return data.get("item", data)

    async def _download_bytes(self, url: str) -> bytes:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=60)) as r:
                return await r.read()

    async def fast_buy(self, item_id: int, price: float) -> tuple[bool, dict]:
        """Миттєво купує лот. Повертає (успіх, дані)"""
        data = await self._post(f"/{item_id}/fast-buy", {"price": price})
        if "item" in data:
            return True, data["item"]
        errors = data.get("errors", {})
        return False, {"error": str(errors) if errors else str(data)}

    async def get_tdata_url(self, item_id: int) -> str | None:
        """
        Шукає пряме посилання на TData після покупки акаунту LZT.
        Перебирає відомі endpoints і поля відповіді.
        """
        import json as _json

        # Спробуємо кілька відомих endpoints
        candidates = [
            f"/{item_id}",
            f"/market/{item_id}",
            f"/telegram/{item_id}",
        ]

        merged = {}
        for path in candidates:
            try:
                data = await self._get(path)
                item = data.get("item", data)
                merged.update(item)
            except:
                pass

        # Парсимо account_data якщо це JSON-рядок
        for key in ("account_data", "goods_data", "stick", "data"):
            val = merged.get(key)
            if isinstance(val, str):
                try:
                    parsed = _json.loads(val)
                    merged[key + "_parsed"] = parsed
                except:
                    pass

        # Рекурсивно шукаємо tdata URLs
        def find_tdata(obj, depth=0):
            if depth > 5:
                return None
            if isinstance(obj, str):
                low = obj.lower()
                if any(kw in low for kw in ["tdata", "tdatas", "tg_data"]) and "http" in low:
                    return obj
                if low.endswith(".zip") and "http" in low:
                    return obj
            elif isinstance(obj, dict):
                # Пріоритетні ключі
                for k in ("tdata", "tdata_url", "download_url", "file_url", "url", "link"):
                    v = obj.get(k)
                    if v:
                        r = find_tdata(v, depth + 1)
                        if r:
                            return r
                for v in obj.values():
                    r = find_tdata(v, depth + 1)
                    if r:
                        return r
            elif isinstance(obj, list):
                for v in obj:
                    r = find_tdata(v, depth + 1)
                    if r:
                        return r
            return None

        return find_tdata(merged)

    async def try_login_with_key(self, item: dict) -> str | None:
        """
        Шукає hex auth_key або session string в даних акаунту LZT,
        заходить в Telegram і повертає 'session:...|phone:...' або None.
        """
        import json as _json
        import struct
        import socket
        import base64
        from config import api_id, api_hash
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        DC_IPS = {
            1: "149.154.175.53",
            2: "149.154.167.51",
            3: "149.154.175.100",
            4: "149.154.167.91",
            5: "91.108.56.130",
        }

        # Розгортаємо JSON-рядки у вкладених полях
        merged = dict(item)
        for k in ("account_data", "goods_data", "stick", "data", "credentials"):
            v = merged.get(k)
            if isinstance(v, str):
                try:
                    parsed = _json.loads(v)
                    if isinstance(parsed, dict):
                        merged.update(parsed)
                except:
                    pass

        def find_in(obj, depth=0):
            """Повертає (тип, значення, dc_id) або None."""
            if depth > 5 or obj is None:
                return None
            if isinstance(obj, str):
                # Hex auth_key — рівно 512 hex-символів (256 байт)
                s = obj.strip()
                if len(s) == 512 and all(c in '0123456789abcdefABCDEF' for c in s):
                    return ("hex", s, None)
                # Telethon StringSession — починається з '1' або '2', довга base64
                if len(s) > 300 and s[0] in ('1', '2'):
                    try:
                        base64.urlsafe_b64decode(s[1:] + '==')
                        return ("session", s, None)
                    except:
                        pass
            elif isinstance(obj, dict):
                # Пріоритетні ключі
                dc_hint = obj.get("dc_id") or obj.get("dc") or obj.get("data_center")
                for k in ("auth_key", "authkey", "authorization_key", "hex_key",
                          "key", "auth", "token"):
                    v = obj.get(k)
                    if isinstance(v, str) and len(v.strip()) == 512:
                        return ("hex", v.strip(), int(dc_hint) if dc_hint else None)
                for k in ("session", "session_string", "string_session"):
                    v = obj.get(k)
                    if isinstance(v, str) and len(v) > 300:
                        return ("session", v.strip(), None)
                for v in obj.values():
                    r = find_in(v, depth + 1)
                    if r:
                        return r
            elif isinstance(obj, list):
                for v in obj:
                    r = find_in(v, depth + 1)
                    if r:
                        return r
            return None

        found = find_in(merged)
        if not found:
            return None

        kind, value, dc_id = found

        if kind == "session":
            # Готовий StringSession — просто підключаємось
            try:
                client = TelegramClient(StringSession(value), api_id, api_hash)
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    return None
                me = await client.get_me()
                phone = getattr(me, "phone", None) or "невідомо"
                await client.disconnect()
                return f"session:{value}|phone:{phone}"
            except:
                return None

        # kind == "hex" — будуємо StringSession з auth_key
        auth_bytes = bytes.fromhex(value)
        dcs_to_try = [dc_id] if dc_id and dc_id in DC_IPS else list(DC_IPS.keys())

        import asyncio as _asyncio
        for dc in dcs_to_try:
            client = None
            try:
                ip = DC_IPS[dc]
                raw = struct.pack('>B4sH256s', dc, socket.inet_aton(ip), 443, auth_bytes)
                session_str = '1' + base64.urlsafe_b64encode(raw).decode('ascii')
                client = TelegramClient(StringSession(session_str), api_id, api_hash,
                                        connection_retries=1, timeout=8)
                await _asyncio.wait_for(client.connect(), timeout=8)
                if not await client.is_user_authorized():
                    await client.disconnect()
                    continue
                me = await client.get_me()
                phone = getattr(me, "phone", None) or "невідомо"
                final_session = StringSession.save(client.session)
                await client.disconnect()
                return f"session:{final_session}|phone:{phone}"
            except:
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass
                continue

        return None

    async def try_extract_tdata(self, item: dict) -> str | None:
        """
        Знаходить TData URL на LZT, завантажує архів, конвертує через opentele.
        Повертає рядок 'session:...|phone:...' або None.
        """
        item_id = item.get("item_id") or item.get("id")
        if not item_id:
            return None

        tdata_url = await self.get_tdata_url(item_id)
        if not tdata_url:
            return None

        try:
            data_bytes = await self._download_bytes(tdata_url)
        except:
            return None

        try:
            import opentele.td as otd
            import opentele.tl as otl
        except ImportError:
            return None

        tmp_dir = tempfile.mkdtemp()
        try:
            zip_path = os.path.join(tmp_dir, "tdata.zip")
            with open(zip_path, "wb") as f:
                f.write(data_bytes)

            tdata_dir = os.path.join(tmp_dir, "tdata_extracted")
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(tdata_dir)

            # Знаходимо папку з tdata (містить key_datas або типові файли)
            tdata_path = tdata_dir
            for root, dirs, files in os.walk(tdata_dir):
                if "key_datas" in files or any(len(f) == 17 and f.endswith("s") for f in files):
                    tdata_path = root
                    break

            tdesk = otd.TDesktop(tdata_path)
            if not tdesk.isLoaded():
                return None

            client = await tdesk.ToTelethon(session=None, flag=otl.CreateNewSession)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                return None

            me = await client.get_me()
            from telethon.sessions import StringSession
            session_str = StringSession.save(client.session)
            phone = getattr(me, "phone", None) or "невідомо"
            await client.disconnect()

            return f"session:{session_str}|phone:{phone}"
        except:
            return None
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def format_account_data(self, item: dict) -> str:
        """Форматує дані купленого акаунту для відправки юзеру"""
        lines = ["📦 <b>Данные аккаунта с LZT Market</b>\n"]

        if item.get("phone_number"):
            lines.append(f"📞 Телефон: <code>{item['phone_number']}</code>")
        if item.get("email_login"):
            lines.append(f"📧 Email: <code>{item['email_login']}</code>")
        if item.get("email_password"):
            lines.append(f"🔑 Пароль email: <code>{item['email_password']}</code>")
        if item.get("account_password"):
            lines.append(f"🔐 Пароль аккаунта: <code>{item['account_password']}</code>")
        if item.get("twofa_totp"):
            lines.append(f"🔒 2FA: <code>{item['twofa_totp']}</code>")

        # Перевіряємо вкладені структури (goods, account_data, data)
        for nested_key in ("goods", "account_data", "data", "credentials"):
            nested = item.get(nested_key)
            if isinstance(nested, dict):
                if nested.get("phone_number") and "📞" not in "\n".join(lines):
                    lines.append(f"📞 Телефон: <code>{nested['phone_number']}</code>")
                if nested.get("session"):
                    lines.append(f"🔑 Session: <code>{nested['session']}</code>")
                if nested.get("tdata_url") or nested.get("download_url"):
                    url = nested.get("tdata_url") or nested.get("download_url")
                    lines.append(f"📁 TData: <code>{url}</code>")

        if item.get("description", "").strip():
            lines.append(f"\n📝 От продавца:\n<i>{item['description'].strip()[:800]}</i>")

        item_id = item.get("item_id") or item.get("id")
        if item_id:
            lines.append(f"\n🆔 LZT ID: <code>{item_id}</code>")

        if len(lines) <= 2:
            lines.append("⚠️ Продавец не предоставил структурированных данных. Проверьте описание выше.")

        return "\n".join(lines)
