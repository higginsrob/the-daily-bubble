"""Load, seed, and switch user / agent YAML profiles."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

from daily_bubble.config import (
    DEFAULTS_DIR,
    agents_dir,
    config_path,
    dot_dir,
    model_name,
    users_dir,
)
from daily_bubble.models import AgentPersona, AppConfig, UserProfile


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)


_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
DEFAULT_AGENT_ID = "buddy"


def slugify_id(name: str, fallback: str = "user") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:32]
    if not slug:
        slug = fallback
    if slug[0].isdigit():
        slug = f"{fallback[0]}-{slug}"[:32]
    return slug


def validate_id(value: str) -> str:
    slug = value.strip().lower()
    if not _ID_RE.fullmatch(slug):
        raise ValueError(
            f"Invalid id '{value}'. Use a lowercase letter, then letters, "
            "digits, hyphens, or underscores (max 32)."
        )
    return slug


def _copy_missing(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return
    for path in src.glob("*.yaml"):
        target = dest / path.name
        if not target.exists():
            shutil.copy2(path, target)


def ensure_home() -> None:
    """Create ~/.the-daily-bubble and seed built-in agent personas."""
    root = dot_dir()
    root.mkdir(parents=True, exist_ok=True)
    users_dir().mkdir(parents=True, exist_ok=True)
    _copy_missing(DEFAULTS_DIR / "agents", agents_dir())
    if not config_path().exists():
        seed = DEFAULTS_DIR / "config.yaml"
        if seed.exists():
            shutil.copy2(seed, config_path())
        else:
            save_config(AppConfig(active_user="", active_agent=DEFAULT_AGENT_ID))


def load_config() -> AppConfig:
    ensure_home()
    data = _read_yaml(config_path())
    cfg = AppConfig.model_validate(data)
    cfg.model = model_name(cfg.model)
    return cfg


def save_config(cfg: AppConfig) -> None:
    _write_yaml(config_path(), cfg.model_dump())


def list_users() -> list[UserProfile]:
    ensure_home()
    profiles = []
    for path in sorted(users_dir().glob("*.yaml")):
        profiles.append(_load_user_file(path))
    return profiles


def list_agents() -> list[AgentPersona]:
    ensure_home()
    personas = []
    for path in sorted(agents_dir().glob("*.yaml")):
        personas.append(_load_agent_file(path))
    return personas


def _load_user_file(path: Path) -> UserProfile:
    data = _read_yaml(path)
    data.setdefault("id", path.stem)
    return UserProfile.model_validate(data)


def _load_agent_file(path: Path) -> AgentPersona:
    data = _read_yaml(path)
    data.setdefault("id", path.stem)
    return AgentPersona.model_validate(data)


def load_user(user_id: str) -> UserProfile:
    user_id = validate_id(user_id)
    path = users_dir() / f"{user_id}.yaml"
    if not path.exists():
        known = ", ".join(p.id for p in list_users()) or "(none)"
        raise FileNotFoundError(f"Unknown user '{user_id}'. Known: {known}")
    return _load_user_file(path)


def load_agent(agent_id: str) -> AgentPersona:
    agent_id = validate_id(agent_id)
    path = agents_dir() / f"{agent_id}.yaml"
    if not path.exists():
        known = ", ".join(p.id for p in list_agents()) or "(none)"
        raise FileNotFoundError(f"Unknown agent '{agent_id}'. Known: {known}")
    return _load_agent_file(path)


def user_exists(user_id: str) -> bool:
    try:
        user_id = validate_id(user_id)
    except ValueError:
        return False
    return (users_dir() / f"{user_id}.yaml").exists()


def agent_exists(agent_id: str) -> bool:
    try:
        agent_id = validate_id(agent_id)
    except ValueError:
        return False
    return (agents_dir() / f"{agent_id}.yaml").exists()


def save_user(profile: UserProfile, overwrite: bool = False) -> Path:
    profile.id = validate_id(profile.id)
    path = users_dir() / f"{profile.id}.yaml"
    if path.exists() and not overwrite:
        raise FileExistsError(f"User '{profile.id}' already exists. Pick another id.")
    _write_yaml(path, profile.model_dump())
    return path


def save_agent(persona: AgentPersona, overwrite: bool = False) -> Path:
    persona.id = validate_id(persona.id)
    path = agents_dir() / f"{persona.id}.yaml"
    if path.exists() and not overwrite:
        raise FileExistsError(f"Agent '{persona.id}' already exists. Pick another id.")
    _write_yaml(path, persona.model_dump())
    return path


def resolve_agent_id(preferred: str | None = None) -> str:
    ids = [a.id for a in list_agents()]
    if preferred and preferred in ids:
        return preferred
    if DEFAULT_AGENT_ID in ids:
        return DEFAULT_AGENT_ID
    if ids:
        return ids[0]
    raise RuntimeError("No agent personas found under the agents directory.")


def set_active_user(user_id: str) -> AppConfig:
    user_id = validate_id(user_id)
    load_user(user_id)
    cfg = load_config()
    cfg.active_user = user_id
    save_config(cfg)
    return cfg


def set_active_agent(agent_id: str) -> AppConfig:
    agent_id = validate_id(agent_id)
    load_agent(agent_id)
    cfg = load_config()
    cfg.active_agent = agent_id
    save_config(cfg)
    return cfg
