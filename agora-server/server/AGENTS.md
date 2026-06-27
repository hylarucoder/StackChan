# Python Backend Agent Guide

Use this guide when changing files under `server/`.

## Current Role

This module is the local FastAPI backend for the quickstart. It remains the authoritative local backend when developing the full stack on one machine.

The deployed web app can also serve `/api/*` directly from Next route handlers, so do not assume Python owns production traffic in every environment.

## Current Stack

- Python 3.10+
- FastAPI
- `agora-agent-server-sdk`
- `python-dotenv`
- `uvicorn`
- `uv` with `pyproject.toml` and `uv.lock`

## Current Implementation Model

- `src/stackchan_server/main.py` composes the combined FastAPI app.
- `src/stackchan_server/apps/native_agent_api.py` exposes `/get_config`, `/v2/startAgent`, and `/v2/stopAgent`.
- `src/stackchan_server/agora/agent.py` wraps `AsyncAgora`.
- `src/stackchan_server/config.py` loads `.env` and `.env.local` from the server root.
- `src/stackchan_server/apps/native_agent_api.py:create_app()` accepts `ServerDependencies` for fake agents, fake token generators, and isolated command routers.
- Do not add root-level package modules that re-export moved code; import concrete modules from `stackchan_server.*`.
- agent sessions are scoped to the requesting user with `remote_uids=[user_uid]`
- stop is idempotent through a session stop first, then `client.stop_agent(...)` fallback
- token expiry is 1 hour
- default providers are the managed Deepgram STT, OpenAI LLM, and MiniMax TTS path used by the current quickstart

## Environment

Setup from the repo root:

```bash
cp server/.env.example server/.env.local
```

Required values:

```bash
AGORA_APP_ID=your_agora_app_id
AGORA_APP_CERTIFICATE=your_agora_app_certificate
```

Optional values:

```bash
PORT=8000
AGENT_GREETING=
```

Keep `AGENT_GREETING` empty for wake-word style behavior. A wake/open event should
not automatically become a chat turn unless the demo explicitly needs a spoken
opening line.

Do not assume separate ASR, LLM, or TTS vendor secrets are required unless the code introduces a custom non-managed provider path.

## Important Files

- `pyproject.toml`: package metadata, dependencies, console scripts, pytest/ruff config
- `src/stackchan_server/main.py`: combined FastAPI app entry point
- `src/stackchan_server/apps/native_agent_api.py`: native Agora Agent HTTP API routes
- `src/stackchan_server/agora/agent.py`: async agent lifecycle and provider configuration
- `src/stackchan_server/xiaozhi/`: XiaoZhi OTA/WebSocket/MCP bridge
- `src/stackchan_server/stackchan/`: StackChan commands, dance channel, choreography
- `src/stackchan_server/config.py`: environment-file discovery and loading
- `.env.example`: local env template
- `README.md`: backend-specific setup and API examples

## Commands

From the repo root:

```bash
bun run backend
bun run doctor:local
bun run verify:backend
```

From `server/` directly:

```bash
uv sync
uv run stackchan-server
uv run uvicorn stackchan_server.main:app --host 0.0.0.0 --port 8000 --reload
uv run pytest
uv run python scripts/smoke_fake_server.py
```

## Working Rules

- Keep the FastAPI handlers async-friendly.
- Prefer `create_app(ServerDependencies(...))` for tests and fake servers instead of monkeypatching globals.
- Keep token generation behavior aligned with the Next route handlers.
- If you change the request or response contract, update the web client and root README in the same change.
- If you change agent defaults, update both backend implementations or document the intended divergence clearly.
