"""
Weather plugin - gives Alyssa a "get_weather" ability, e.g. "Alyssa, what's
the weather like" or "Alyssa, what's the weather in Tokyo".

Uses Open-Meteo (https://open-meteo.com) for both geocoding (turning a city
name into coordinates) and the forecast itself - free, no API key, no
account, same "no cost" spirit as the rest of this project.

No location given -> tries, in order: WEATHER_DEFAULT_LOCATION in
config.py if set, then auto-detecting your city from your IP address (see
_shared_location.py) if AUTO_DETECT_LOCATION is on, then finally just asks.
"""
import requests

import config
from location import get_ip_location

UNTRUSTED_OUTPUTS = {"get_weather"}

_GEOCODE_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes -> short human description.
# https://open-meteo.com/en/docs#weathervariables
_WEATHER_CODES = {
    0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy with frost", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
    67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "light rain showers", 81: "rain showers",
    82: "violent rain showers", 85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with light hail",
    99: "thunderstorms with heavy hail",
}


def _geocode(location: str) -> dict | None:
    response = requests.get(
        _GEOCODE_ENDPOINT,
        params={"name": location, "count": 1, "language": "en", "format": "json"},
        timeout=10,
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        return None
    place = results[0]
    label_bits = [place.get("name", location)]
    if place.get("admin1") and place["admin1"] != place.get("name"):
        label_bits.append(place["admin1"])
    if place.get("country"):
        label_bits.append(place["country"])
    return {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "label": ", ".join(label_bits),
    }


def get_weather(location: str = "") -> str:
    location = (location or "").strip()
    place = None

    if not location:
        location = getattr(config, "WEATHER_DEFAULT_LOCATION", "")

    if not location:
        # Nothing named and no default set - try auto-detecting from the IP
        # address before giving up and asking.
        detected = get_ip_location()
        if detected:
            place = detected
        else:
            return (
                "I don't have a default location set, and I couldn't "
                "detect one automatically - say a city name, or set "
                "WEATHER_DEFAULT_LOCATION in config.py."
            )

    if not place:
        try:
            place = _geocode(location)
        except requests.exceptions.RequestException as e:
            return f"I couldn't reach the weather service - {e}"
        if not place:
            return f"I couldn't find a place called '{location}'."

    unit = "fahrenheit" if getattr(config, "WEATHER_UNITS", "imperial") == "imperial" else "celsius"
    speed_unit = "mph" if unit == "fahrenheit" else "kmh"

    try:
        response = requests.get(
            _FORECAST_ENDPOINT,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": unit,
                "wind_speed_unit": speed_unit,
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return f"I found {place['label']}, but couldn't fetch the forecast - {e}"

    current = data.get("current", {})
    daily = data.get("daily", {})
    temp = current.get("temperature_2m")
    feels_like = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")
    code = current.get("weather_code")
    high = (daily.get("temperature_2m_max") or [None])[0]
    low = (daily.get("temperature_2m_min") or [None])[0]

    if temp is None:
        return f"I found {place['label']}, but the forecast came back empty."

    deg = "°F" if unit == "fahrenheit" else "°C"
    condition = _WEATHER_CODES.get(code, "changeable conditions")

    parts = [f"{place['label']}: {condition}, {round(temp)}{deg}"]
    if feels_like is not None and round(feels_like) != round(temp):
        parts.append(f"feels like {round(feels_like)}{deg}")
    if high is not None and low is not None:
        parts.append(f"high {round(high)}{deg}, low {round(low)}{deg}")
    if humidity is not None:
        parts.append(f"{round(humidity)}% humidity")
    if wind is not None:
        parts.append(f"wind {round(wind)} {speed_unit}")

    return ", ".join(parts) + "."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Gets the current weather and today's forecast for a "
                "location, e.g. 'what's the weather like', 'what's it like "
                "outside', 'weather in Chicago', 'do I need an umbrella "
                "today'. Leave location blank to use the default set in "
                "config.py."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": (
                            "City (and optionally region/country) to check, "
                            "e.g. 'Chicago' or 'Chicago, IL'. Leave blank "
                            "for the configured default / when the user "
                            "doesn't name a place."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
]

FUNCTIONS = {
    "get_weather": get_weather,
}
