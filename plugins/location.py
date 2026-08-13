"""
Location plugin - gives Alyssa a "get_my_location" ability, e.g. "where am I".
Uses IP-based geolocation via ipapi.co.
"""
import time
import requests
import config

UNTRUSTED_OUTPUTS = {"get_my_location"}

_ENDPOINT = "https://ipapi.co/json/"
_cache = {"location": None, "fetched_at": 0.0}


def get_ip_location() -> dict | None:
    """Returns {"latitude", "longitude", "label"} from current public IP, or None."""
    if not getattr(config, "AUTO_DETECT_LOCATION", True):
        return None

    cache_seconds = max(0, int(getattr(config, "LOCATION_CACHE_MINUTES", 60))) * 60
    now = time.time()
    if _cache["location"] and (now - _cache["fetched_at"]) < cache_seconds:
        return _cache["location"]

    try:
        response = requests.get(_ENDPOINT, timeout=6)
        response.raise_for_status()
        data = response.json()
    except (requests.exceptions.RequestException, ValueError):
        return _cache["location"]

    if data.get("error") or "latitude" not in data or "longitude" not in data:
        return _cache["location"]

    label_bits = [b for b in (data.get("city"), data.get("region"), data.get("country_name")) if b]
    location = {
        "latitude": data["latitude"],
        "longitude": data["longitude"],
        "label": ", ".join(label_bits) or "your area",
    }
    _cache["location"] = location
    _cache["fetched_at"] = now
    return location


def get_my_location() -> str:
    place = get_ip_location()
    if not place:
        return (
            "I couldn't detect your location - either automatic detection "
            "is off (AUTO_DETECT_LOCATION in config.py) or the lookup failed."
        )
    return (
        f"Based on your IP address, you're near {place['label']}. That's "
        "an approximation from your internet connection, not exact - it "
        "can be off if you're on a VPN."
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_my_location",
            "description": (
                "Reports the user's approximate current location (city-level, detected from their IP address)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

FUNCTIONS = {
    "get_my_location": get_my_location,
}
