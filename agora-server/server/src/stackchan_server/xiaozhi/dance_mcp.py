"""
Server-side MCP tool surface for the Agora ConvoAI agent.

The ConvoAI agent runs in Agora's cloud with `advanced_features.enable_tools` on
(see agora/agent.py). We register this server in the agent's `llm.mcp_servers`,
so the *model itself* can decide to call `dance` mid-conversation. When it does,
Agora's cloud connects here over streamable HTTP, invokes the tool, and we push
the dance to the device over its already-registered /dance/ws side-channel.

This replaces the keyword matcher (voice_bridge passphrase) with a real tool call:
the trigger now comes from the LLM's decision, not from a substring match.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

logger = logging.getLogger("uvicorn.error")


def _transport_security() -> TransportSecuritySettings:
    """FastMCP auto-enables DNS-rebinding protection (Host allow-list) when bound to
    localhost, which 421s requests from Agora's cloud. This is a deployed backend, not a
    browser-facing localhost server, so default to protection OFF. Set
    XZ_DANCE_MCP_ALLOWED_HOSTS (comma-separated) to lock it back down to known hosts."""
    raw = os.getenv("XZ_DANCE_MCP_ALLOWED_HOSTS", "").strip()
    if not raw:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
    )

# push(style) -> result dict. Returns {"sent": bool, ...}; never raises for the
# "no device connected" case so the model gets a clean tool result either way.
DancePush = Callable[[str], Awaitable[dict[str, Any]]]


def build_dance_mcp(push: DancePush) -> FastMCP:
    """Build a stateless streamable-HTTP MCP server exposing a single `dance` tool.

    `stateless_http=True` keeps each tool call self-contained (no per-client session
    to keep alive), which matches Agora's cloud calling us one tool at a time.
    """
    mcp = FastMCP(
        "stackchan-dance",
        stateless_http=True,
        transport_security=_transport_security(),
    )

    @mcp.tool(
        description=(
            "让桌面机器人 Stack Chan 跳一段舞。当主人让你跳舞、表演、庆祝，"
            "或者气氛适合卖个萌动一下时调用。style 可传情绪，如 happy。"
        )
    )
    async def dance(style: str = "happy") -> str:
        result = await push(style)
        if not result.get("sent"):
            logger.info("MCP dance tool: not sent (%s)", result.get("reason"))
            return f"现在没法跳：{result.get('reason', 'no device')}"
        logger.info("MCP dance tool: pushed style=%s -> %s", style, result.get("device"))
        return f"已经开始跳舞啦（style={style}）"

    return mcp
