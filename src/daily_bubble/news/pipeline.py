"""Ingest loop: search → fetch → rerank → persist, plus weather."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from langsmith import get_current_run_tree, traceable

from daily_bubble.models import Article, ArticleCache, UserProfile, WeatherCache
from daily_bubble.news.cache import (
    save_articles,
    save_weather,
    write_body,
)
from daily_bubble.news.fetch import fetch_article
from daily_bubble.news.rerank import rerank_and_blurb
from daily_bubble.news.search import search_news
from daily_bubble.weather import fetch_weather

MAX_FETCH = 12
Progress = Callable[[str], None]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _dedupe_hits(hits):
    seen: set[str] = set()
    out = []
    for hit in hits:
        key = hit.url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


@traceable(name="ingest", tags=["ingest"])
def run_ingest(
    user: UserProfile,
    model: str,
    progress: Progress | None = None,
) -> tuple[ArticleCache, WeatherCache]:
    log = progress or (lambda _msg: None)

    queries = list(user.interests)
    queries.append(f"top local news stories {user.location}")

    hits = []
    for query in queries:
        log(f"searching: {query}")
        try:
            hits.extend(search_news(query))
        except Exception as exc:  # noqa: BLE001 — keep ingest moving in a demo
            log(f"search failed ({query}): {exc}")

    hits = _dedupe_hits(hits)[:MAX_FETCH]
    log(f"fetching {len(hits)} pages")
    fetched = []
    for hit in hits:
        body = fetch_article(hit.url)
        fetched.append((hit, body))

    log("reranking")
    kept, dropped = rerank_and_blurb(fetched, user, model=model)

    articles: list[Article] = []
    for i, row in enumerate(kept, start=1):
        article_id = f"a{i}"
        body = row.body or row.hit.snippet or row.hit.title
        body_path = write_body(user.id, article_id, body)
        excerpt = (row.hit.snippet or body)[:280]
        articles.append(
            Article(
                id=article_id,
                title=row.hit.title,
                url=row.hit.url,
                score=row.score,
                reason=row.reason,
                spoken_description=row.spoken_description,
                excerpt=excerpt,
                body_path=body_path,
            )
        )

    article_cache = ArticleCache(
        fetched_at=_now_iso(),
        user_id=user.id,
        articles=articles,
        dropped=dropped,
    )
    save_articles(article_cache)

    log("weather")
    try:
        weather = fetch_weather(user.location)
    except Exception as exc:  # noqa: BLE001
        log(f"weather failed: {exc}")
        from daily_bubble.weather import unavailable_weather

        weather = unavailable_weather(user.location)
    save_weather(user.id, weather)

    tree = get_current_run_tree()
    if tree is not None:
        tree.metadata["user_id"] = user.id
        tree.metadata["model"] = model
        tree.metadata["article_count"] = len(articles)
        tree.metadata["dropped_count"] = len(dropped)

    return article_cache, weather
