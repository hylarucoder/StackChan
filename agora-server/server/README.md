# StackChan Hardware Backend

FastAPI service used by `stackchan-firmware/` for the XiaoZhi-to-Agora bridge.

The server has three boundaries:

- `stackchan_server.stackchan`: StackChan robot commands, dance channels, and choreography;
- `stackchan_server.xiaozhi`: XiaoZhi OTA/WebSocket/MCP/session protocol handling;
- `stackchan_server.agora`: Agora Conversational AI and media integration.

There is no Web UI in this POC.

## Configure

```bash
cp .env.example .env.local
```

Edit `.env.local`:

```bash
AGORA_APP_ID=your_agora_app_id
AGORA_APP_CERTIFICATE=your_agora_app_certificate
AGENT_GREETING=
XZ_DEVICE_TOKEN=change_me_before_deploying
PORT=8000
# Dance trigger mode(s), comma-separated: keyword (default) and/or mcp.
XZ_DANCE_TRIGGER=keyword
# Required only with the "mcp" mode: this server's dance MCP endpoint, reachable by
# Agora's cloud (e.g. https://your-public-host/dance-mcp/mcp).
XZ_DANCE_MCP_URL=
```

`AGENT_GREETING` is the spoken self-introduction on join; it defaults to
`Hi，我是 StackChan`. Set it empty for wake-word style behavior, where opening/waking
the agent does not automatically start a chat turn.
Set a unique `XZ_DEVICE_TOKEN` before exposing the service outside a local
development network.

**Dance trigger:** `XZ_DANCE_TRIGGER` selects how the robot is told to dance — `keyword`
(server matches dance phrases in the user transcript; works on LAN, the default for local
debugging) and/or `mcp` (the cloud ConvoAI agent calls the `dance` tool, a real LLM tool
call, requiring `XZ_DANCE_MCP_URL` reachable by Agora's cloud). Either fires a push over
the device's `/dance/ws` channel. See `agora-server/README.md` for the full table.

## Install

```bash
uv sync
```

`pyproject.toml` is the dependency source of truth. `uv.lock` pins the resolved
environment.

## Run

```bash
uv run stackchan-server
```

The firmware should call this service by LAN IP, for example:

```text
http://<your-computer-lan-ip>:8000
```

Equivalent development commands:

```bash
uv run uvicorn stackchan_server.main:app --host 0.0.0.0 --port 8000 --reload
uv run python -m stackchan_server.main
```

The XiaoZhi bridge can be run separately with:

```bash
uv run stackchan-xiaozhi-bridge
```

The native Agent HTTP API can be run by itself with:

```bash
uv run stackchan-native-agent-api
```

## API

Firmware uses:

- `GET /healthz`
- `GET /metrics`
- `GET /get_config`
- `POST /v2/startAgent`
- `POST /v2/stopAgent`
- `GET /device/commands/next?deviceId=stackchan-1`

Manual POC testing uses:

- `POST /device/commands/resolve`
- `POST /device/commands`

## Command Smoke

Resolve text into a queued device command:

```bash
curl -X POST http://localhost:8000/device/commands/resolve \
  -H "Content-Type: application/json" \
  -d '{"deviceId":"stackchan-1","text":"来跳个舞吧"}'
```

Poll and consume one command:

```bash
curl "http://localhost:8000/device/commands/next?deviceId=stackchan-1"
```

Queue a direct command:

```bash
curl -X POST http://localhost:8000/device/commands \
  -H "Content-Type: application/json" \
  -d '{"deviceId":"stackchan-1","action":"dance","source":"manual"}'
```

Supported actions are `dance`, `sing`, and `stop`.

## Observability

Health and lightweight JSON metrics are available without Agora credits:

```bash
curl "http://localhost:8000/healthz"
curl "http://localhost:8000/metrics"
```

Every HTTP response includes an `x-request-id` header. If the caller sends
`x-request-id`, the server preserves it; otherwise the server generates one and
logs method, path, status, duration, and request id.

## Agora Smoke

```bash
curl "http://localhost:8000/get_config?channel=stackchan-poc&uid=10001"
```

Start and stop require a real Agora project with Conversational AI enabled:

```bash
curl -X POST http://localhost:8000/v2/startAgent \
  -H "Content-Type: application/json" \
  -d '{"channelName":"stackchan-poc","rtcUid":20001,"userUid":10001}'

curl -X POST http://localhost:8000/v2/stopAgent \
  -H "Content-Type: application/json" \
  -d '{"agentId":"your_agent_id"}'
```

## Verify

```bash
uv run pytest
uv run python -m compileall src tests scripts
uv run python scripts/smoke_fake_server.py
```

`smoke_fake_server.py` starts a local fake backend and checks the HTTP API
without consuming Agora credits.

## Reuse In Tests

`stackchan_server.apps.native_agent_api.create_app()` accepts `ServerDependencies`, so tests and
local tools can inject a fake Agent, fake token generator, or isolated
`CommandRouter` without monkeypatching module globals.
