# -*- coding: utf-8 -*-
"""
Voice bridge (M3): connect one XiaoZhi device session to Agora + ConvoAI.

Flow:
  device mic Opus(60ms) --WS--> [decode] --PCM--> AgoraMedia.push_pcm --> RTC channel
  ConvoAI agent TTS --RTC--> AgoraMedia.on_remote_pcm(PCM,10ms)
    --[reframe 60ms + encode]--> device speaker

The Agora audio observer fires on a native C thread; we marshal PCM into the asyncio loop
via loop.call_soon_threadsafe, then an asyncio task reframes -> encodes Opus -> WS, with
tts/start gating and a silence-gap tts/stop.
"""
from __future__ import annotations

import array
import asyncio
import audioop
import base64
import json
import logging
import os
import random
import subprocess
import time
from typing import Optional

from .opus_codec import FRAME_BYTES, SAMPLE_RATE, OpusCodec
from ..agora.media import AgoraMedia
from ..config import dance_keyword_enabled

logger = logging.getLogger("uvicorn.error")

DOWNLINK_GAP_S = 0.6   # no agent audio for this long -> end the TTS turn

# Downlink smoothing. _downlink_recv drops leading silence, so playout starts at the
# first LOUD sample -> a hard onset (click/pop) and the device's Opus decoder is still
# cold, making the first ~second sound abrupt/flaky. Two mitigations:
#   - PREBUFFER: accumulate a cushion before the first frame (absorbs agent jitter).
#   - FADE: a short linear gain ramp in at turn start (and out at turn end) so the onset
#     and tail are gradual instead of a step from/to silence.
PREBUFFER_MS = int(os.getenv("XZ_DOWNLINK_PREBUFFER_MS", "600"))
FADE_MS = int(os.getenv("XZ_DOWNLINK_FADE_MS", "80"))
FADE_SAMPLES = SAMPLE_RATE * FADE_MS // 1000


def _fade_in(pcm: bytes, done: int, total: int) -> tuple[bytes, int]:
    """Linear fade-in: gain rises 0->1 across the first `total` samples of a turn.
    `done` = samples already ramped in prior frames. Returns (pcm_out, new_done).
    No-op once done >= total (the steady-state fast path)."""
    if total <= 0 or done >= total:
        return pcm, done
    buf = array.array("h")
    buf.frombytes(pcm)
    for i in range(len(buf)):
        pos = done + i
        if pos >= total:
            break
        buf[i] = (buf[i] * pos) // total
    return buf.tobytes(), done + len(buf)


def _fade_out(pcm: bytes, fade_samples: int, valid_samples: int) -> bytes:
    """Linear fade-out: gain falls 1->0 across the last `fade_samples` of the real
    audio (the first `valid_samples` of the frame; any trailing zero padding is left
    untouched). Used on the final flushed frame of a turn to avoid a tail click."""
    f = min(fade_samples, valid_samples)
    if f <= 0:
        return pcm
    buf = array.array("h")
    buf.frombytes(pcm)
    start = valid_samples - f
    for k in range(f):
        buf[start + k] = (buf[start + k] * (f - k)) // (f + 1)
    return buf.tobytes()

# When set, speak a macOS `say` line the moment the ConvoAI agent actually joins the
# RTC channel (the real "ready to talk" signal). Helps measure first-turn latency: the
# gap between voice_bridge_start and this line is how long the cold-start agent took.
SAY_READY = os.getenv("XZ_SAY_READY", "0") == "1"
SAY_READY_TEXT = os.getenv("XZ_SAY_READY_TEXT", "可以说话了")


def _say(text: str) -> None:
    """Best-effort, non-blocking macOS `say`. Safe to call from any thread."""
    try:
        subprocess.Popen(["say", text])
    except Exception:
        logger.exception("say command failed")

# M4: trigger the dance when the user says any of these (the passphrase + variants).
# This keyword path is one of the XZ_DANCE_TRIGGER modes ("keyword"); the other is the
# MCP tool call (agent.py llm.mcp_servers). See config.dance_trigger_modes.
DANCE_PHRASES = ("接着奏乐接着舞", "奏乐", "跳舞", "跳个舞", "跳支舞", "dance")


def _gen_ids():
    user_uid = random.randint(1000, 9999999)
    agent_uid = random.randint(10000000, 99999999)
    channel = f"stackchan-{int(time.time())}-{random.randint(1000,9999)}"
    return channel, user_uid, agent_uid


class VoiceBridge:
    def __init__(self, session, loop: asyncio.AbstractEventLoop):
        self.session = session
        self.loop = loop
        self.channel, self.user_uid, self.agent_uid = _gen_ids()
        self.media: Optional[AgoraMedia] = None
        self.agent = None           # ConvoAI start() result (dict)
        self.agent_id = None        # the agent_id string (for stop())
        self._agent_obj = None      # Agent() instance
        self.dec = OpusCodec()      # uplink: device Opus -> PCM
        self.enc = OpusCodec()      # downlink: PCM -> device Opus
        self._down_q: asyncio.Queue = asyncio.Queue()
        self._recv_task: Optional[asyncio.Task] = None   # producer: drains Agora PCM -> jitter
        self._send_task: Optional[asyncio.Task] = None   # consumer: plays 1 frame / 60ms
        self._jitter = bytearray()       # downlink PCM jitter buffer (producer/consumer)
        self._tts_active = False
        self._last_voice = 0.0           # monotonic time of last loud agent frame
        self._last_user_speech = time.monotonic()
        self._running = False
        self._up_n = 0
        self._down_n = 0
        self._up_last_speech = 0.0   # uplink VAD: last time the user actually spoke
        self._rec = None      # debug: raw recording of agent PCM received from Agora
        self._tx_chunks: dict = {}   # transcript reassembly: msgId -> {chunkIdx: b64}
        self._last_transcript_key: tuple[str, str] | None = None
        self._last_transcript_repeats = 0
        self._start_t = 0.0          # monotonic time start() began, for ready-latency
        self._agent_joined = False   # has the ConvoAI agent joined the channel yet?
        self._fade_in_done = 0       # samples already faded in for the current TTS turn

    # ---- lifecycle ----
    async def start(self) -> bool:
        from ..agora.agent import Agent
        from agora_agent.agentkit.token import generate_convo_ai_token

        self._start_t = time.monotonic()
        app_id = os.getenv("AGORA_APP_ID")
        app_cert = os.getenv("AGORA_APP_CERTIFICATE")
        token = generate_convo_ai_token(
            app_id=app_id, app_certificate=app_cert,
            channel_name=self.channel, uid=self.user_uid, token_expire=3600,
        )
        logger.info(
            "TURN state session=%s voice_bridge_start channel=%s user_uid=%s agent_uid=%s",
            self.session.session_id,
            self.channel,
            self.user_uid,
            self.agent_uid,
        )

        # 1) media bridge joins as USER_UID
        self.media = AgoraMedia(self.channel, self.user_uid,
                                on_remote_pcm=self._on_remote_pcm,
                                on_stream_msg=self._on_stream_msg,
                                on_user_joined=self._on_agora_user_joined)
        ok = await self.loop.run_in_executor(None, self.media.connect, token)
        if not ok:
            logger.error("VoiceBridge: media connect failed")
            return False

        # 2) downlink: producer (fill jitter) + consumer (steady 60ms playout), decoupled
        self._running = True
        try:
            self._rec = open("/tmp/stackchan_agent_rx.raw", "wb")   # debug: raw agent PCM
        except Exception:
            self._rec = None
        self._recv_task = asyncio.create_task(self._downlink_recv())
        self._send_task = asyncio.create_task(self._downlink_send())

        # 3) device uplink -> push PCM to channel
        self.session.on_audio_frame = self._on_device_opus

        # 4) start ConvoAI agent in the channel, listening to USER_UID
        try:
            self._agent_obj = Agent()
            self.agent = await self._agent_obj.start(
                channel_name=self.channel, agent_uid=self.agent_uid, user_uid=self.user_uid,
            )
            self.agent_id = (
                self.agent.get("agent_id") if isinstance(self.agent, dict) else self.agent
            )
            logger.info(
                "TURN state session=%s convoai_started agent_id=%s channel=%s",
                self.session.session_id,
                self.agent_id,
                self.channel,
            )
        except Exception:
            logger.exception("VoiceBridge: ConvoAI start failed (media still up)")
        return True

    async def stop(self):
        self._running = False
        self.session.on_audio_frame = None
        for t in (self._recv_task, self._send_task):
            if t:
                t.cancel()
        try:
            if self._agent_obj and self.agent_id:
                await self._agent_obj.stop(self.agent_id)
        except Exception:
            logger.exception("VoiceBridge: agent stop error")
        if self.media:
            await self.loop.run_in_executor(None, self.media.close)
        if self._rec:
            try:
                self._rec.close()
            except Exception:
                pass
            self._rec = None

    # ---- readiness: the agent joining the channel is the real "ready to talk" signal ----
    def _on_agora_user_joined(self, user_id: str):
        # Runs on a native Agora thread. In our 1:1 channel the only remote user is the
        # ConvoAI agent, so this fires once, when it's actually in the room and listening.
        if self._agent_joined:
            return
        self._agent_joined = True
        ready_after = time.monotonic() - self._start_t if self._start_t else -1.0
        logger.info(
            "TURN state session=%s agent_joined uid=%s ready_after=%.2fs",
            self.session.session_id,
            user_id,
            ready_after,
        )
        if SAY_READY:
            _say(SAY_READY_TEXT)

    # ---- M4: ConvoAI transcript (data stream) -> passphrase -> dance ----
    def _on_stream_msg(self, user_id: str, data: bytes):
        # runs on a native thread; hand to the loop
        self.loop.call_soon_threadsafe(self._handle_transcript, data)

    def _handle_transcript(self, data: bytes):
        # Agora ConvoAI transcript protocol over the RTC data stream:
        #   "<msgId>|<chunkIndex>|<totalChunks>|<base64 slice of the JSON>"
        # One JSON message may be split across several stream messages, so reassemble by
        # msgId, then base64-decode the concatenated payload and parse the JSON. The JSON
        # carries {"object": "user.transcription"|"assistant.transcription", "text": ...}.
        try:
            s = bytes(data).decode("utf-8", "replace")
        except Exception:
            return
        parts = s.split("|", 3)
        if len(parts) != 4:
            return
        msg_id, idx_s, total_s, b64 = parts
        try:
            idx, total = int(idx_s), int(total_s)
        except ValueError:
            return
        buf = self._tx_chunks.setdefault(msg_id, {})
        buf[idx] = b64
        if len(buf) < total:
            return                                   # wait for remaining chunks
        self._tx_chunks.pop(msg_id, None)
        try:
            payload = base64.b64decode("".join(buf[i] for i in sorted(buf)))
            obj = json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            logger.exception("transcript reassemble/decode failed")
            return
        if not isinstance(obj, dict):
            return
        who = str(obj.get("object", ""))             # user.transcription / assistant.transcription
        text = str(obj.get("text", ""))
        self._log_transcript(msg_id, who, text)
        if "user" in who.lower() and text.strip():
            self._last_user_speech = time.monotonic()
            logger.info(
                "TURN state session=%s user_speech_detected text_chars=%d",
                self.session.session_id,
                len(text),
            )
        # M4: trigger only on the USER's words (not the agent echoing the phrase back).
        if (
            dance_keyword_enabled()
            and "user" in who.lower()
            and any(p.lower() in text.lower() for p in DANCE_PHRASES)
        ):
            logger.info("M4: passphrase detected in USER transcript -> dance")
            asyncio.create_task(self._safe_dance())

    def last_meaningful_activity_at(self) -> float:
        return max(self._last_user_speech, self._last_voice)

    def _log_transcript(self, msg_id: str, who: str, text: str) -> None:
        key = (who, text)
        if key == self._last_transcript_key:
            self._last_transcript_repeats += 1
            if self._last_transcript_repeats <= 2 or self._last_transcript_repeats % 10 == 0:
                logger.info(
                    "TRANSCRIPT duplicate session=%s repeats=%d msg_id=%s object=%s text=%s",
                    self.session.session_id,
                    self._last_transcript_repeats,
                    msg_id,
                    who,
                    text,
                )
            return
        if self._last_transcript_repeats:
            logger.info(
                "TRANSCRIPT duplicate-ended session=%s repeats=%d",
                self.session.session_id,
                self._last_transcript_repeats,
            )
        self._last_transcript_key = key
        self._last_transcript_repeats = 0
        logger.info(
            "TRANSCRIPT session=%s msg_id=%s object=%s text=%s",
            self.session.session_id,
            msg_id,
            who,
            text,
        )

    async def _safe_dance(self):
        try:
            await self.session.trigger_dance("happy")
        except Exception:
            logger.exception("M4 dance trigger failed")

    # ---- uplink: device Opus -> Agora PCM. NO server-side VAD: turn detection is 100%
    #      ConvoAI's job (see turn_detection in agent.py). The ONLY thing we do here is a
    #      half-duplex ECHO guard (not a VAD): while the agent is speaking, send silence,
    #      because there is no device AEC and otherwise the agent's own voice would leak
    #      into the mic and trip ConvoAI's barge-in, cutting the agent off mid-sentence. ----
    def _on_device_opus(self, opus_packet: bytes):
        try:
            pcm = self.dec.decode(opus_packet)
            rms = audioop.rms(pcm, 2) if pcm else 0
            if self._tts_active:
                send = b"\x00" * len(pcm)   # echo guard only
                state = "(agent speaking->muted)"
            else:
                send = pcm                  # raw mic straight through; ConvoAI VAD decides
                state = "SPEECH" if rms > 500 else "(quiet)"
            ret = self.media.push_pcm(send) if self.media else None
            self._up_n += 1
            if self._up_n <= 3 or self._up_n % 25 == 0:
                logger.info(
                    "UPLINK device->agora session=%s frame=%d pcm_bytes=%d rms=%d state=%s push=%s",
                    self.session.session_id,
                    self._up_n,
                    len(pcm),
                    rms,
                    state,
                    ret,
                )
        except Exception:
            logger.exception("uplink decode/push error")

    # ---- downlink: Agora PCM (C thread) -> queue -> reframe+encode -> device ----
    def _on_remote_pcm(self, pcm: bytes):
        # called on a native thread; hand to the loop
        self._down_n += 1
        if self._down_n <= 3 or self._down_n % 50 == 0:
            logger.info(
                "DOWNLINK agora->device session=%s pcm_frame=%d pcm_bytes=%d queued=%d",
                self.session.session_id,
                self._down_n,
                len(pcm),
                self._down_q.qsize(),
            )
        self.loop.call_soon_threadsafe(self._down_q.put_nowait, pcm)

    # ---- downlink PRODUCER: drain Agora PCM into the jitter buffer (no sleeps here, so the
    #      queue is always drained immediately — receiving is decoupled from playing) ----
    async def _downlink_recv(self):
        # Near-digital-silence threshold: keep ALL real (even quiet) agent speech; only
        # treat true silence as silence. (250 was dropping quiet speech -> "few frames".)
        SILENCE_RMS = 60
        recv = 0
        dropped = 0
        try:
            while self._running:
                pcm = await self._down_q.get()
                if not pcm:
                    continue
                recv += 1
                if self._rec:
                    try:
                        self._rec.write(pcm)   # raw, ungated: exactly what Agora delivers
                    except Exception:
                        pass
                try:
                    rms = audioop.rms(pcm, 2)
                except Exception:
                    rms = 9999
                now = time.monotonic()
                if rms > SILENCE_RMS:
                    self._last_voice = now
                # Buffer everything within an utterance (from onset until sustained silence).
                # Lossless for real speech; only long leading/trailing silence is dropped.
                if (now - self._last_voice) <= DOWNLINK_GAP_S:
                    self._jitter.extend(pcm)
                else:
                    dropped += 1
                if recv % 50 == 0:
                    logger.info(
                        "DOWNLINK buffer session=%s recv=%d rms=%d jitter_ms=%d dropped_silence=%d",
                        self.session.session_id,
                        recv,
                        rms,
                        (len(self._jitter) // FRAME_BYTES) * 60,
                        dropped,
                    )
                    if self._rec:
                        try:
                            self._rec.flush()
                        except Exception:
                            pass
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("downlink recv error")

    # ---- downlink CONSUMER: play exactly one 60ms frame every 60ms (steady real-time) ----
    async def _downlink_send(self):
        # cushion before starting playout, rounded to whole 60ms frames (>=1)
        PREBUFFER = FRAME_BYTES * max(1, PREBUFFER_MS // 60)
        underruns = 0
        next_t = time.monotonic()
        try:
            while self._running:
                next_t += 0.06
                delay = next_t - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_t = time.monotonic()   # fell behind; resync
                have = len(self._jitter)
                if have >= FRAME_BYTES and (self._tts_active or have >= PREBUFFER):
                    if not self._tts_active:
                        logger.info(
                            "TTS device-send session=%s begin prebuffer_ms=%d",
                            self.session.session_id,
                            (have // FRAME_BYTES) * 60,
                        )
                        await self.session.begin_tts()
                        self._tts_active = True
                        self._fade_in_done = 0   # ramp this turn in from silence
                    frame = bytes(self._jitter[:FRAME_BYTES])
                    del self._jitter[:FRAME_BYTES]
                    if self._fade_in_done < FADE_SAMPLES:
                        frame, self._fade_in_done = _fade_in(
                            frame, self._fade_in_done, FADE_SAMPLES)
                    await self.session.push_tts_frame(self.enc.encode(frame))
                elif self._tts_active and have < FRAME_BYTES \
                        and (time.monotonic() - self._last_voice) > DOWNLINK_GAP_S:
                    if have:                      # flush a final partial frame, faded out
                        frame = bytes(self._jitter) + b"\x00" * (FRAME_BYTES - have)
                        del self._jitter[:]
                        frame = _fade_out(frame, FADE_SAMPLES, have // 2)
                        await self.session.push_tts_frame(self.enc.encode(frame))
                    logger.info(
                        "TTS device-send session=%s end reason=silence gap_s=%.3f",
                        self.session.session_id,
                        time.monotonic() - self._last_voice,
                    )
                    await self.session.end_tts()
                    self._tts_active = False
                elif self._tts_active:
                    # mid-turn underrun: jitter empty but agent spoke recently. Insert one
                    # silence frame to keep playout continuous (prevents device-side skips).
                    underruns += 1
                    if underruns % 15 == 1:
                        logger.info(
                            "DOWNLINK underrun session=%s count=%d action=silence_frame",
                            self.session.session_id,
                            underruns,
                        )
                    await self.session.push_tts_frame(self.enc.encode(b"\x00" * FRAME_BYTES))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("downlink send error")
