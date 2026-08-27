"""Build the compact XML workspace map injected into the system prompt."""

from __future__ import annotations

from datetime import datetime

from daily_bubble.models import AgentPersona, Article, UserProfile, WeatherCache
from daily_bubble.weather import weather_oneliner


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _join(items: list[str]) -> str:
    return "; ".join(items) if items else "(none)"


def _forecast_lines(weather: WeatherCache | None) -> str:
    if not weather or not weather.forecast:
        return "    (none)"
    lines = []
    for day in weather.forecast[1:]:
        parsed = datetime.fromisoformat(day.date).strftime("%a")
        lines.append(
            f"    {parsed}: {day.summary}, high {day.high_f:.0f} / low {day.low_f:.0f}"
        )
    return "\n".join(lines) if lines else "    (none)"


def build_workspace_map(
    user: UserProfile,
    agent: AgentPersona,
    weather: WeatherCache | None,
    articles: list[Article],
) -> str:
    today = weather_oneliner(weather) if weather else "unavailable"
    article_blocks = []
    for article in articles:
        article_blocks.append(
            "    <article "
            f'id="{_esc(article.id)}" score="{article.score:.2f}" '
            f'url="{_esc(article.url)}">\n'
            f"      <title>{_esc(article.title)}</title>\n"
            f"      <spoken>{_esc(article.spoken_description)}</spoken>\n"
            "    </article>"
        )
    articles_xml = "\n".join(article_blocks) if article_blocks else "    (none)"

    return f"""<workspace>
  <agent name="{_esc(agent.name)}" />
  <user>
    <name>{_esc(user.name)}</name>
    <location>{_esc(user.location)}</location>
    <interests>{_esc(_join(user.interests))}</interests>
    <anti_interests>{_esc(_join(user.anti_interests))}</anti_interests>
  </user>
  <weather location="{_esc(weather.location if weather else user.location)}">
    <today>{_esc(today)}</today>
    <forecast>
{_forecast_lines(weather)}
    </forecast>
  </weather>
  <articles>
{articles_xml}
  </articles>
</workspace>"""
