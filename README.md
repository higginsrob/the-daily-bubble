# The Daily Bubble

A time-boxed agentic CLI for the Grafana Labs AI engineer take-home. A general-purpose assistant whose **bubble** is the user's world: profile, weather, and a ranked news index.

The interesting part is not search. It is a **workspace map** — a compact, regenerable XML block in the system prompt — so the model talks against a live index of the day instead of stuffing full articles into context.

Design, trade-offs, and the 10-minute demo script live in [`project.plan.md`](project.plan.md). Assignment brief: [`assignment.md`](assignment.md).

## Run

```bash
cp .env.example .env   # add OPENAI_API_KEY (or OPENAI_HOST for local); LangSmith is optional
uv sync                # or: python3 -m venv .venv && source .venv/bin/activate && pip install -e .
uv run daily-bubble    # or: daily-bubble
```

First run seeds built-in agent personas into `~/.the-daily-bubble/` and interviews you for a user profile. After that, if today's cache is missing, it searches the web, fetches pages, reranks, and pulls weather.

## Environment

| Variable | Required | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | for chat + rerank | skip if using a local `OPENAI_HOST` |
| `OPENAI_MODEL` | no | default `gpt-4.1`; must match a model the host serves |
| `OPENAI_HOST` | no | OpenAI-compatible base (e.g. `http://localhost:11434` for Ollama) |
| `LANGSMITH_API_KEY` | no | tracing is off unless this is set |
| `LANGSMITH_TRACING` | no | defaults on only when a LangSmith key is present |
| `LANGSMITH_PROJECT` | no | default `the-daily-bubble` |
| `DAILY_BUBBLE_HOME` | no | override `~/.the-daily-bubble` |

Ollama example: `OPENAI_HOST=http://localhost:11434` and `OPENAI_MODEL=llama3.2` (or any model you have pulled). Chat uses tool calling and ingest uses structured JSON, so pick a model that supports both. Local hosts do not need `OPENAI_API_KEY`.

## Try this

```
Rob ❯ What's the weather look like?
Rob ❯ What's new?
Rob ❯ Read me the first one
Rob ❯ /agent gray
Rob ❯ /new-user-profile
Rob ❯ /refresh
```

Slash commands: `/help`, `/clear`, `/refresh`, `/profile [id]`, `/agent [id]`, `/show profile [id]`, `/show agent [id]`, `/new-user-profile`, `/new-agent-profile`, `/edit profile [id]`, `/edit agent [id]`, `/cancel`, `/quit`.

User profiles are created by a short interview (or `/new-user-profile` later). Built-in agents: **brief** (newscaster), **buddy** (friendly), **sunny** (positive), **gray** (pessimistic). Add more with `/new-agent-profile`.

## How it works

Two loops:

1. **Ingest** (startup if cache is stale, or `/refresh`) — Google News RSS per interest, fetch + extract, LLM rerank with spoken blurbs (drop score `< 0`), Open-Meteo weather. Cached per user per calendar day under `~/.the-daily-bubble/cache/`.
2. **Chat** — LangChain `create_agent` with the workspace map in the system prompt and a `read_article` tool for full text.

LangSmith tags: `ingest`, `rerank`, `weather`, `chat`.
