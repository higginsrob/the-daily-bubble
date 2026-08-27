"""Interactive REPL and slash commands."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import typer
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from daily_bubble.agent import build_agent, content_to_text, stream_chat
from daily_bubble.chat_render import CHAT_THEME, ChatDisplay
from daily_bubble.config import llm_configured, load_env
from daily_bubble.guide import (
    GUIDE_NAME,
    GuideState,
    build_guide_agent,
    format_agent_yaml,
    format_user_yaml,
)
from daily_bubble.models import AgentPersona, ArticleCache, UserProfile, WeatherCache
from daily_bubble.news.cache import (
    cache_is_fresh,
    invalidate_today,
    load_articles,
    load_weather,
)
from daily_bubble.news.pipeline import run_ingest
from daily_bubble.profiles import (
    list_agents,
    list_users,
    load_agent,
    load_config,
    load_user,
    resolve_agent_id,
    save_user,
    set_active_agent,
    set_active_user,
    slugify_id,
    user_exists,
)
from daily_bubble.weather import weather_oneliner

app = typer.Typer(
    add_completion=False,
    pretty_exceptions_show_locals=False,
    help="The Daily Bubble — a CLI assistant with a daily workspace map.",
)
console = Console(theme=CHAT_THEME, highlight=False, soft_wrap=False)
display = ChatDisplay(console)

HELP_TEXT = """\
The Daily Bubble is a general-purpose assistant whose bubble is your world:
profile, weather, and a ranked news index.

Commands:
  /help                 this message
  /clear                clear the screen, reset the chat session, and reprint the header
  /refresh              clear today's cache and re-run search + weather
  /profile              list user profiles
  /profile <id>         switch user (rebuilds the workspace map)
  /agent                list agent personas
  /agent <id>           switch persona (rebuilds the system prompt)
  /show profile [id]    print a user profile (current if no id)
  /show agent [id]      print an agent persona (current if no id)
  /new-user-profile     interview to create a user profile
  /new-agent-profile    interview to create an agent persona
  /edit profile [id]    interview to edit a user profile
  /edit agent [id]      interview to edit an agent persona
  /cancel               leave a profile interview
  /quit  /exit          leave
"""


@dataclass
class Session:
    user: UserProfile | None
    persona: AgentPersona
    articles: ArticleCache
    weather: WeatherCache | None
    model: str
    graph: Any = None
    messages: list = field(default_factory=list)
    mode: str = "chat"
    guide: GuideState | None = None
    guide_graph: Any = None
    guide_messages: list = field(default_factory=list)

    def rebuild_agent(self) -> None:
        if self.user is None:
            self.graph = None
            return
        self.graph = build_agent(
            self.user, self.persona, self.weather, self.articles, self.model
        )


def _empty_articles(user_id: str = "") -> ArticleCache:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return ArticleCache(fetched_at=now, user_id=user_id)


def _mark(items: list[str], active: str) -> str:
    parts = []
    for item in items:
        parts.append(f"{item}*" if item == active else item)
    return ", ".join(parts) if parts else "(none)"


def _banner(session: Session) -> None:
    if session.user is None:
        line = Text.assemble(
            ("The Daily Bubble", "bold"),
            f"  ·  no user profile yet  ·  agent {session.persona.name}",
        )
    else:
        weather = weather_oneliner(session.weather)
        line = Text.assemble(
            ("The Daily Bubble", "bold"),
            "  ·  ",
            f"{session.user.name} × {session.persona.name}",
            "  ·  ",
            weather,
            "  ·  ",
            f"{len(session.articles.articles)} articles",
        )
    _print_truncated(line)
    console.print()


def _print_truncated(text: Text) -> None:
    """Print one line; if it is wider than the terminal, keep the start and end with ..."""
    width = max(1, shutil.get_terminal_size().columns)
    if cell_len(text.plain) > width:
        ellipsis = "..."
        keep = max(0, width - cell_len(ellipsis))
        text = text.copy()
        text.truncate(keep, overflow="crop")
        while text.plain.endswith(" "):
            text.right_crop()
        text.append(ellipsis)
        if cell_len(text.plain) > width:
            text.truncate(width, overflow="crop")
    console.print(text, overflow="crop", no_wrap=True, crop=True, end="\n")


def _show_header(session: Session) -> None:
    display.invalidate()
    console.clear()
    _banner(session)
    if session.mode == "chat" and session.user is not None:
        console.print("[dim]Type /help for commands. News is in the workspace map.[/dim]")
        console.print()


def _input_prompt(session: Session) -> str:
    name = session.user.name if session.user is not None else "you"
    return f"{name} ❯ "


def _progress(message: str) -> None:
    console.print(f"[dim][ingest] {message}[/dim]")


def _ensure_cache(user: UserProfile, model: str, force: bool = False) -> tuple[ArticleCache, WeatherCache]:
    if force:
        invalidate_today(user.id)
    if not force and cache_is_fresh(user.id):
        return load_articles(user.id), load_weather(user.id)
    if not llm_configured():
        console.print(
            "[yellow]No OPENAI_API_KEY or OPENAI_HOST — skipping news ingest. "
            "Weather still loads.[/yellow]"
        )
        from daily_bubble.news.cache import save_articles, save_weather
        from daily_bubble.weather import fetch_weather, unavailable_weather

        try:
            weather = fetch_weather(user.location)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Weather unavailable ({exc}).[/yellow]")
            weather = unavailable_weather(user.location)
        save_weather(user.id, weather)
        empty = ArticleCache(fetched_at=weather.fetched_at, user_id=user.id)
        save_articles(empty)
        return empty, weather
    return run_ingest(user, model, progress=_progress)


def _activate_user(session: Session, user: UserProfile) -> None:
    set_active_user(user.id)
    session.user = user
    session.articles, session.weather = _ensure_cache(user, session.model)
    session.messages = []
    if llm_configured():
        session.rebuild_agent()
    _banner(session)


def _activate_agent(session: Session, persona: AgentPersona) -> None:
    set_active_agent(persona.id)
    session.persona = persona
    session.messages = []
    if llm_configured() and session.user is not None:
        session.rebuild_agent()
    _banner(session)


def _load_session() -> Session:
    cfg = load_config()
    agent_id = resolve_agent_id(cfg.active_agent)
    if agent_id != cfg.active_agent:
        set_active_agent(agent_id)
    persona = load_agent(agent_id)
    users = list_users()
    if not users:
        return Session(
            user=None,
            persona=persona,
            articles=_empty_articles(),
            weather=None,
            model=cfg.model,
        )
    user_id = cfg.active_user
    if user_id not in {u.id for u in users}:
        user_id = users[0].id
        set_active_user(user_id)
    user = load_user(user_id)
    articles, weather = _ensure_cache(user, cfg.model)
    session = Session(
        user=user,
        persona=persona,
        articles=articles,
        weather=weather,
        model=cfg.model,
    )
    if llm_configured():
        session.rebuild_agent()
    return session


def _end_guide(session: Session) -> None:
    session.mode = "chat"
    session.guide = None
    session.guide_graph = None
    session.guide_messages = []


def _wizard_user_profile(existing: UserProfile | None = None) -> UserProfile | None:
    if existing is None:
        console.print("[dim]No API key — a short setup instead of an interview.[/dim]")
    else:
        console.print("[dim]No API key — edit fields directly. Blank keeps the current value.[/dim]")
    try:
        name = input(f"Your name [{existing.name if existing else ''}]: ").strip()
        location = input(
            f"Location (city, region) [{existing.location if existing else ''}]: "
        ).strip()
        interest_hint = ", ".join(existing.interests) if existing else ""
        anti_hint = ", ".join(existing.anti_interests) if existing else ""
        interests = input(f"Interests (comma-separated) [{interest_hint}]: ").strip()
        anti = input(f"Things to skip (comma-separated) [{anti_hint}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None
    name = name or (existing.name if existing else "")
    location = location or (existing.location if existing else "")
    if not name or not location:
        console.print("[red]Name and location are required.[/red]")
        return None
    if existing is not None:
        profile_id = existing.id
        parsed_interests = (
            [p.strip() for p in interests.split(",") if p.strip()]
            if interests
            else list(existing.interests)
        )
        parsed_anti = (
            [p.strip() for p in anti.split(",") if p.strip()]
            if anti
            else list(existing.anti_interests)
        )
    else:
        profile_id = slugify_id(name)
        if user_exists(profile_id):
            suffix = 2
            while user_exists(f"{profile_id}-{suffix}"):
                suffix += 1
            profile_id = f"{profile_id}-{suffix}"
        parsed_interests = [p.strip() for p in interests.split(",") if p.strip()]
        parsed_anti = [p.strip() for p in anti.split(",") if p.strip()]
    profile = UserProfile(
        id=profile_id,
        name=name,
        location=location,
        interests=parsed_interests,
        anti_interests=parsed_anti,
    )
    save_user(profile, overwrite=existing is not None)
    return profile


def _start_guide(
    session: Session,
    kind: str,
    *,
    editing_user: UserProfile | None = None,
    editing_agent: AgentPersona | None = None,
) -> None:
    if not llm_configured():
        if kind == "user":
            profile = _wizard_user_profile(existing=editing_user)
            if profile is not None:
                if editing_user is not None:
                    console.print(f"[green]Updated user '{profile.id}'.[/green]")
                    _apply_user_edit(session, editing_user, profile)
                else:
                    _activate_user(session, profile)
            return
        console.print("[red]OPENAI_API_KEY or OPENAI_HOST is required to create or edit an agent persona.[/red]")
        return
    existing = (
        [p.id for p in list_users()] if kind == "user" else [p.id for p in list_agents()]
    )
    state = GuideState(kind=kind, existing_ids=existing)
    if editing_user is not None:
        state.editing = True
        state.editing_id = editing_user.id
        state.original_user = editing_user
    if editing_agent is not None:
        state.editing = True
        state.editing_id = editing_agent.id
        state.original_agent = editing_agent
    session.mode = f"{kind}_guide"
    session.guide = state
    session.guide_graph = build_guide_agent(kind, session.model, existing, state)
    session.guide_messages = []
    if state.editing:
        label = f"edit {kind} '{state.editing_id}'"
    else:
        label = "user profile" if kind == "user" else "agent persona"
    console.print(f"[dim]Profile interview — {label}. /cancel to stop.[/dim]")
    _chat(session, "[begin]")


def _apply_user_edit(
    session: Session,
    original: UserProfile | None,
    profile: UserProfile,
) -> None:
    if session.user is None or session.user.id != profile.id:
        return
    session.user = profile
    bubble_changed = original is None or (
        original.location != profile.location
        or original.interests != profile.interests
        or original.anti_interests != profile.anti_interests
    )
    if bubble_changed:
        console.print(
            "[dim]Location or interests changed — refreshing today's bubble…[/dim]"
        )
        session.articles, session.weather = _ensure_cache(
            profile, session.model, force=True
        )
    if llm_configured():
        session.rebuild_agent()
    _banner(session)


def _apply_agent_edit(session: Session, persona: AgentPersona) -> None:
    if session.persona.id != persona.id:
        return
    session.persona = persona
    if llm_configured() and session.user is not None:
        session.rebuild_agent()
    _banner(session)


def _finish_guide_if_saved(session: Session) -> None:
    if session.guide is None:
        return
    if session.guide.saved_user is not None or session.guide.saved_agent is not None:
        display.invalidate()
    if session.guide.saved_user is not None:
        profile = session.guide.saved_user
        editing = session.guide.editing
        original = session.guide.original_user
        _end_guide(session)
        if editing:
            console.print(f"[green]Updated user '{profile.id}'.[/green]")
            _apply_user_edit(session, original, profile)
            return
        console.print(f"[green]Saved user '{profile.id}'. Building today's bubble…[/green]")
        _activate_user(session, profile)
        return
    if session.guide.saved_agent is not None:
        persona = session.guide.saved_agent
        editing = session.guide.editing
        _end_guide(session)
        if editing:
            console.print(f"[green]Updated agent '{persona.id}'.[/green]")
            _apply_agent_edit(session, persona)
            return
        console.print(f"[green]Saved agent '{persona.id}'.[/green]")
        _activate_agent(session, persona)


def _cmd_profile(session: Session, arg: str) -> None:
    users = list_users()
    if not arg:
        active = session.user.id if session.user else ""
        console.print(f"users: {_mark([p.id for p in users], active)}")
        if not users:
            console.print("[dim]None yet. Run /new-user-profile.[/dim]")
        return
    try:
        set_active_user(arg)
        session.user = load_user(arg)
        session.articles, session.weather = _ensure_cache(session.user, session.model)
        session.messages = []
        if llm_configured():
            session.rebuild_agent()
        _banner(session)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")


def _cmd_agent(session: Session, arg: str) -> None:
    if not arg:
        ids = [p.id for p in list_agents()]
        console.print(f"agents: {_mark(ids, session.persona.id)}")
        return
    try:
        set_active_agent(arg)
        session.persona = load_agent(arg)
        session.messages = []
        if llm_configured() and session.user is not None:
            session.rebuild_agent()
        _banner(session)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")


def _cmd_refresh(session: Session) -> None:
    if session.user is None:
        console.print("[red]Create a user profile first (/new-user-profile).[/red]")
        return
    if not llm_configured():
        console.print("[red]OPENAI_API_KEY or OPENAI_HOST is required for /refresh.[/red]")
        return
    session.articles, session.weather = _ensure_cache(
        session.user, session.model, force=True
    )
    session.rebuild_agent()
    _banner(session)


def _cmd_show(session: Session, arg: str) -> None:
    parts = arg.split(maxsplit=1)
    if not parts or parts[0].lower() not in {"profile", "user", "agent"}:
        console.print("[dim]Usage: /show profile [id]  or  /show agent [id][/dim]")
        return
    kind = parts[0].lower()
    ident = parts[1].strip() if len(parts) > 1 else ""
    try:
        if kind in {"profile", "user"}:
            if ident:
                profile = load_user(ident)
            elif session.user is not None:
                profile = session.user
            else:
                console.print("[red]No current user profile.[/red]")
                return
            console.print(f"[bold]user:{profile.id}[/bold]")
            console.print(format_user_yaml(profile))
            return
        if ident:
            persona = load_agent(ident)
        else:
            persona = session.persona
        console.print(f"[bold]agent:{persona.id}[/bold]")
        console.print(format_agent_yaml(persona))
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")


def _cmd_edit(session: Session, arg: str) -> None:
    parts = arg.split(maxsplit=1)
    if not parts or parts[0].lower() not in {"profile", "user", "agent"}:
        console.print("[dim]Usage: /edit profile [id]  or  /edit agent [id][/dim]")
        return
    kind = parts[0].lower()
    ident = parts[1].strip() if len(parts) > 1 else ""
    try:
        if kind in {"profile", "user"}:
            if ident:
                target = load_user(ident)
            elif session.user is not None:
                target = session.user
            else:
                console.print("[red]No current user. Use /edit profile <id>.[/red]")
                return
            _start_guide(session, "user", editing_user=target)
            return
        if ident:
            target_agent = load_agent(ident)
        else:
            target_agent = session.persona
        _start_guide(session, "agent", editing_agent=target_agent)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")


def _cmd_clear(session: Session) -> None:
    if session.mode == "chat":
        session.messages = []
        if llm_configured() and session.user is not None:
            session.rebuild_agent()
    _show_header(session)
    if session.mode != "chat":
        console.print("[dim]Profile interview still in progress. /cancel to stop.[/dim]")


def _cmd_cancel(session: Session) -> None:
    if session.mode == "chat":
        console.print("[dim]Nothing to cancel.[/dim]")
        return
    if session.user is None and session.mode == "user_guide":
        console.print("[yellow]A user profile is required to chat. Interview still open.[/yellow]")
        return
    _end_guide(session)
    console.print("[dim]Left the interview.[/dim]")
    _banner(session)


def _handle_slash(session: Session, line: str) -> bool:
    """Return True if the REPL should exit."""
    display.invalidate()
    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in {"/quit", "/exit"}:
        return True
    if cmd == "/help":
        console.print(HELP_TEXT)
        return False
    if cmd == "/cancel":
        _cmd_cancel(session)
        return False
    if cmd == "/clear":
        _cmd_clear(session)
        return False
    if cmd == "/show":
        _cmd_show(session, arg)
        return False
    if cmd == "/edit":
        _cmd_edit(session, arg)
        return False
    if cmd in {"/new-user-profile", "/new-user"}:
        _start_guide(session, "user")
        return False
    if cmd in {"/new-agent-profile", "/new-agent"}:
        _start_guide(session, "agent")
        return False
    if cmd == "/refresh":
        _cmd_refresh(session)
        return False
    if cmd == "/profile":
        _cmd_profile(session, arg)
        return False
    if cmd == "/agent":
        _cmd_agent(session, arg)
        return False
    console.print("[dim]Unknown command.[/dim]")
    console.print(HELP_TEXT)
    return False


def _chat(session: Session, text: str) -> None:
    in_guide = session.mode != "chat"
    graph = session.guide_graph if in_guide else session.graph
    if graph is None:
        if in_guide or session.user is None:
            console.print("[red]OPENAI_API_KEY or OPENAI_HOST is required for the profile interview.[/red]")
        else:
            console.print("[red]OPENAI_API_KEY or OPENAI_HOST is required to chat.[/red]")
        console.print()
        return
    if in_guide:
        payload = list(session.guide_messages) + [{"role": "user", "content": text}]
        speaker = GUIDE_NAME
        user_id = session.user.id if session.user else "new"
        agent_id = "guide"
        tags = ["guide"]
        run_name = "guide"
    else:
        payload = list(session.messages) + [{"role": "user", "content": text}]
        speaker = session.persona.name
        user_id = session.user.id if session.user else "none"
        agent_id = session.persona.id
        tags = ["chat"]
        run_name = "chat"

    accumulated = ""
    try:
        with display.streaming(speaker) as stream:
            for kind, data in stream_chat(
                graph,
                payload,
                user_id=user_id,
                agent_id=agent_id,
                model=session.model,
                tags=tags,
                run_name=run_name,
            ):
                if kind == "token":
                    accumulated += data
                    stream.feed(data)
                elif kind == "tool":
                    name = data.get("name")
                    args = data.get("args") or {}
                    stream.tool(name, args)
                elif kind == "done":
                    history = data or (
                        payload + [{"role": "assistant", "content": accumulated}]
                    )
                    if in_guide:
                        session.guide_messages = history
                    else:
                        session.messages = history
                    if not accumulated and history:
                        last = history[-1]
                        fallback = content_to_text(getattr(last, "content", None))
                        if fallback:
                            stream.feed(fallback)
    except Exception as exc:  # noqa: BLE001
        display.invalidate()
        console.print(f"\n[red]{exc}[/red]")
        console.print()
        return
    if in_guide:
        _finish_guide_if_saved(session)


def run_repl() -> None:
    load_env()
    display.attach()
    console.clear()
    session = _load_session()
    _show_header(session)
    if session.user is None:
        _start_guide(session, "user")
        if session.user is None and session.mode == "chat":
            console.print("[red]Need a user profile to continue.[/red]")
            return
    while True:
        try:
            line = display.read_line(_input_prompt(session)).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not line:
            continue
        console.print()
        if line.startswith("/"):
            if _handle_slash(session, line):
                break
            continue
        if session.mode == "chat" and session.user is None:
            console.print("[dim]Create a profile first: /new-user-profile[/dim]")
            console.print()
            continue
        _chat(session, line)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Start the Daily Bubble REPL."""
    if ctx.invoked_subcommand is not None:
        return
    run_repl()


if __name__ == "__main__":
    app()
