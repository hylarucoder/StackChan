# -*- coding: utf-8 -*-
"""
Agora Agent & Token Service

HTTP APIs:
- GET  /get_config     -> Agent.generate_config()
- POST /v2/startAgent  -> Agent.start()
- POST /v2/stopAgent   -> Agent.stop()
"""
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agora_agent.core.api_error import ApiError
from agora_agent.agentkit.token import generate_convo_ai_token
from ..agora.agent import Agent
from ..stackchan.commands import CommandRouter, DeviceCommand
from ..config import load_environment

logger = logging.getLogger("uvicorn.error")
load_environment()

TokenGenerator = Callable[..., str]


@dataclass
class ServerMetrics:
    requests_total: int = 0
    request_errors_total: int = 0
    commands_queued_total: int = 0
    started_at: float = field(default_factory=time.time)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "requests_total": self.requests_total,
            "request_errors_total": self.request_errors_total,
            "commands_queued_total": self.commands_queued_total,
            "uptime_seconds": max(0.0, time.time() - self.started_at),
        }


@dataclass
class ServerDependencies:
    agent: Optional[Any]
    command_router: CommandRouter
    token_generator: TokenGenerator = generate_convo_ai_token
    app_id: Optional[str] = None
    app_certificate: Optional[str] = None
    metrics: ServerMetrics = field(default_factory=ServerMetrics)

    @classmethod
    def from_environment(cls) -> "ServerDependencies":
        try:
            agent = Agent()
        except ValueError as exc:
            logger.exception(
                "Failed to initialize Agora Agent SDK. Service will fail if endpoints are called without proper configuration: %s",
                exc,
            )
            agent = None
        return cls(
            agent=agent,
            command_router=CommandRouter(),
            token_generator=generate_convo_ai_token,
            app_id=os.getenv("AGORA_APP_ID"),
            app_certificate=os.getenv("AGORA_APP_CERTIFICATE"),
        )


def _log_route_error(route: str, exc: Exception, **context) -> None:
    """Log route failures with safe request context and a traceback."""
    safe_context = {key: value for key, value in context.items() if value is not None}
    logger.exception(
        "Request failed route=%s context=%s error_type=%s error=%s",
        route,
        safe_context,
        type(exc).__name__,
        exc,
    )


def _to_http_error(exc: Exception) -> HTTPException:
    """Convert SDK exceptions to HTTP errors"""
    if isinstance(exc, ApiError):
        headers = getattr(exc, "headers", None) or {}
        body = getattr(exc, "body", None)
        upstream_status = getattr(exc, "status_code", None)
        upstream_message = None
        if isinstance(body, dict):
            upstream_message = body.get("message") or body.get("error")
        detail = {
            "message": "Agora upstream API error",
            "upstream_status": upstream_status,
            "upstream_message": upstream_message or str(body),
            "agora_trace_id": headers.get("x-agora-trace-id"),
        }
        return HTTPException(status_code=502, detail=detail)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Internal error: {exc}")

# Request models
class StartAgentRequest(BaseModel):
    """Request body for POST /v2/startAgent"""
    channelName: str
    rtcUid: int
    userUid: int
    parameters: Optional[Dict[str, Any]] = None


class StopAgentRequest(BaseModel):
    """Request body for POST /v2/stopAgent"""
    agentId: str


class DirectDeviceCommandRequest(BaseModel):
    """Request body for POST /device/commands"""
    deviceId: str
    action: str
    source: str = "manual"
    parameters: Optional[Dict[str, str]] = None


class ResolveDeviceCommandRequest(BaseModel):
    """Request body for POST /device/commands/resolve"""
    deviceId: str
    text: str


# API endpoints
def _generate_channel_name() -> str:
    return f"ai-conversation-{int(time.time())}-{random.randint(1000, 9999)}"


def _command_payload(command: DeviceCommand) -> Dict[str, Any]:
    return {
        "command_id": command.command_id,
        "sequence": command.sequence,
        "action": command.action,
        "source": command.source,
        "created_at": command.created_at,
        "text": command.text,
        "parameters": command.parameters,
    }


def _idle_command_payload() -> Dict[str, Any]:
    return {
        "command_id": "",
        "sequence": 0,
        "action": "idle",
        "source": "poll",
        "created_at": time.time(),
        "text": None,
        "parameters": {},
    }


def _dependencies(request: Request) -> ServerDependencies:
    return request.app.state.dependencies


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz(request: Request):
        current = _dependencies(request)
        agent_configured = current.agent is not None and bool(
            current.app_id and current.app_certificate
        )
        return {
            "ok": True,
            "dependencies": {
                "agent_configured": agent_configured,
                "command_router": True,
            },
            "metrics": current.metrics.snapshot(),
        }

    @router.get("/metrics")
    async def metrics(request: Request):
        return _dependencies(request).metrics.snapshot()

    @router.get("/get_config")
    async def get_config(
        request: Request,
        channel: Optional[str] = Query(default=None),
        uid: Optional[int] = Query(default=None),
    ):
        """Generate connection configuration"""
        current = _dependencies(request)
        if current.agent is None:
            raise HTTPException(
                status_code=500,
                detail="Service not properly configured. Please check environment variables.",
            )

        try:
            user_uid = uid or random.randint(1000, 9999999)
            agent_uid = str(random.randint(10000000, 99999999))
            channel_name = channel or _generate_channel_name()

            # Generate a one-hour RTC+RTM token and renew it client-side as needed.
            token = current.token_generator(
                app_id=current.app_id,
                app_certificate=current.app_certificate,
                channel_name=channel_name,
                uid=user_uid,
                token_expire=3600,
            )

            config_data = {
                "app_id": current.app_id,
                "token": token,
                "uid": str(user_uid),
                "channel_name": channel_name,
                "agent_uid": agent_uid,
            }

            return {
                "code": 0,
                "data": config_data,
                "msg": "success",
            }
        except Exception as e:
            _log_route_error("/get_config", e, channel=channel, uid=uid)
            raise _to_http_error(e)

    @router.post("/v2/startAgent")
    async def start_agent(request: Request, body: StartAgentRequest):
        """Start agent in a channel"""
        current = _dependencies(request)
        if current.agent is None:
            raise HTTPException(
                status_code=500,
                detail="Service not properly configured. Please check environment variables.",
            )

        try:
            output_audio_codec = None
            if body.parameters:
                output_audio_codec = body.parameters.get("output_audio_codec")

            result = await current.agent.start(
                channel_name=body.channelName,
                agent_uid=body.rtcUid,
                user_uid=body.userUid,
                output_audio_codec=output_audio_codec,
            )
            return {"code": 0, "msg": "success", "data": result}
        except Exception as e:
            _log_route_error(
                "/v2/startAgent",
                e,
                channelName=body.channelName,
                rtcUid=body.rtcUid,
                userUid=body.userUid,
            )
            raise _to_http_error(e)

    @router.post("/v2/stopAgent")
    async def stop_agent(request: Request, body: StopAgentRequest):
        """Stop agent by ID"""
        current = _dependencies(request)
        if current.agent is None:
            raise HTTPException(
                status_code=500,
                detail="Service not properly configured. Please check environment variables.",
            )

        try:
            await current.agent.stop(body.agentId)
            return {"code": 0, "msg": "success"}
        except Exception as e:
            _log_route_error("/v2/stopAgent", e, agentId=body.agentId)
            raise _to_http_error(e)

    @router.post("/device/commands")
    async def enqueue_device_command(request: Request, body: DirectDeviceCommandRequest):
        """Queue a direct command for a device."""
        current = _dependencies(request)
        try:
            command = current.command_router.enqueue(
                device_id=body.deviceId,
                action=body.action,
                source=body.source,
                parameters=body.parameters,
            )
            current.metrics.commands_queued_total += 1
            return {
                "code": 0,
                "msg": "success",
                "data": {
                    "matched": True,
                    "command": _command_payload(command),
                },
            }
        except Exception as e:
            _log_route_error("/device/commands", e, deviceId=body.deviceId, action=body.action)
            raise _to_http_error(e)

    @router.post("/device/commands/resolve")
    async def resolve_device_command(request: Request, body: ResolveDeviceCommandRequest):
        """Resolve voice/LLM text into a device command and queue it."""
        current = _dependencies(request)
        try:
            command = current.command_router.enqueue_resolved_text(
                device_id=body.deviceId,
                text=body.text,
            )
            if command is not None:
                current.metrics.commands_queued_total += 1
            return {
                "code": 0,
                "msg": "success",
                "data": {
                    "matched": command is not None,
                    "command": _command_payload(command) if command is not None else _idle_command_payload(),
                },
            }
        except Exception as e:
            _log_route_error("/device/commands/resolve", e, deviceId=body.deviceId)
            raise _to_http_error(e)

    @router.get("/device/commands/next")
    async def poll_device_command(
        request: Request,
        deviceId: str = Query(default="stackchan-1"),
    ):
        """Poll one queued command for a device. Polling consumes the command."""
        current = _dependencies(request)
        try:
            command = current.command_router.poll_next(deviceId)
            return {
                "code": 0,
                "msg": "success",
                "data": {
                    "has_command": command is not None,
                    "command": _command_payload(command) if command is not None else _idle_command_payload(),
                },
            }
        except Exception as e:
            _log_route_error("/device/commands/next", e, deviceId=deviceId)
            raise _to_http_error(e)

    return router


def configure_app(app: FastAPI, dependencies: Optional[ServerDependencies] = None) -> FastAPI:
    deps = dependencies or ServerDependencies.from_environment()
    app.state.dependencies = deps

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_observability(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid4())
        started = time.perf_counter()
        status_code = 500
        unhandled_error = False
        deps.metrics.requests_total += 1
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            unhandled_error = True
            deps.metrics.request_errors_total += 1
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            if status_code >= 500 and not unhandled_error:
                deps.metrics.request_errors_total += 1
            logger.info(
                "HTTP request method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
                request_id,
            )
            if "response" in locals():
                response.headers["x-request-id"] = request_id

    app.include_router(create_router())
    return app


def create_app(dependencies: Optional[ServerDependencies] = None) -> FastAPI:
    app = FastAPI(
        title="Agora Agent & Token Service",
        version="2.0.0",
        description="Agora Conversational AI service",
    )
    return configure_app(app, dependencies)


app = create_app()


def main() -> None:
    """Run the FastAPI app with Uvicorn for local development."""
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
