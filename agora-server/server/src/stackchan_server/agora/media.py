# -*- coding: utf-8 -*-
"""
Agora media bridge (M2): the server joins an RTC channel as USER_UID, publishes the
device's mic audio as PCM, and receives the ConvoAI agent's TTS audio as PCM.

API mirrors the vendored Go example (Agora-Golang-Server-SDK .../send_recv_pcm). The
Python agora.rtc package is the same v2 "agoraservice" surface:
  service.create_rtc_connection(conn_cfg, publish_cfg)
  conn.register_observer / get_local_user / register_audio_frame_observer
  conn.connect(token, channel, user_id) / publish_audio() / push_audio_pcm_data(...)
  IAudioFrameObserver.on_playback_audio_frame_before_mixing -> remote PCM

IMPORTANT: SDK callbacks run on native (C) threads, NOT the asyncio loop. The on_remote_pcm
callback handed in here will be invoked from those threads; the caller must bridge to asyncio
(run_coroutine_threadsafe / thread-safe queue).
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from typing import Callable, Optional

from agora.rtc.agora_service import AgoraService, AgoraServiceConfig, RTCConnConfig
from agora.rtc.agora_base import (
    RtcConnectionPublishConfig, ClientRoleType, ChannelProfileType,
    AudioPublishType, AudioScenarioType, AudioProfileType, AudioParams,
)
from agora.rtc.audio_frame_observer import IAudioFrameObserver
from agora.rtc.rtc_connection_observer import IRTCConnectionObserver
from agora.rtc.local_user_observer import IRTCLocalUserObserver

logger = logging.getLogger("uvicorn.error")

SAMPLE_RATE = 16000
CHANNELS = 1

_service: Optional[AgoraService] = None
_service_lock = threading.Lock()


def get_service() -> AgoraService:
    """Process-wide AgoraService singleton (initialize once)."""
    global _service
    with _service_lock:
        if _service is None:
            app_id = os.getenv("AGORA_APP_ID")
            if not app_id:
                raise RuntimeError("AGORA_APP_ID not set")
            cfg = AgoraServiceConfig()
            cfg.appid = app_id
            # Server-side AI scenario: configures the SDK's audio pipeline for a headless
            # server receiving an agent's voice (steady jitter handling). Using CHORUS/default
            # here was the missed adaptation that made the received audio choppy/bursty.
            try:
                cfg.audio_scenario = AudioScenarioType.AUDIO_SCENARIO_AI_SERVER
            except Exception:
                logger.exception("could not set AI_SERVER audio scenario")
            logdir = os.path.join(tempfile.gettempdir(), "agora_rtc_log")
            os.makedirs(logdir, exist_ok=True)
            cfg.log_path = os.path.join(logdir, "agorasdk.log")
            try:
                cfg.config_dir = logdir
                cfg.data_dir = logdir
            except Exception:
                pass
            svc = AgoraService()
            ret = svc.initialize(cfg)
            if ret != 0:
                raise RuntimeError(f"AgoraService.initialize failed: {ret}")
            _service = svc
            logger.info("AgoraService initialized")
    return _service


class _ConnObserver(IRTCConnectionObserver):
    def __init__(self, media: "AgoraMedia"):
        self.media = media

    def on_connected(self, agora_rtc_conn, conn_info, reason):
        logger.info("[agora] connected channel=%s reason=%s",
                    getattr(conn_info, "channel_id", "?"), reason)
        self.media._connected.set()

    def on_disconnected(self, agora_rtc_conn, conn_info, reason):
        logger.info("[agora] disconnected reason=%s", reason)

    def on_connection_failure(self, agora_rtc_conn, conn_info, err_code):
        logger.error("[agora] connection failure err=%s", err_code)

    def on_user_joined(self, agora_rtc_conn, user_id):
        logger.info("[agora] remote user joined: %s", user_id)

    def on_user_left(self, agora_rtc_conn, user_id, reason):
        logger.info("[agora] remote user left: %s", user_id)


class _AudioObserver(IAudioFrameObserver):
    def __init__(self, media: "AgoraMedia"):
        self.media = media

    def on_playback_audio_frame_before_mixing(self, agora_local_user, channel_id, user_id,
                                              frame, vad_result_state=None, vad_result_frame=None):
        # Per-remote-user decoded audio. (Mixed playback doesn't render on a headless server.)
        try:
            buf = getattr(frame, "buffer", None)
            if not getattr(self, "_logged", False):
                self._logged = True
                logger.info("[agora] before-mixing frame: sps=%s spc=%s ch=%s buflen=%s",
                            getattr(frame, "samples_per_sec", "?"), getattr(frame, "samples_per_channel", "?"),
                            getattr(frame, "channels", "?"), len(buf) if buf else 0)
            if buf and self.media.on_remote_pcm is not None:
                self.media.on_remote_pcm(bytes(buf))
        except Exception:
            logger.exception("[agora] before-mixing frame error")
        return 1


class _LocalUserObserver(IRTCLocalUserObserver):
    def __init__(self, media: "AgoraMedia"):
        self.media = media

    def on_stream_message(self, agora_local_user, user_id, stream_id, data, length=None):
        # NOTE: the SDK passes 5 args after self (..., data, length). Declaring only 4
        # made this throw "takes 5 positional arguments but 6 were given" on EVERY message,
        # so transcripts (the M4 passphrase source) never reached the matcher.
        logger.info("[agora] RTC stream message uid=%s stream=%s %dB", user_id, stream_id,
                    len(data) if data else 0)
        # ConvoAI delivers transcripts / events over the data stream. Hand the raw bytes
        # to the bridge, which parses + keyword-matches for the dance passphrase (M4).
        try:
            if self.media.on_stream_msg is not None:
                self.media.on_stream_msg(user_id, bytes(data) if data else b"")
        except Exception:
            logger.exception("[agora] on_stream_message error")
        return 0


class AgoraMedia:
    """One RTC connection = one device session's presence in the channel (as USER_UID)."""

    def __init__(self, channel: str, user_uid: int,
                 on_remote_pcm: Optional[Callable[[bytes], None]] = None,
                 on_stream_msg: Optional[Callable[[str, bytes], None]] = None):
        self.channel = channel
        self.user_uid = user_uid
        self.on_remote_pcm = on_remote_pcm
        self.on_stream_msg = on_stream_msg
        self._connected = threading.Event()
        self._conn = None
        self._conn_obs = None
        self._audio_obs = None
        self._local_obs = None

    def connect(self, token: str, timeout: float = 10.0) -> bool:
        svc = get_service()
        conn_cfg = RTCConnConfig()
        conn_cfg.auto_subscribe_audio = 1
        conn_cfg.auto_subscribe_video = 0
        conn_cfg.client_role_type = ClientRoleType.CLIENT_ROLE_BROADCASTER
        conn_cfg.channel_profile = ChannelProfileType.CHANNEL_PROFILE_LIVE_BROADCASTING

        pub_cfg = RtcConnectionPublishConfig()
        pub_cfg.audio_publish_type = AudioPublishType.AUDIO_PUBLISH_TYPE_PCM
        pub_cfg.audio_scenario = AudioScenarioType.AUDIO_SCENARIO_AI_SERVER
        pub_cfg.is_publish_audio = 1
        pub_cfg.is_publish_video = 0
        try:
            pub_cfg.audio_profile = AudioProfileType.AUDIO_PROFILE_DEFAULT
        except Exception:
            pass

        self._conn = svc.create_rtc_connection(conn_cfg, pub_cfg)
        self._conn_obs = _ConnObserver(self)
        self._conn.register_observer(self._conn_obs)

        local_user = self._conn.get_local_user()
        try:
            local_user.set_playback_audio_frame_before_mixing_parameters(CHANNELS, SAMPLE_RATE)
        except Exception:
            logger.exception("[agora] set_playback_before_mixing_parameters failed (continuing)")
        self._audio_obs = _AudioObserver(self)
        self._conn.register_audio_frame_observer(self._audio_obs, 0, None)

        # local user observer: receives ConvoAI stream messages (transcripts) for M4
        self._local_obs = _LocalUserObserver(self)
        try:
            self._conn.register_local_user_observer(self._local_obs)
        except Exception:
            logger.exception("[agora] register_local_user_observer failed (M4 transcript off)")

        ret = self._conn.connect(token or "", self.channel, str(self.user_uid))
        logger.info("[agora] connect() ret=%s channel=%s uid=%s", ret, self.channel, self.user_uid)
        if not self._connected.wait(timeout):
            logger.error("[agora] connect timed out")
            return False
        self._conn.publish_audio()
        return True

    def push_pcm(self, pcm16: bytes) -> int:
        """Push one chunk of PCM16 mono @16k. SDK reframes internally to 10ms."""
        if self._conn is None:
            return -1
        # Server Gateway accepts PCM in EXACTLY 10ms frames (160 samples = 320 bytes @16k
        # mono). The device sends 60ms (1920B); split into 6x10ms or STT gets malformed audio.
        # SDK does from_buffer() -> needs a writable bytearray.
        FRAME = 320
        mv = memoryview(pcm16 if isinstance(pcm16, (bytes, bytearray)) else bytes(pcm16))
        ret = 0
        off = 0
        while off + FRAME <= len(mv):
            ret = self._conn.push_audio_pcm_data(bytearray(mv[off:off + FRAME]), SAMPLE_RATE, CHANNELS, 0)
            off += FRAME
        if off < len(mv):  # trailing partial (shouldn't happen for 60ms multiples)
            ret = self._conn.push_audio_pcm_data(bytearray(mv[off:]), SAMPLE_RATE, CHANNELS, 0)
        return ret

    def close(self):
        try:
            if self._conn is not None:
                try:
                    self._conn.unpublish_audio()
                except Exception:
                    pass
                self._conn.disconnect()
                self._conn.release()
        except Exception:
            logger.exception("[agora] close error")
        finally:
            self._conn = None


if __name__ == "__main__":
    # Smoke test: init service + create a connection (no real join without a token).
    logging.basicConfig(level=logging.INFO)
    svc = get_service()
    m = AgoraMedia("smoke-test-channel", 12345)
    print("AgoraMedia constructed; service OK")
