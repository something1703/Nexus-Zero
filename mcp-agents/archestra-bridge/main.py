"""
Archestra MCP Bridge
====================
HTTP JSON-RPC server that proxies MCP requests to Nexus agent SSE endpoints.
Archestra → HTTP → Bridge → SSE → Agents
"""

import os
import logging
from typing import Any, Dict
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("archestra-bridge")

# Agent SSE endpoints
AGENTS = {
    "sentinel": "https://nexus-sentinel-agent-833613368271.us-central1.run.app",
    "detective": "https://nexus-detective-agent-833613368271.us-central1.run.app",
    "historian": "https://nexus-historian-agent-833613368271.us-central1.run.app",
    "mediator": "https://nexus-mediator-agent-833613368271.us-central1.run.app",
    "executor": "https://nexus-executor-agent-833613368271.us-central1.run.app",
}

# Cache for agent sessions
agent_sessions = {}

app = FastAPI(title="Nexus-Zero Archestra Bridge")


async def get_agent_session(agent_name: str) -> Dict[str, str]:
    """Get or create SSE session for an agent"""
    if agent_name in agent_sessions:
        return agent_sessions[agent_name]
    
    base_url = AGENTS[agent_name]
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("GET", f"{base_url}/sse") as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data.startswith("/messages/"):
                            message_url = f"{base_url}{data}"
                            session_id = data.split("session_id=")[1] if "session_id=" in data else None
                            
                            agent_sessions[agent_name] = {
                                "message_url": message_url,
                                "session_id": session_id
                            }
                            logger.info(f"Connected to {agent_name}: {session_id}")
                            return agent_sessions[agent_name]
                            
    except Exception as e:
        logger.error(f"Failed to connect to {agent_name}: {e}")
        raise


async def call_agent_tool(agent_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
    """Call a tool on a specific agent via its SSE endpoint"""
    session = await get_agent_session(agent_name)
    
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        },
        "id": 1
    }
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(session["message_url"], json=payload)
            
            if response.status_code != 200:
                return {"error": f"Agent returned {response.status_code}: {response.text}"}
                
            result = response.json()
            
            if "result" in result:
                return result["result"]
            elif "error" in result:
                return {"error": result["error"]}
            else:
                return result
                
    except Exception as e:
        logger.error(f"Error calling {tool_name} on {agent_name}: {e}")
        return {"error": str(e)}


async def list_agent_tools(agent_name: str) -> list:
    """Get tools from a specific agent"""
    session = await get_agent_session(agent_name)
    
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "params": {},
        "id": 1
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(session["message_url"], json=payload)
            
            if response.status_code != 200:
                logger.error(f"Failed to list tools for {agent_name}: {response.text}")
                return []
                
            result = response.json()
            return result.get("result", {}).get("tools", [])
            
    except Exception as e:
        logger.error(f"Error listing tools for {agent_name}: {e}")
        return []


@app.get("/")
async def health_check():
    """Health check"""
    return {
        "service": "Nexus-Zero Archestra Bridge",
        "status": "running",
        "agents": list(AGENTS.keys())
    }


@app.post("/mcp")
async def mcp_http_endpoint(request: Request):
    """
    HTTP MCP endpoint that Archestra can POST to.
    Implements full MCP JSON-RPC 2.0 protocol.
    """
    try:
        body = await request.json()
        method = body.get("method")
        params = body.get("params", {})
        request_id = body.get("id", 1)
        
        logger.info(f"MCP request: method={method}, id={request_id}")
        
        # Handle initialize
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
        
        # Handle initialized notification
        elif method == "notifications/initialized":
            # This is a notification, no response needed
            return JSONResponse({"jsonrpc": "2.0"}, status_code=200)
        
        # Handle tools/list
        elif method == "tools/list":
            all_tools = []
            for agent_name, base_url in AGENTS.items():
                try:
                    tools = await list_agent_tools(agent_name)
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
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": "Tool name must be in format: agent_toolname"
                    }
                })
                
            agent_name, actual_tool = tool_name.split("_", 1)
            
            if agent_name not in AGENTS:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": f"Agent {agent_name} not found"
                    }
                })
                
            result = await call_agent_tool(agent_name, actual_tool, arguments)
            
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            })
        
        else:
            logger.warning(f"Unknown method: {method}")
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            })
            
    except Exception as e:
        logger.error(f"MCP endpoint error: {e}", exc_info=True)
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": body.get("id", 1) if 'body' in locals() else 1,
            "error": {
                "code": -32603,
                "message": str(e)
            }
        }, status_code=500)


if __name__ == "__main__":
    import uvicorn
    PORT = int(os.environ.get("PORT", 8080))
    HOST = os.environ.get("HOST", "0.0.0.0")
    
    logger.info(f"Starting Archestra Bridge on {HOST}:{PORT}")
    logger.info(f"Proxying to {len(AGENTS)} agents: {list(AGENTS.keys())}")
    logger.info("HTTP MCP endpoint: /mcp")
    
    uvicorn.run(app, host=HOST, port=PORT)
