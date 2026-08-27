"""Shared data shapes for profiles, weather, and the article index."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    id: str
    name: str
    location: str
    interests: list[str] = Field(default_factory=list)
    anti_interests: list[str] = Field(default_factory=list)


class AgentPersona(BaseModel):
    id: str
    name: str
    speaking_style: str = ""
    persona: str = ""


class AppConfig(BaseModel):
    active_user: str = ""
    active_agent: str = "buddy"
    model: str = "gpt-4.1"


class WeatherCurrent(BaseModel):
    summary: str
    temp_f: float
    wind_mph: float = 0.0


class WeatherDay(BaseModel):
    date: str
    high_f: float
    low_f: float
    summary: str


class WeatherCache(BaseModel):
    fetched_at: str
    location: str
    latitude: float
    longitude: float
    current: WeatherCurrent
    forecast: list[WeatherDay] = Field(default_factory=list)


class SearchHit(BaseModel):
    title: str
    url: str
    snippet: str = ""


class Article(BaseModel):
    id: str
    title: str
    url: str
    score: float
    reason: str = ""
    spoken_description: str
    excerpt: str = ""
    body_path: str = ""


class DroppedArticle(BaseModel):
    title: str
    url: str
    score: float
    reason: str = ""


class ArticleCache(BaseModel):
    fetched_at: str
    user_id: str
    articles: list[Article] = Field(default_factory=list)
    dropped: list[DroppedArticle] = Field(default_factory=list)
