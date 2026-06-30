# StackChan Agent Guide

Use this guide when working from the StackChan repository root. Subdirectories may
have their own `AGENTS.md`; follow the most specific guide for the files being
changed.

## Collaboration Style

- Think with a consultant's structure: define the symptom, split the system into
  layers, identify the narrowest proof point, then act.
- Communicate like a close teammate: direct, warm, and concrete.
- Prefer evidence over guesses. When hardware, firmware, backend, and web UI are
  all involved, do not collapse the problem into one likely cause too early.

## Repository Map

- `agora-server/server/`: local FastAPI backend and XiaoZhi bridge.
- `agora-server/web/`: local web frontend.
- `firmware/`: ESP-IDF firmware, now also buildable through PlatformIO.
- `app/`, `server/`, `remote/`: supporting project areas; inspect before assuming
  ownership.

## CodeGraph

If a `.codegraph/` directory exists at a repo root, use CodeGraph before broad
grep/find when locating behavior:

```bash
codegraph explore "<symbol names or question>"
codegraph node <symbol-or-file>
```

Skip CodeGraph when no `.codegraph/` directory exists.

## Debugging Voice Conversations

Treat voice failures as a pipeline, not a single "the bot is broken" state:

1. Wake/listen state on the device.
2. Mic capture / voice processor output.
3. Opus encode queue push.
4. Opus packet produced.
5. Firmware send queue pop.
6. WebSocket binary frame sent.
7. Backend receives device audio.
8. Backend forwards audio upstream.
9. STT transcript arrives.
10. LLM response arrives.
11. TTS/downlink audio returns to the device.

When adding logs, place bounded, high-signal logs at these boundaries. Log the
first few events and then every Nth event for high-rate audio paths. Include sizes,
queue depth, state, session id, channel id, and failure reasons where available.

Good firmware log tags from the current path:

- `VOICE processor output`
- `VOICE encode queue push`
- `VOICE opus encoded`
- `VOICE send queue pop`
- `VOICE websocket send`

If these appear and keep counting upward, the device is producing and uploading
audio frames. Continue the investigation on backend receive, upstream STT, turn
state, and response downlink instead of assuming the firmware is silent.

## Local Dev Servers

- Backend default: `agora-server/server`, port `8000`.
- Frontend default: Vite on port `5173`.
- When starting long-running dev servers for the user, keep them running only when
  they are part of the requested test setup and report the URL/port.
- Stop temporary monitors, one-off debug commands, and accidental long-running
  sessions before finishing a turn.

## PlatformIO / Firmware Habits

- Use PlatformIO from `firmware/`:

```bash
pio run
pio run -t upload
pio device monitor
```

- Prefer the checked-in `firmware/platformio.ini` over ad hoc board/platform
  guesses.
- Current upload/monitor port used during setup: `/dev/cu.usbmodem1101`.
- The app partition limit is defined by `partitions.csv`; keep PlatformIO's
  maximum app size aligned with the active OTA partition.
- PlatformIO CMake inspection may run in CMake script mode. Guard commands such
  as `add_definitions`, `add_compile_options`, `include(project.cmake)`, and
  `project(...)` so script-mode inspection does not fail.
- If generated asset/resource files are missing during the first PlatformIO
  build, check the generated Ninja targets under `.pio/build/<env>/` rather than
  rewriting the asset pipeline.
- Be careful with nonstandard flash partitions. PlatformIO's normal upload path
  may flash bootloader, partition table, OTA data, and app, but not every custom
  data partition. Do not erase or overwrite asset partitions casually.
- For serial monitor on macOS, a non-TTY command can fail with a `termios` error.
  Use a real TTY monitor session when interactive serial output is needed.

## Common Mistakes To Avoid

- Do not treat the device's visible "listening" state as proof that audio is or
  is not uploading. Confirm the audio-frame boundary logs.
- Do not jump straight to firmware, backend, or Agora as the root cause. First
  identify the last boundary with positive evidence.
- Do not spam logs on every audio frame indefinitely. Rate-limit them so the
  timing signal remains readable.
- Do not assume the Python backend owns every production route; `agora-server`
  has both backend and web paths.
- Do not edit or revert unrelated dirty worktree changes. Inspect them only if
  they intersect the task.
- Do not leave `pio device monitor` running after collecting the needed evidence.
- Do not use destructive git commands unless the user explicitly asks for them.

## Verification Habits

- For backend changes, run the narrowest available check first, then the broader
  verification command if the change touches contracts or shared behavior.
- For firmware changes, build before upload when time permits. After upload,
  verify with serial logs that the expected boot, connect, wake, and voice-path
  events actually happen.
- For conversation bugs, verify both sides: firmware serial logs and backend logs.
  A successful fix needs a positive signal on both ends of the bridge.
