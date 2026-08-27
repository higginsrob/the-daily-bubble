"""Daily article + weather cache on disk."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from daily_bubble.config import cache_root
from daily_bubble.models import ArticleCache, WeatherCache


def today_stamp() -> str:
    return datetime.now().astimezone().date().isoformat()


def user_day_dir(user_id: str, day: str | None = None) -> Path:
    return cache_root() / user_id / (day or today_stamp())


def cache_is_fresh(user_id: str, day: str | None = None) -> bool:
    folder = user_day_dir(user_id, day)
    return (folder / "articles.json").exists() and (folder / "weather.json").exists()


def load_articles(user_id: str, day: str | None = None) -> ArticleCache:
    path = user_day_dir(user_id, day) / "articles.json"
    with path.open(encoding="utf-8") as handle:
        return ArticleCache.model_validate(json.load(handle))


def load_weather(user_id: str, day: str | None = None) -> WeatherCache:
    path = user_day_dir(user_id, day) / "weather.json"
    with path.open(encoding="utf-8") as handle:
        return WeatherCache.model_validate(json.load(handle))


def save_articles(cache: ArticleCache, day: str | None = None) -> Path:
    folder = user_day_dir(cache.user_id, day)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "articles.json"
    path.write_text(cache.model_dump_json(indent=2), encoding="utf-8")
    return path


def save_weather(user_id: str, weather: WeatherCache, day: str | None = None) -> Path:
    folder = user_day_dir(user_id, day)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "weather.json"
    path.write_text(weather.model_dump_json(indent=2), encoding="utf-8")
    return path


def write_body(user_id: str, article_id: str, body: str, day: str | None = None) -> str:
    folder = user_day_dir(user_id, day) / "bodies"
    folder.mkdir(parents=True, exist_ok=True)
    rel = f"bodies/{article_id}.txt"
    (folder / f"{article_id}.txt").write_text(body, encoding="utf-8")
    return rel


def read_body(user_id: str, body_path: str, day: str | None = None) -> str:
    path = user_day_dir(user_id, day) / body_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def invalidate_today(user_id: str) -> None:
    folder = user_day_dir(user_id)
    if folder.exists():
        shutil.rmtree(folder)
