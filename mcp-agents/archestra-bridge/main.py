"""
Archestra MCP Bridge
====================
HTTP JSON-RPC server that proxies MCP requests to Nexus agent SSE endpoints.
Uses the official MCP SDK client for proper SSE protocol handling.

Archestra  →  HTTP POST /mcp  →  Bridge  →  MCP SSE Client  →  Agents
"""

import os
import json
import asyncio
import logging
from typing import Any, Dict, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("archestra-bridge")

# ── Agent SSE endpoints ──────────────────────────────────────────────────────
AGENTS = {
    "sentinel": "https://nexus-sentinel-agent-833613368271.us-central1.run.app",
    "detective": "https://nexus-detective-agent-833613368271.us-central1.run.app",
    "historian": "https://nexus-historian-agent-833613368271.us-central1.run.app",
    "mediator": "https://nexus-mediator-agent-833613368271.us-central1.run.app",
    "executor": "https://nexus-executor-agent-833613368271.us-central1.run.app",
}

# ── Tool cache ────────────────────────────────────────────────────────────────
cached_tools: List[dict] = []
tools_loaded = False


@asynccontextmanager
async def agent_session(agent_name: str):
    """
    Create a fully-initialized MCP client session to an agent.
    Handles: SSE connect → initialize handshake → yield ready session → cleanup
    """
    base_url = AGENTS[agent_name]
    sse_url = f"{base_url}/sse"

    async with sse_client(url=sse_url) as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            logger.info(f"✅ Initialized session to {agent_name}")
            yield session


async def load_all_tools():
    """Discover tools from all 5 agents and cache them."""
    global cached_tools, tools_loaded

    all_tools = []
    for agent_name in AGENTS:
        try:
            async with agent_session(agent_name) as session:
                result = await session.list_tools()

                for tool in result.tools:
                    tool_dict = {
                        "name": f"{agent_name}_{tool.name}",
                        "description": tool.description or "",
                        "inputSchema": tool.inputSchema
                            if hasattr(tool, "inputSchema")
                            else {},
                    }
                    all_tools.append(tool_dict)

                logger.info(
                    f"  Loaded {len(result.tools)} tools from {agent_name}"
                )
        except Exception as e:
            logger.error(f"  ❌ Failed to load tools from {agent_name}: {e}")

    cached_tools = all_tools
    tools_loaded = True
    logger.info(f"🔧 Total tools cached: {len(cached_tools)}")


async def call_agent_tool(
    agent_name: str, tool_name: str, arguments: Dict[str, Any]
) -> Any:
    """Open a fresh MCP session and call a single tool."""
    try:
        async with agent_session(agent_name) as session:
            result = await session.call_tool(tool_name, arguments)

            # Flatten TextContent list into a single string / dict
            if hasattr(result, "content") and result.content:
                texts = []
                for block in result.content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
                combined = "\n".join(texts)

                # Try to parse as JSON for structured output
                try:
                    return {
                        "content": [
                            {"type": "text", "text": combined}
                        ]
                    }
                except Exception:
                    return {
                        "content": [
                            {"type": "text", "text": combined}
                        ]
                    }
            return {"content": [{"type": "text", "text": str(result)}]}

    except Exception as e:
        logger.error(f"Error calling {tool_name} on {agent_name}: {e}")
        return {
            "content": [
                {"type": "text", "text": f"Error: {e}"}
            ],
            "isError": True,
        }


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Nexus-Zero Archestra Bridge")


@app.on_event("startup")
async def startup_event():
    """Load tools from all agents on bridge startup."""
    logger.info("🚀 Bridge starting — discovering agent tools …")
    try:
        await load_all_tools()
    except Exception as e:
        logger.error(f"Tool discovery failed on startup: {e}")


@app.get("/")
async def health_check():
    """Health check endpoint."""
    return {
        "service": "Nexus-Zero Archestra Bridge",
        "status": "running",
        "tools_loaded": len(cached_tools),
        "agents": list(AGENTS.keys()),
    }


@app.get("/tools")
async def list_all_tools():
    """Debug endpoint — show all discovered tools."""
    if not tools_loaded:
        await load_all_tools()
    return {"tools": cached_tools, "count": len(cached_tools)}


@app.post("/refresh")
async def refresh_tools():
    """Force refresh tool cache from all agents."""
    global tools_loaded
    tools_loaded = False
    await load_all_tools()
    return {"status": "refreshed", "tools_loaded": len(cached_tools)}


@app.post("/mcp")
async def mcp_http_endpoint(request: Request):
    """
    HTTP MCP endpoint that Archestra POSTs JSON-RPC 2.0 to.
    Handles: initialize, notifications/initialized, tools/list, tools/call
    """
    body = {}
    try:
        body = await request.json()
        method = body.get("method")
        params = body.get("params", {})
        request_id = body.get("id")

        logger.info(f"MCP ← method={method}  id={request_id}")

        # ── initialize ────────────────────────────────────────────────────
        if method == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "nexus-zero-bridge",
                        "version": "1.0.0",
                    },
                },
            })

        # ── notifications/initialized ─────────────────────────────────────
        if method == "notifications/initialized":
            # Fire-and-forget notification — no id, return empty 200
            return JSONResponse(content={}, status_code=200)

        # ── ping ──────────────────────────────────────────────────────────
        if method == "ping":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {},
            })

        # ── tools/list ────────────────────────────────────────────────────
        if method == "tools/list":
            # Always refresh to pick up agent schema changes
            await load_all_tools()

            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": cached_tools},
            })

        # ── tools/call ────────────────────────────────────────────────────
        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if "_" not in tool_name:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": f"Invalid tool name '{tool_name}'. Expected format: agentname_toolname",
                    },
                })

            agent_name, actual_tool = tool_name.split("_", 1)

            if agent_name not in AGENTS:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": f"Unknown agent: {agent_name}",
                    },
                })

            result = await call_agent_tool(agent_name, actual_tool, arguments)

            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            })

        # ── unknown method ────────────────────────────────────────────────
        logger.warning(f"Unknown MCP method: {method}")
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        })

    except Exception as e:
        logger.error(f"MCP endpoint error: {e}", exc_info=True)
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": body.get("id") if body else None,
                "error": {"code": -32603, "message": str(e)},
            },
            status_code=500,
        )


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    PORT = int(os.environ.get("PORT", 8080))
    HOST = os.environ.get("HOST", "0.0.0.0")

    logger.info(f"Starting Archestra Bridge on {HOST}:{PORT}")
    logger.info(f"Proxying to {len(AGENTS)} agents: {list(AGENTS.keys())}")

    uvicorn.run(app, host=HOST, port=PORT)
