"""Open-Meteo geocode + current conditions + 7-day forecast."""

from __future__ import annotations

from datetime import datetime

import httpx
from langsmith import traceable

from daily_bubble.models import WeatherCache, WeatherCurrent, WeatherDay

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes (subset).
_WMO: dict[int, str] = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Icy fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Heavy freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Rain showers",
    82: "Heavy rain showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def weather_summary(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return _WMO.get(int(code), f"Weather code {code}")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def geocode_location(location: str) -> tuple[float, float, str]:
    """Return (lat, lon, resolved_name) for a human location string."""
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            GEOCODE_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
        )
        response.raise_for_status()
        payload = response.json()
    results = payload.get("results") or []
    if not results:
        raise ValueError(f"Could not geocode location: {location}")
    hit = results[0]
    parts = [hit.get("name"), hit.get("admin1"), hit.get("country_code")]
    resolved = ", ".join(p for p in parts if p)
    return float(hit["latitude"]), float(hit["longitude"]), resolved or location


def unavailable_weather(location: str) -> WeatherCache:
    return WeatherCache(
        fetched_at=_now_iso(),
        location=location,
        latitude=0.0,
        longitude=0.0,
        current=WeatherCurrent(summary="unavailable", temp_f=0.0, wind_mph=0.0),
        forecast=[],
    )


@traceable(name="weather", tags=["weather"])
def fetch_weather(location: str) -> WeatherCache:
    last_error: Exception | None = None
    for _ in range(2):
        try:
            return _fetch_weather_once(location)
        except Exception as exc:  # noqa: BLE001 — retry once, then caller may stub
            last_error = exc
    assert last_error is not None
    raise last_error


def _fetch_weather_once(location: str) -> WeatherCache:
    lat, lon, resolved = geocode_location(location)
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "forecast_days": 7,
                "timezone": "auto",
            },
        )
        response.raise_for_status()
        payload = response.json()

    current = payload.get("current") or {}
    daily = payload.get("daily") or {}
    current_code = current.get("weather_code")
    forecast: list[WeatherDay] = []
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    for i, day in enumerate(dates[:7]):
        forecast.append(
            WeatherDay(
                date=day,
                high_f=float(highs[i]) if i < len(highs) and highs[i] is not None else 0.0,
                low_f=float(lows[i]) if i < len(lows) and lows[i] is not None else 0.0,
                summary=weather_summary(codes[i] if i < len(codes) else None),
            )
        )

    return WeatherCache(
        fetched_at=_now_iso(),
        location=resolved,
        latitude=lat,
        longitude=lon,
        current=WeatherCurrent(
            summary=weather_summary(current_code),
            temp_f=float(current.get("temperature_2m") or 0),
            wind_mph=float(current.get("wind_speed_10m") or 0),
        ),
        forecast=forecast,
    )


def weather_oneliner(weather: WeatherCache | None) -> str:
    if weather is None:
        return "weather unavailable"
    today = weather.forecast[0] if weather.forecast else None
    extra = ""
    if today:
        extra = f", high {today.high_f:.0f} / low {today.low_f:.0f}"
    return (
        f"{weather.current.summary} {weather.current.temp_f:.0f}F"
        f"{extra} ({weather.location})"
    )
