"""Env, paths, and model name."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

APP_NAME = "the-daily-bubble"
DEFAULT_MODEL = "gpt-4.1"
FALLBACK_MODEL = "gpt-4o"
LANGSMITH_PROJECT = "the-daily-bubble"
LOCAL_API_KEY_PLACEHOLDER = "not-needed"

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULTS_DIR = PACKAGE_DIR / "defaults"


def langsmith_api_key() -> str | None:
    key = os.getenv("LANGSMITH_API_KEY")
    return key if key else None


def load_env() -> None:
    load_dotenv()
    os.environ.setdefault("LANGSMITH_PROJECT", LANGSMITH_PROJECT)
    if langsmith_api_key():
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        return
    # Empty or missing key: do not attempt hosted LangSmith (avoids 401 noise).
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"


def dot_dir() -> Path:
    override = os.getenv("DAILY_BUBBLE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / f".{APP_NAME}"


def users_dir() -> Path:
    return dot_dir() / "users"


def agents_dir() -> Path:
    return dot_dir() / "agents"


def cache_root() -> Path:
    return dot_dir() / "cache"


def config_path() -> Path:
    return dot_dir() / "config.yaml"


def model_name(config_model: str | None = None) -> str:
    return os.getenv("OPENAI_MODEL") or config_model or DEFAULT_MODEL


def openai_api_key() -> str | None:
    key = os.getenv("OPENAI_API_KEY")
    return key if key else None


def openai_host() -> str | None:
    host = os.getenv("OPENAI_HOST")
    if host is None:
        return None
    host = host.strip()
    return host or None


def openai_base_url() -> str | None:
    """OpenAI-compatible chat completions root, or None for api.openai.com."""
    host = openai_host()
    if not host:
        return None
    return _normalize_openai_host(host)


def llm_configured() -> bool:
    return bool(openai_api_key() or openai_host())


def _normalize_openai_host(host: str) -> str:
    host = host.rstrip("/")
    if "://" not in host:
        host = f"http://{host}"
    path = urlparse(host).path
    if path in ("", "/"):
        host = f"{host}/v1"
    return host


def make_chat_model(model: str, *, temperature: float = 0.0):
    """ChatOpenAI pointed at OpenAI or an OPENAI_HOST-compatible endpoint."""
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {"model": model, "temperature": temperature}
    base_url = openai_base_url()
    if base_url:
        kwargs["base_url"] = base_url
        kwargs["api_key"] = openai_api_key() or LOCAL_API_KEY_PLACEHOLDER
    return ChatOpenAI(**kwargs)


load_env()
