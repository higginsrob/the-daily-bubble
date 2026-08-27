"""LangChain agent factory, read_article tool, and streaming chat."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from langchain.agents import create_agent
from langchain.tools import tool

from daily_bubble.config import make_chat_model
from daily_bubble.models import (
    AgentPersona,
    Article,
    ArticleCache,
    UserProfile,
    WeatherCache,
)
from daily_bubble.news.cache import read_body
from daily_bubble.prompts import build_system_prompt


def _article_lookup(cache: ArticleCache) -> dict[str, Article]:
    return {article.id: article for article in cache.articles}


def make_read_article(user_id: str, cache: ArticleCache):
    lookup = _article_lookup(cache)

    @tool
    def read_article(article_id: str) -> str:
        """Load the full cached text of an indexed article by id (for example a1)."""
        article = lookup.get(article_id.strip())
        if article is None:
            known = ", ".join(lookup) or "(none)"
            return f"Unknown article id '{article_id}'. Known ids: {known}"
        body = read_body(user_id, article.body_path) if article.body_path else ""
        if not body:
            body = article.excerpt or article.spoken_description
        return (
            f"Title: {article.title}\n"
            f"URL: {article.url}\n"
            f"Score: {article.score:.2f}\n\n"
            f"{body}"
        )

    return read_article


def build_agent(
    user: UserProfile,
    persona: AgentPersona,
    weather: WeatherCache | None,
    articles: ArticleCache,
    model: str,
):
    system_prompt = build_system_prompt(user, persona, weather, articles.articles)
    llm = make_chat_model(model, temperature=0.4)
    return create_agent(
        model=llm,
        tools=[make_read_article(user.id, articles)],
        system_prompt=system_prompt,
    )


def content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in ("text", "output_text"):
                parts.append(str(block.get("text") or ""))
            elif hasattr(block, "text"):
                parts.append(getattr(block, "text") or "")
        return "".join(parts)
    return ""


def stream_chat(
    graph,
    messages: list,
    *,
    user_id: str,
    agent_id: str,
    model: str,
    tags: list[str] | None = None,
    run_name: str = "chat",
) -> Iterator[tuple[str, Any]]:
    """Yield ('token', text) and ('tool', {name, args}) events."""
    config = {
        "run_name": run_name,
        "tags": tags or ["chat"],
        "metadata": {
            "user_id": user_id,
            "agent_id": agent_id,
            "model": model,
        },
        "recursion_limit": 25,
    }
    final_messages = None
    for mode, data in graph.stream(
        {"messages": messages},
        config=config,
        stream_mode=["messages", "updates", "values"],
    ):
        if mode == "messages":
            token, _metadata = data
            token_name = type(token).__name__
            if "AIMessageChunk" in token_name:
                text = content_to_text(getattr(token, "content", None))
                if text:
                    yield ("token", text)
            continue

        if mode == "values" and isinstance(data, dict):
            final_messages = data.get("messages")
            continue

        if mode != "updates" or not isinstance(data, dict):
            continue
        for _node, update in data.items():
            if not isinstance(update, dict):
                continue
            chunk_messages = update.get("messages") or []
            if not chunk_messages:
                continue
            last = chunk_messages[-1]
            tool_calls = getattr(last, "tool_calls", None) or []
            for call in tool_calls:
                if isinstance(call, dict):
                    yield ("tool", {"name": call.get("name"), "args": call.get("args")})
                else:
                    yield (
                        "tool",
                        {
                            "name": getattr(call, "name", None),
                            "args": getattr(call, "args", None),
                        },
                    )
    yield ("done", final_messages)
