"""Live crypto prices from CoinGecko's free API - 'what's bitcoin at?'."""
import requests

PERMISSIONS = ["network"]
UNTRUSTED_OUTPUTS = {"get_crypto_price"}

_ALIASES = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana", "doge": "dogecoin", "xrp": "ripple", "ada": "cardano"}


def get_crypto_price(coin: str = "bitcoin", currency: str = "usd") -> str:
    coin_id = _ALIASES.get(coin.strip().lower(), coin.strip().lower().replace(" ", "-"))
    currency = (currency or "usd").strip().lower()
    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin_id, "vs_currencies": currency, "include_24hr_change": "true"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json().get(coin_id, {})
    except (requests.RequestException, ValueError) as e:
        return f"I couldn't reach CoinGecko just now - {e}"
    if currency not in data:
        return f"I couldn't find a price for '{coin}' in {currency.upper()}."
    price = data[currency]
    change = data.get(f"{currency}_24h_change")
    text = f"{coin_id.replace('-', ' ').title()} is at {price:,.2f} {currency.upper()}"
    if change is not None:
        text += f", {'up' if change >= 0 else 'down'} {abs(change):.1f}% today"
    return text + "."


FUNCTIONS = {"get_crypto_price": get_crypto_price}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_crypto_price",
            "description": "Current price of a cryptocurrency, e.g. 'what's bitcoin at', 'ETH price in euros'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {"type": "string", "description": "Coin name or ticker, e.g. bitcoin, eth."},
                    "currency": {"type": "string", "description": "Currency code, default usd."},
                },
                "required": ["coin"],
            },
        },
    },
]
