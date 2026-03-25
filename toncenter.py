import aiohttp

TONCENTER_URL = "https://toncenter.com/api/v2"


async def get_recent_transactions(address: str, api_key: str = "", limit: int = 20) -> list:
    """
    Повертає список вхідних транзакцій на адресу.
    Кожна транзакція: {"hash": str, "comment": str, "amount_ton": float}
    """
    params = {"address": address, "limit": limit}
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{TONCENTER_URL}/getTransactions",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
    except Exception as e:
        print(f"[TON] Помилка запиту: {e}")
        return []

    if not data.get("ok"):
        print(f"[TON] API error: {data.get('error')}")
        return []

    result = []
    for tx in data.get("result", []):
        in_msg = tx.get("in_msg", {})
        value = int(in_msg.get("value", 0))
        if value <= 0:
            continue
        amount_ton = value / 1_000_000_000
        comment = in_msg.get("message", "").strip()
        tx_hash = tx.get("transaction_id", {}).get("hash", "")
        if tx_hash:
            result.append({
                "hash": tx_hash,
                "comment": comment,
                "amount_ton": amount_ton,
            })
    return result
