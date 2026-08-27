"""Interview guides for creating user profiles and agent personas."""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml
from langchain.agents import create_agent
from langchain.tools import tool

from daily_bubble.config import make_chat_model
from daily_bubble.models import AgentPersona, UserProfile
from daily_bubble.profiles import (
    agent_exists,
    save_agent,
    save_user,
    slugify_id,
    user_exists,
)

GUIDE_NAME = "Guide"

USER_GUIDE_PROMPT = """
You are Guide, helping someone create a Daily Bubble user profile.

Collect:
- display name
- location (city and region — used for weather and local news)
- interests (topics they want in their news bubble)
- anti-interests (topics to filter out)

Be conversational. Ask one or two questions at a time. Do not dump a form.
If the latest user message is [begin], greet them and start. Do not mention [begin].

When you have enough, recap in a few lines, then call save_user_profile.
Pick a short lowercase id from their name (for example rob, sam). If that
id is taken, choose another or ask. After the tool succeeds, tell them they
are set and can start chatting.

Existing user ids: {existing}
""".strip()

USER_EDIT_PROMPT = """
You are Guide, helping someone edit an existing Daily Bubble user profile.
Keep the same profile id ({profile_id}) unless they explicitly want a new one.

Current profile:
{current}

Ask what they want to change. One or two questions at a time. Do not dump a form.
If the latest user message is [begin], greet them, recap the current profile
briefly, and ask what to change. Do not mention [begin].

When they confirm, call save_user_profile with the full updated fields
(including unchanged ones) and profile_id={profile_id}, overwrite=true.
After the tool succeeds, tell them the profile is updated.
""".strip()

AGENT_GUIDE_PROMPT = """
You are Guide, helping someone create a new Daily Bubble agent persona.

Collect:
- name
- speaking style (how they sound when they talk)
- persona (who they are and how they behave as a general-purpose assistant)

Built-in personas already exist: brief (newscaster), buddy (friendly),
sunny (positive), gray (pessimistic). A new persona should feel distinct.

Be conversational. Ask one or two questions at a time. Do not dump a form.
If the latest user message is [begin], greet them and start. Do not mention [begin].

When you have enough, recap, then call save_agent_profile with a lowercase id.
Do not overwrite a built-in id unless they explicitly insist (overwrite=true).
After the tool succeeds, tell them the new persona is ready.

Existing agent ids: {existing}
""".strip()

AGENT_EDIT_PROMPT = """
You are Guide, helping someone edit an existing Daily Bubble agent persona.
Keep the same id ({profile_id}) unless they explicitly want a new one.

Current persona:
{current}

Ask what they want to change. One or two questions at a time. Do not dump a form.
If the latest user message is [begin], greet them, recap the current persona
briefly, and ask what to change. Do not mention [begin].

When they confirm, call save_agent_profile with the full updated fields
(including unchanged ones) and profile_id={profile_id}, overwrite=true.
After the tool succeeds, tell them the persona is updated.
""".strip()


@dataclass
class GuideState:
    kind: str
    saved_user: UserProfile | None = None
    saved_agent: AgentPersona | None = None
    existing_ids: list[str] = field(default_factory=list)
    editing: bool = False
    editing_id: str = ""
    original_user: UserProfile | None = None
    original_agent: AgentPersona | None = None


def format_user_yaml(profile: UserProfile) -> str:
    return yaml.safe_dump(profile.model_dump(), sort_keys=False, allow_unicode=True).rstrip()


def format_agent_yaml(persona: AgentPersona) -> str:
    return yaml.safe_dump(persona.model_dump(), sort_keys=False, allow_unicode=True).rstrip()


def _as_list(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def make_save_user_profile(state: GuideState):
    @tool
    def save_user_profile(
        name: str,
        location: str,
        interests: list[str] | None = None,
        anti_interests: list[str] | None = None,
        profile_id: str = "",
        overwrite: bool = False,
    ) -> str:
        """Save the user profile after they confirm the recap.

        Args:
            name: Display name.
            location: City and region for weather and local news.
            interests: Topics they want in the news bubble.
            anti_interests: Topics to filter out.
            profile_id: Optional lowercase id. Derived from name if empty.
            overwrite: Replace an existing profile with the same id.
        """
        pid = (profile_id or slugify_id(name, fallback="user")).strip().lower()
        if state.editing_id:
            pid = state.editing_id
            overwrite = True
        if user_exists(pid) and not overwrite:
            return (
                f"Id '{pid}' is already taken. Ask the user for another id "
                "or set overwrite=true if they want to replace it."
            )
        profile = UserProfile(
            id=pid,
            name=name.strip(),
            location=location.strip(),
            interests=_as_list(interests),
            anti_interests=_as_list(anti_interests),
        )
        if not profile.name or not profile.location:
            return "Need both a name and a location before saving."
        try:
            save_user(profile, overwrite=overwrite)
        except (ValueError, FileExistsError) as exc:
            return str(exc)
        state.saved_user = profile
        return (
            f"Saved user profile '{profile.id}' for {profile.name} in "
            f"{profile.location}."
        )

    return save_user_profile


def make_save_agent_profile(state: GuideState):
    @tool
    def save_agent_profile(
        name: str,
        speaking_style: str,
        persona: str,
        profile_id: str = "",
        overwrite: bool = False,
    ) -> str:
        """Save a new agent persona after they confirm the recap.

        Args:
            name: Display name for the persona.
            speaking_style: How the agent should sound.
            persona: Who they are and how they should behave.
            profile_id: Optional lowercase id. Derived from name if empty.
            overwrite: Replace an existing persona with the same id.
        """
        pid = (profile_id or slugify_id(name, fallback="agent")).strip().lower()
        if state.editing_id:
            pid = state.editing_id
            overwrite = True
        if agent_exists(pid) and not overwrite:
            return (
                f"Id '{pid}' is already taken. Ask for another id or set "
                "overwrite=true only if they explicitly want to replace it."
            )
        agent = AgentPersona(
            id=pid,
            name=name.strip(),
            speaking_style=speaking_style.strip(),
            persona=persona.strip(),
        )
        if not agent.name or not agent.persona:
            return "Need both a name and a persona description before saving."
        try:
            save_agent(agent, overwrite=overwrite)
        except (ValueError, FileExistsError) as exc:
            return str(exc)
        state.saved_agent = agent
        return f"Saved agent persona '{agent.id}' ({agent.name})."

    return save_agent_profile


def build_guide_agent(kind: str, model: str, existing_ids: list[str], state: GuideState):
    if kind == "user":
        if state.editing and state.original_user is not None:
            prompt = USER_EDIT_PROMPT.format(
                profile_id=state.editing_id,
                current=format_user_yaml(state.original_user),
            )
        else:
            prompt = USER_GUIDE_PROMPT.format(
                existing=", ".join(existing_ids) or "(none)"
            )
        tools = [make_save_user_profile(state)]
    else:
        if state.editing and state.original_agent is not None:
            prompt = AGENT_EDIT_PROMPT.format(
                profile_id=state.editing_id,
                current=format_agent_yaml(state.original_agent),
            )
        else:
            prompt = AGENT_GUIDE_PROMPT.format(
                existing=", ".join(existing_ids) or "(none)"
            )
        tools = [make_save_agent_profile(state)]
    llm = make_chat_model(model, temperature=0.5)
    return create_agent(model=llm, tools=tools, system_prompt=prompt)
