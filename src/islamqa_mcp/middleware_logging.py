"""Log MCP tool calls."""

from __future__ import annotations

import logging
import time

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

logger = logging.getLogger("islamqa_mcp.tools")


class ToolCallLoggingMiddleware(Middleware):
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        name = context.message.name
        t0 = time.perf_counter()
        try:
            return await call_next(context)
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            logger.info("tool_call name=%s duration_ms=%.1f", name, ms)
