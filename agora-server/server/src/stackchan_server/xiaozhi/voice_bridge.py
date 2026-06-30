# -*- coding: utf-8 -*-
"""
Voice bridge (M3): connect one XiaoZhi device session to Agora + ConvoAI.

Flow:
  device mic Opus(60ms) --WS--> [decode] --PCM--> AgoraMedia.push_pcm --> RTC channel
  ConvoAI agent TTS --RTC--> AgoraMedia.on_remote_pcm(PCM,10ms) --[reframe 60ms + encode]--> device speaker

The Agora audio observer fires on a native C thread; we marshal PCM into the asyncio loop
via loop.call_soon_threadsafe, then an asyncio task reframes -> encodes Opus -> WS, with
tts/start gating and a silence-gap tts/stop.
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import os
import random
import time
from typing import Optional

from .opus_codec import OpusCodec, FRAME_BYTES
from ..agora.media import AgoraMedia
from ..config import dance_keyword_enabled

logger = logging.getLogger("uvicorn.error")

DOWNLINK_GAP_S = 0.6   # no agent audio for this long -> end the TTS turn

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

    # ---- lifecycle ----
    async def start(self) -> bool:
        from ..agora.agent import Agent
        from agora_agent.agentkit.token import generate_convo_ai_token

        app_id = os.getenv("AGORA_APP_ID")
        app_cert = os.getenv("AGORA_APP_CERTIFICATE")
        token = generate_convo_ai_token(
            app_id=app_id, app_certificate=app_cert,
            channel_name=self.channel, uid=self.user_uid, token_expire=3600,
        )

        # 1) media bridge joins as USER_UID
        self.media = AgoraMedia(self.channel, self.user_uid,
                                on_remote_pcm=self._on_remote_pcm,
                                on_stream_msg=self._on_stream_msg)
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
            self.agent_id = self.agent.get("agent_id") if isinstance(self.agent, dict) else self.agent
            logger.info("VoiceBridge: ConvoAI started agent_id=%s channel=%s", self.agent_id, self.channel)
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
        logger.info("TRANSCRIPT [%s]: %s", who, text)
        if "user" in who.lower() and text.strip():
            self._last_user_speech = time.monotonic()
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
            if self._up_n % 25 == 0:
                logger.info("UPLINK #%d rms=%d %s push=%s", self._up_n, rms, state, ret)
        except Exception:
            logger.exception("uplink decode/push error")

    # ---- downlink: Agora PCM (C thread) -> queue -> reframe+encode -> device ----
    def _on_remote_pcm(self, pcm: bytes):
        # called on a native thread; hand to the loop
        self._down_n += 1
        if self._down_n <= 3 or self._down_n % 50 == 0:
            logger.info("DOWNLINK #%d: agent pcm %dB from Agora", self._down_n, len(pcm))
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
                    logger.info("RECV #%d rms=%d jitter=%dms dropped=%d",
                                recv, rms, (len(self._jitter) // FRAME_BYTES) * 60, dropped)
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
        PREBUFFER = FRAME_BYTES * 8   # ~480ms cushion before starting, to absorb agent jitter
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
                        logger.info("TTS-> device: begin (agent speaking)")
                        await self.session.begin_tts()
                        self._tts_active = True
                    frame = bytes(self._jitter[:FRAME_BYTES]); del self._jitter[:FRAME_BYTES]
                    await self.session.push_tts_frame(self.enc.encode(frame))
                elif self._tts_active and have < FRAME_BYTES \
                        and (time.monotonic() - self._last_voice) > DOWNLINK_GAP_S:
                    if have:                      # flush a final partial frame
                        frame = bytes(self._jitter) + b"\x00" * (FRAME_BYTES - have)
                        del self._jitter[:]
                        await self.session.push_tts_frame(self.enc.encode(frame))
                    logger.info("TTS-> device: end (silence -> back to listening)")
                    await self.session.end_tts()
                    self._tts_active = False
                elif self._tts_active:
                    # mid-turn underrun: jitter empty but agent spoke recently. Insert one
                    # silence frame to keep playout continuous (prevents device-side skips).
                    underruns += 1
                    if underruns % 15 == 1:
                        logger.info("downlink underrun #%d (jitter empty mid-turn) -> silence frame", underruns)
                    await self.session.push_tts_frame(self.enc.encode(b"\x00" * FRAME_BYTES))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("downlink send error")
