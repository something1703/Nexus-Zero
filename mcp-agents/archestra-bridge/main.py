"""
Archestra MCP Bridge
====================
HTTP JSON-RPC bridge that connects Archestra to FastMCP SSE agents.
Translates Archestra's HTTP requests to MCP SSE protocol.
"""

import os
import json
import asyncio
import logging
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("archestra-bridge")

app = FastAPI(title="Nexus-Zero Archestra Bridge")

# Agent SSE endpoints
AGENTS = {
    "sentinel": "https://nexus-sentinel-agent-833613368271.us-central1.run.app",
    "detective": "https://nexus-detective-agent-833613368271.us-central1.run.app",
    "historian": "https://nexus-historian-agent-833613368271.us-central1.run.app",
    "mediator": "https://nexus-mediator-agent-833613368271.us-central1.run.app",
    "executor": "https://nexus-executor-agent-833613368271.us-central1.run.app",
}

# Session management for SSE connections
sessions = {}


class MCPClient:
    """Manages MCP SSE connection to an agent"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session_id = None
        self.message_url = None
        
    async def connect(self):
        """Establish SSE connection and get session info"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{self.base_url}/sse")
            
            # Parse SSE stream to get session endpoint
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]  # Remove "data: " prefix
                    if data.startswith("/messages/"):
                        self.message_url = f"{self.base_url}{data}"
                        self.session_id = data.split("session_id=")[1] if "session_id=" in data else None
                        logger.info(f"Connected to {self.base_url}, session: {self.session_id}")
                        break
                        
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on the MCP agent"""
        if not self.message_url:
            await self.connect()
            
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            },
            "id": 1
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(self.message_url, json=payload)
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"Agent error: {response.text}")
                
            result = response.json()
            return result.get("result", {})
            
    async def list_tools(self) -> list:
        """Get list of available tools from agent"""
        if not self.message_url:
            await self.connect()
            
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            "id": 1
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.message_url, json=payload)
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"Agent error: {response.text}")
                
            result = response.json()
            return result.get("result", {}).get("tools", [])


@app.get("/")
async def root():
    """Health check"""
    return {
        "service": "Nexus-Zero Archestra Bridge",
        "status": "running",
        "agents": list(AGENTS.keys()),
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/agents")
async def list_agents():
    """List all available agents"""
    return {
        "agents": AGENTS,
        "count": len(AGENTS)
    }


@app.get("/tools/{agent_name}")
async def get_agent_tools(agent_name: str):
    """Get tools available from a specific agent"""
    if agent_name not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent {agent_name} not found")
        
    try:
        client = MCPClient(AGENTS[agent_name])
        tools = await client.list_tools()
        return {
            "agent": agent_name,
            "tools": tools
        }
    except Exception as e:
        logger.error(f"Error listing tools for {agent_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/call/{agent_name}/{tool_name}")
async def call_tool(agent_name: str, tool_name: str, request: Request):
    """Call a specific tool on an agent"""
    if agent_name not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent {agent_name} not found")
        
    try:
        body = await request.json()
        arguments = body.get("arguments", {})
        
        client = MCPClient(AGENTS[agent_name])
        result = await client.call_tool(tool_name, arguments)
        
        return {
            "agent": agent_name,
            "tool": tool_name,
            "result": result,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error calling {tool_name} on {agent_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """
    MCP-compatible endpoint for Archestra.
    Accepts JSON-RPC 2.0 requests and routes to appropriate agents.
    """
    try:
        body = await request.json()
        method = body.get("method")
        params = body.get("params", {})
        request_id = body.get("id", 1)
        
        # Handle initialization
        if method == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "nexus-zero-bridge",
                        "version": "1.0.0"
                    }
                }
            })
        
        # Handle tools/list - aggregate all agent tools
        elif method == "tools/list":
            all_tools = []
            for agent_name, base_url in AGENTS.items():
                try:
                    client = MCPClient(base_url)
                    tools = await client.list_tools()
                    # Prefix tool names with agent name
                    for tool in tools:
                        tool["name"] = f"{agent_name}_{tool['name']}"
                        all_tools.append(tool)
                except Exception as e:
                    logger.error(f"Error listing tools for {agent_name}: {e}")
                    
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": all_tools
                }
            })
        
        # Handle tools/call
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            
            # Parse agent name from tool name (format: agent_toolname)
            if "_" not in tool_name:
                raise HTTPException(status_code=400, detail="Tool name must be in format: agent_toolname")
                
            agent_name, actual_tool = tool_name.split("_", 1)
            
            if agent_name not in AGENTS:
                raise HTTPException(status_code=404, detail=f"Agent {agent_name} not found")
                
            client = MCPClient(AGENTS[agent_name])
            result = await client.call_tool(actual_tool, arguments)
            
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            })
        
        else:
            raise HTTPException(status_code=400, detail=f"Unknown method: {method}")
            
    except Exception as e:
        logger.error(f"MCP endpoint error: {e}")
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32603,
                "message": str(e)
            }
        }, status_code=500)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
