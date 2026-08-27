# The Daily Bubble

A CLI assistant whose **bubble** is your world: a user profile, local weather, and a ranked news index.

The interesting part is not search. It is a **workspace map** — a compact, regenerable XML block in the system prompt — so the model talks against a live index of the day instead of stuffing full articles into context.

This is a local, single-user CLI. Profiles and caches live under `~/.the-daily-bubble/` and should not be committed. Licensed under MIT.

## Quick start

### Install and configure

Python 3.12+ is required. [uv](https://docs.astral.sh/uv/) is the fastest path:

```bash
cp .env.example .env   # add OPENAI_API_KEY; the rest is optional
uv sync
uv run daily-bubble
```

Without uv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
daily-bubble
```

`python -m daily_bubble` works too.

| Variable | Required | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | for chat, rerank, and guided profile interviews | News search uses Google News RSS (no extra key) |
| `OPENAI_HOST` | no | OpenAI-compatible endpoint (Ollama, LM Studio, vLLM). When set, a real API key is optional |
| `OPENAI_MODEL` | no | Overrides `~/.the-daily-bubble/config.yaml`; default `gpt-4.1` |
| `LANGSMITH_API_KEY` | no | Tracing stays off unless this is set |
| `LANGSMITH_TRACING` | no | Defaults on only when a LangSmith key is present |
| `LANGSMITH_PROJECT` | no | Default `the-daily-bubble` |
| `DAILY_BUBBLE_HOME` | no | Override `~/.the-daily-bubble` |

First run copies the built-in agent personas into `~/.the-daily-bubble/agents/` and starts a short conversation to create your user profile. After that, if today's cache is missing, it searches, fetches, reranks, and pulls weather.

### Set up a user profile

On first launch the Guide interviews you for a display name, location, interests, and anti-interests, then writes YAML to `~/.the-daily-bubble/users/<id>.yaml`.

Later:

```
/new-user-profile          # or /new-user
/show profile              # current user; or /show profile <id>
/edit profile              # current user; or /edit profile <id>
```

Without an API key, user setup falls back to a short form. Chat, agent interviews, and `/refresh` still need a key.

### Add an agent profile

Four personas are seeded on first run: **brief** (newscaster), **buddy** (friendly, default), **sunny** (positive), **gray** (pessimistic). Files live in `~/.the-daily-bubble/agents/<id>.yaml`.

```
/new-agent-profile         # or /new-agent; requires OPENAI_API_KEY
/show agent                # current persona; or /show agent <id>
/edit agent                # current persona; or /edit agent <id>
```

You can also drop a valid YAML file into `agents/` and switch to it.

### Switch users and agents

```
/profile                   # list users; active id is marked with *
/profile sam               # switch user, reload today's cache, rebuild the map
/agent                     # list personas
/agent gray                # switch persona and rebuild the system prompt
```

The active pair is stored in `~/.the-daily-bubble/config.yaml` as `active_user` and `active_agent`.

### Optional: LangSmith

Uncomment `LANGSMITH_API_KEY` in `.env`. Tracing turns on automatically when that key is set; the project name defaults to `the-daily-bubble`.

Traces can include profile text, article blurbs, and chat. Tags: `ingest`, `search`, `rerank`, `weather`, `chat`, `guide`.

### Set the OpenAI model

Resolution order: `OPENAI_MODEL` in the environment, then `model` in `~/.the-daily-bubble/config.yaml`, then `gpt-4.1`.

```bash
# .env
OPENAI_MODEL=gpt-4o-mini
```

Restart the CLI after changing it. There is no `--model` flag.

## Try this

```
Rob ❯ What's the weather look like?
Rob ❯ What's new?
Rob ❯ Read me the first one
Rob ❯ /agent gray
Rob ❯ /new-user-profile
Rob ❯ /refresh
```

Slash commands: `/help`, `/clear`, `/refresh`, `/profile [id]`, `/agent [id]`, `/show profile [id]`, `/show agent [id]`, `/new-user-profile` (`/new-user`), `/new-agent-profile` (`/new-agent`), `/edit profile [id]`, `/edit agent [id]`, `/cancel`, `/quit` (`/exit`).

## How it works

Two loops:

1. **Ingest** (startup if cache is stale, or `/refresh`) — Google News RSS per interest, fetch + extract, LLM rerank with spoken blurbs (drop score `< 0`), Open-Meteo weather. Cached per user per calendar day under `~/.the-daily-bubble/cache/`.
2. **Chat** — LangChain `create_agent` with the workspace map in the system prompt and a `read_article` tool for full text. Article bodies in the map are treated as untrusted source text, not instructions.
