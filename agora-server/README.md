# StackChan Agora Server

FastAPI backend and React choreographer UI for the StackChan XiaoZhi-to-Agora bridge.

The firmware in `../firmware/` calls this server directly over HTTP after Wi-Fi is configured. The `ui/` directory contains the React + Vite choreographer for editing and pushing `dance.json`; its production build is served by the backend at the root path.

## What It Provides

- XiaoZhi protocol OTA and WebSocket endpoints for firmware.
- Agora Conversational AI token/config and Agent start/stop endpoints.
- StackChan command routing, dance channel, and choreography helpers.
- Health and lightweight JSON metrics endpoints.
- Curl-friendly command injection for POC testing.

## Layout

```text
agora-server/
  README.md
  LICENSE
  ui/                # React + Vite choreographer; `npm run build` -> ui/dist
  server/
    .env.example
    pyproject.toml
    uv.lock
    src/
      stackchan_server/
        config.py
        main.py
        apps/
          native_agent_api.py
        agora/
          agent.py
          media.py
        stackchan/
          commands.py
          dance_channel.py
          choreography/
        xiaozhi/
          app.py
          session.py
          protocol.py
          mcp.py
          voice_bridge.py
          opus_codec.py
    tests/
      test_agent_config.py
      test_command_router.py
      test_device_command_api.py
```

## Configuration

Create `server/.env.local` from the template:

```bash
cd server
cp .env.example .env.local
```

Set:

```bash
AGORA_APP_ID=your_agora_app_id
AGORA_APP_CERTIFICATE=your_agora_app_certificate
# Optional: RESTful API Basic-auth credentials. Set both when Token007 auth is
# rejected with "401 Invalid token"; the server then uses HTTP Basic auth.
AGORA_CUSTOMER_ID=your_agora_customer_id
AGORA_CUSTOMER_SECRET=your_agora_customer_secret
# Empty by default: a wake/open event should not auto-start a chat turn.
AGENT_GREETING=
XZ_DEVICE_TOKEN=change_me_before_deploying
PORT=8000
```

`AGORA_APP_ID` and `AGORA_APP_CERTIFICATE` are required for live Agora token generation and agent startup. The agent persona is the Mandarin-speaking Stack Chan; STT defaults to `zh-CN` and TTS to a Chinese voice (override via `STT_LANGUAGE` / `TTS_VOICE_ID`).
Set a unique `XZ_DEVICE_TOKEN` before exposing the service outside a local development network.

## Run

```bash
# Build the UI first; the backend serves ui/dist at the root path.
(cd agora-server/ui && npm install && npm run build)

cd agora-server/server
uv sync
uv run stackchan-server
```

The server listens on `0.0.0.0:8000` by default. Configure StackChan firmware to call the host machine's LAN IP, not `localhost`.

## Firmware-Facing Endpoints

- `GET /get_config`
- `GET /healthz`
- `GET /metrics`
- `POST /v2/startAgent`
- `POST /v2/stopAgent`
- `GET /device/commands/next?deviceId=stackchan-1`

## POC Command Endpoints

- `POST /device/commands/resolve`
- `POST /device/commands`

Supported POC actions:

- `dance`
- `sing`
- `stop`

## Curl Smoke Test

Queue a command from text:

```bash
curl -X POST http://localhost:8000/device/commands/resolve \
  -H "Content-Type: application/json" \
  -d '{"deviceId":"stackchan-1","text":"来跳个舞吧"}'
```

Poll the next command as firmware would:

```bash
curl "http://localhost:8000/device/commands/next?deviceId=stackchan-1"
```

Queue a direct command:

```bash
curl -X POST http://localhost:8000/device/commands \
  -H "Content-Type: application/json" \
  -d '{"deviceId":"stackchan-1","action":"stop","source":"manual"}'
```

Generate Agora config:

```bash
curl "http://localhost:8000/get_config?channel=stackchan-poc&uid=10001"
```

## Verify

From `agora-server/server`:

```bash
uv run pytest
uv run python -m compileall src tests scripts
uv run python scripts/smoke_fake_server.py
```

These checks cover the command router and the firmware-facing command endpoints. Live `/get_config`, `/v2/startAgent`, and `/v2/stopAgent` still require valid Agora credentials.
