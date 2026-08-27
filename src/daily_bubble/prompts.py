"""Persona extras and news-trigger instructions (not a classifier)."""

from daily_bubble.models import AgentPersona, Article, UserProfile, WeatherCache
from daily_bubble.workspace import build_workspace_map

WORKSPACE_INSTRUCTIONS = """
You have a workspace map of the user's world: who they are, today's weather,
and a ranked index of news. This is an index, not full article text.

How to use it:
- Help with anything. You are a general-purpose assistant, not a news bot.
- Weather questions: answer from <weather>. Do not invent forecasts.
- News questions such as "what's new?", "what's up?", "anything in the news?",
  "brief me", or similar: brief from the <articles> index. Prefer each
  article's <spoken> copy over titles. Lead with higher scores. Do not dump
  URLs unless the user asks for sources. Do not mention scores unless asked.
- When the user wants more depth on a story, call read_article with that
  article's id (a1, a2, ...). Then summarize in your speaking style.
- Do not volunteer a news dump unprompted. A greeting can mention notable
  weather in passing, nothing more.
- Do not claim you searched the live web in this chat. Today's bubble was
  precomputed. If they want a fresh crawl, tell them to run /refresh.
- If the index is empty, say so plainly and suggest /refresh.
- Stay in the speaking style given above. Keep answers concise unless asked
  to go long.
""".strip()


def build_system_prompt(
    user: UserProfile,
    agent: AgentPersona,
    weather: WeatherCache | None,
    articles: list[Article],
) -> str:
    persona = agent.persona.strip()
    style = agent.speaking_style.strip()
    parts = [persona]
    if style:
        parts.append(f"Speaking style: {style}")
    parts.append(WORKSPACE_INSTRUCTIONS)
    parts.append(build_workspace_map(user, agent, weather, articles))
    return "\n\n".join(parts)
