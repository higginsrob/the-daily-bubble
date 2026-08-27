"""LLM structured scoring + spoken blurbs. Drop score < 0."""

from __future__ import annotations

from langsmith import get_current_run_tree, traceable
from pydantic import BaseModel, Field

from daily_bubble.config import make_chat_model
from daily_bubble.models import DroppedArticle, SearchHit, UserProfile

RERANK_INSTRUCTIONS = """You score news articles for one person's daily briefing.

User: {name} in {location}
Interests (boost): {interests}
Anti-interests (penalize): {anti_interests}

For each article, return:
- index: the article's index from the input
- score: float from -1.0 to 1.0
  - above 0.3: strong overlap with interests
  - 0 to 0.3: weak or tangential
  - near 0: off-topic
  - below 0: anti-interests dominate, or it is junk / celebrity / betting spam
- reason: short and concrete (what matched or clashed)
- spoken_description: 1-2 conversational sentences meant to be read aloud.
  No headline voice, no URL, no "as an AI", no "this article".
  Speak as if catching a friend up.

Score anti-interest matches below zero even if they are also slightly interesting.
Score every index you are given.
"""


class RankedItem(BaseModel):
    index: int
    score: float
    reason: str
    spoken_description: str


class RankedBatch(BaseModel):
    items: list[RankedItem] = Field(default_factory=list)


class RankedArticle(BaseModel):
    hit: SearchHit
    body: str
    score: float
    reason: str
    spoken_description: str


def _fallback_spoken(hit: SearchHit, body: str) -> str:
    text = (hit.snippet or body or hit.title).strip().replace("\n", " ")
    if len(text) > 240:
        text = text[:237].rstrip() + "..."
    return text


def _format_batch(items: list[tuple[SearchHit, str]]) -> str:
    blocks = []
    for i, (hit, body) in enumerate(items):
        excerpt = (body or hit.snippet or "")[:800]
        blocks.append(
            f"[{i}] title: {hit.title}\n"
            f"url: {hit.url}\n"
            f"text: {excerpt}"
        )
    return "\n\n".join(blocks)


@traceable(name="rerank", tags=["rerank"])
def rerank_and_blurb(
    items: list[tuple[SearchHit, str]],
    user: UserProfile,
    model: str,
) -> tuple[list[RankedArticle], list[DroppedArticle]]:
    """Score articles; keep score >= 0. Returns (kept, dropped)."""
    if not items:
        return [], []

    llm = make_chat_model(model, temperature=0).with_structured_output(RankedBatch)
    prompt = RERANK_INSTRUCTIONS.format(
        name=user.name,
        location=user.location,
        interests="; ".join(user.interests) or "(none)",
        anti_interests="; ".join(user.anti_interests) or "(none)",
    )
    result = llm.invoke(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": _format_batch(items)},
        ]
    )
    by_index = {item.index: item for item in result.items}

    kept: list[RankedArticle] = []
    dropped: list[DroppedArticle] = []
    scores: dict[str, float] = {}

    for i, (hit, body) in enumerate(items):
        ranked = by_index.get(i)
        if ranked is None:
            score = 0.0
            reason = "unscored; kept at zero"
            spoken = _fallback_spoken(hit, body)
        else:
            score = float(ranked.score)
            reason = ranked.reason
            spoken = ranked.spoken_description.strip() or _fallback_spoken(hit, body)
        scores[hit.url] = score
        if score < 0:
            dropped.append(
                DroppedArticle(
                    title=hit.title,
                    url=hit.url,
                    score=score,
                    reason=reason,
                )
            )
            continue
        kept.append(
            RankedArticle(
                hit=hit,
                body=body,
                score=score,
                reason=reason,
                spoken_description=spoken,
            )
        )

    kept.sort(key=lambda row: row.score, reverse=True)
    tree = get_current_run_tree()
    if tree is not None:
        tree.metadata["user_id"] = user.id
        tree.metadata["scores"] = scores
        tree.metadata["kept"] = len(kept)
        tree.metadata["dropped"] = len(dropped)
    return kept, dropped
