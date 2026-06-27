# StackChan Server Architecture

## Overview

Python FastAPI service for the StackChan XiaoZhi-to-Agora bridge.

Core responsibilities:

- Serve XiaoZhi protocol OTA and WebSocket endpoints for firmware.
- Bridge one XiaoZhi device session to Agora RTC and Conversational AI.
- Own StackChan robot commands, dance side-channel delivery, and choreography helpers.
- Expose native Agora Agent HTTP endpoints for diagnostics and direct integrations.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Framework | FastAPI |
| Language | Python 3.10+ |
| HTTP Server | Uvicorn |
| Agent SDK | agora-agent-server-sdk |
| RTC Bridge SDK | agora-python-server-sdk |
| Config | python-dotenv |
| Packaging | uv + pyproject.toml + src layout |

## Project Structure

```text
server/
├── pyproject.toml          # uv-managed package metadata, dependencies, scripts, tool config
├── uv.lock                 # Resolved dependency lock
├── src/
│   └── stackchan_server/
│       ├── main.py         # Combined FastAPI app entry point
│       ├── config.py       # Server-root .env discovery and loading
│       ├── apps/
│       │   └── native_agent_api.py
│       ├── agora/
│       │   ├── agent.py
│       │   └── media.py
│       ├── stackchan/
│       │   ├── commands.py
│       │   ├── dance_channel.py
│       │   └── choreography/
│       └── xiaozhi/
│           ├── app.py
│           ├── session.py
│           ├── protocol.py
│           ├── mcp.py
│           ├── voice_bridge.py
│           └── opus_codec.py
├── scripts/
├── tests/
└── .env.example
```

## Module Boundaries

### `stackchan_server.main`

Application composition layer.

- Creates the combined FastAPI app.
- Includes native Agora Agent routes.
- Includes XiaoZhi bridge routes.
- Provides the `stackchan-server` console entry point.

### `stackchan_server.apps.native_agent_api`

Native Agent HTTP API.

- Defines `/get_config`, `/v2/startAgent`, `/v2/stopAgent`, and device command routes.
- Provides `create_app(ServerDependencies(...))` for tests and fake tools.
- Owns request id logging, lightweight metrics, CORS, and HTTP error mapping for this route group.

### `stackchan_server.agora`

Agora integration.

- Wraps agora-agent-server-sdk.
- Configures ASR/LLM/TTS providers.
- Joins Agora RTC as the server-side media participant.
- Pushes device PCM uplink and receives agent PCM downlink.

### `stackchan_server.stackchan`

Robot capability layer.

- Resolves voice/LLM text into `dance`, `sing`, or `stop` commands.
- Suppresses wake-word-only text so wake/open does not become a command.
- Queues commands per device and consumes each command once.
- Manages the dedicated dance WebSocket channel.
- Compiles and validates choreography.

### `stackchan_server.xiaozhi`

XiaoZhi protocol layer.

- Serves OTA/WebSocket endpoints for XiaoZhi firmware.
- Maintains one `XzSession` per connected device.
- Handles XiaoZhi hello/listen/MCP/audio frames.
- Bridges XiaoZhi Opus audio to Agora RTC PCM and back through `voice_bridge.py`.

## Runtime Flow

```text
StackChan firmware
  -> XiaoZhi OTA / WebSocket
  -> stackchan_server.xiaozhi
  -> stackchan_server.xiaozhi.voice_bridge
  -> stackchan_server.agora.media + stackchan_server.agora.agent
  -> Agora Conversational AI
```

```text
StackChan action request
  -> stackchan_server.stackchan.commands
  -> XiaoZhi MCP or dance side-channel
  -> firmware motion/display behavior
```

## Verification Surface

- `tests/test_command_router.py`: StackChan command resolution.
- `tests/test_dance_channel.py`: dance frame protocol.
- `tests/test_dance_push_endpoint.py`: XiaoZhi dance side-channel route.
- `tests/test_device_command_api.py`: native device command HTTP routes.
- `tests/test_server_factory_observability.py`: app factory, metrics, and observability.
- `tests/test_agent_config.py`: Agora Agent configuration.
