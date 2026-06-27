# -*- coding: utf-8 -*-
"""
MCP host (server side) toward the XiaoZhi device.

The device runs a JSON-RPC 2.0 MCP *server* (firmware: main/mcp_server.cc). We are the
*host/client*: we send `initialize`, `tools/list`, `tools/call` wrapped in XiaoZhi `mcp`
messages, and correlate replies by JSON-RPC id.

Usage:
    host = McpHost(send_payload)   # send_payload: async (payload_dict) -> None
    ...
    host.feed(payload)            # call on every inbound {"type":"mcp"} payload
    await host.initialize()
    tools = await host.list_tools()
    await host.call_tool("self.robot.dance", {})
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("uvicorn.error")

SendPayload = Callable[[Dict[str, Any]], Awaitable[None]]


class McpError(RuntimeError):
    pass


class McpHost:
    def __init__(self, send_payload: SendPayload, request_timeout: float = 20.0):
        self._send = send_payload
        self._timeout = request_timeout
        self._next_id = 0
        self._pending: Dict[int, "asyncio.Future[Any]"] = {}
        self.tools: List[Dict[str, Any]] = []
        self.initialized = False

    def _alloc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        req_id = self._alloc_id()
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[Any]" = loop.create_future()
        self._pending[req_id] = fut
        logger.info("MCP -> id=%s method=%s params=%s", req_id, method, params)
        await self._send(payload)
        try:
            return await asyncio.wait_for(fut, self._timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise McpError(f"MCP request timed out: {method}") from exc

    def feed(self, payload: Dict[str, Any]) -> None:
        """Handle an inbound JSON-RPC message from the device (the `payload` of an mcp msg)."""
        logger.info("MCP <- id=%s keys=%s", payload.get("id"), list(payload.keys()))
        msg_id = payload.get("id")
        if msg_id is None:
            # notification (or device-initiated request we don't answer in the POC)
            return
        fut = self._pending.pop(int(msg_id), None)
        if fut is None or fut.done():
            return
        if "error" in payload:
            fut.set_exception(McpError(str(payload["error"])))
        else:
            fut.set_result(payload.get("result"))

    async def initialize(self) -> Dict[str, Any]:
        result = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "stackchan-xzbridge", "version": "0.1.0"},
        })
        self.initialized = True
        logger.info("MCP device serverInfo=%s", (result or {}).get("serverInfo"))
        return result or {}

    async def list_tools(self) -> List[Dict[str, Any]]:
        """tools/list, following the device's cursor pagination (8KB payload cap)."""
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(64):  # safety bound
            params = {"withUserTools": False}
            if cursor:
                params["cursor"] = cursor
            result = await self._request("tools/list", params) or {}
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self.tools = tools
        logger.info("MCP device tools: %s", [t.get("name") for t in tools])
        return tools

    def has_tool(self, name: str) -> bool:
        return any(t.get("name") == name for t in self.tools)

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        return await self._request("tools/call", {"name": name, "arguments": arguments or {}})

    async def call_tool_nowait(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> None:
        """Send tools/call without awaiting the JSON-RPC result. The device executes the
        tool regardless; its ack can be slow/starved while in realtime-listen mode."""
        req_id = self._alloc_id()
        payload = {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments or {}}}
        logger.info("MCP -> (nowait) id=%s tools/call %s", req_id, name)
        await self._send(payload)
