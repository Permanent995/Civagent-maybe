"""Unciv MCP server - lets any MCP client (Claude Desktop, IDE agents) operate a running
Unciv game in real time through the in-game AgentGateway (127.0.0.1:8081).

Zero dependencies: speaks MCP over stdio (newline-delimited JSON-RPC 2.0).

Run:  python -m civagent.mcp_server          (gateway must be up: start the game first)
Register (Claude Desktop style):
    {"mcpServers": {"unciv": {"command": "python",
                              "args": ["-m", "civagent.mcp_server"],
                              "env": {"UNCIV_GATEWAY": "http://127.0.0.1:8081"}}}}

Tools expose exactly the gateway surface: read the world (observation slice), act through
the developer console (gm = full tree / agent = whitelisted), manage agent seats, inspect
trust. The gateway only binds localhost; the MCP server only talks to it.
"""

import json
import os
import sys
import urllib.request

GATEWAY = os.environ.get("UNCIV_GATEWAY", "http://127.0.0.1:8081").rstrip("/")
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "game_status",
        "description": "Is a game running and reachable? Returns gateway health (gameId, turn).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_observation",
        "description": "Full ObservationSlice for one civ: terrain-summarized world view "
                       "(fog-trimmed): gold/science/culture stocks+deltas, cities, known civs "
                       "with relations, faction balance, recent engine events.",
        "inputSchema": {
            "type": "object",
            "required": ["civID"],
            "properties": {"civID": {"type": "string", "description": "civ id, e.g. Rome"}},
        },
    },
    {
        "name": "run_console_gm",
        "description": "Run ANY developer-console command in the live game (full GM tier). "
                       "Examples: 'civ addstat Gold 100 Rome', 'unit add Warrior Rome', "
                       "'city add Rome', 'tile setterrain Tundra' (needs selected tile in-game), "
                       "'diplomacy meet Rome Egypt', 'game setturn 50'. "
                       "Multi-word civ names must be quoted: 'diplomacy make-peace \"Sheng Tang\" Egypt'.",
        "inputSchema": {
            "type": "object",
            "required": ["command"],
            "properties": {"command": {"type": "string"}},
        },
    },
    {
        "name": "run_console_agent",
        "description": "Run a console command under the agent whitelist (only diplomacy list / "
                       "declare-war / make-peace / denounce from registered agent seats).",
        "inputSchema": {
            "type": "object",
            "required": ["command"],
            "properties": {"command": {"type": "string"}},
        },
    },
    {
        "name": "manage_seats",
        "description": "List or set which civs are CivAgent seats. Set replaces the whole list.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "set": {"type": "array", "items": {"type": "string"},
                        "description": "New seat list; omit to just list current seats"},
            },
        },
    },
    {
        "name": "trust_report",
        "description": "Placeholder for brain-side trust tables. The brain (port 2335) serves "
                       "GET /v1/trust?gameId=&civID=; this tool proxies it so an MCP client can "
                       "audit why an agent trusts or distrusts someone.",
        "inputSchema": {
            "type": "object",
            "required": ["gameId", "civID"],
            "properties": {"gameId": {"type": "string"}, "civID": {"type": "string"}},
        },
    },
]


def _http(method: str, url: str, body: bytes = None) -> tuple:
    request = urllib.request.Request(url, method=method, data=body)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except Exception as exception:
        return 0, json.dumps({"error": f"{type(exception).__name__}: {exception}"})


def call_tool(name: str, arguments: dict) -> str:
    if name == "game_status":
        status, body = _http("GET", f"{GATEWAY}/healthz")
        return f"HTTP {status}: {body}"
    if name == "get_observation":
        status, body = _http("GET", f"{GATEWAY}/v1/slice?civ={urllib.request.quote(arguments['civID'])}")
        return body
    if name == "run_console_gm":
        status, body = _http("POST", f"{GATEWAY}/console/gm",
                             json.dumps({"command": arguments["command"]}).encode())
        return f"HTTP {status}: {body}"
    if name == "run_console_agent":
        status, body = _http("POST", f"{GATEWAY}/console/agent",
                             json.dumps({"command": arguments["command"]}).encode())
        return f"HTTP {status}: {body}"
    if name == "manage_seats":
        if "set" in arguments:
            status, body = _http("POST", f"{GATEWAY}/seats",
                                 json.dumps({"agentSeats": arguments["set"]}).encode())
        else:
            status, body = _http("GET", f"{GATEWAY}/seats")
        return f"HTTP {status}: {body}"
    if name == "trust_report":
        brain = os.environ.get("CIVAGENT_BRAIN", "http://127.0.0.1:2335").rstrip("/")
        query = urllib.request.quote(f"gameId={arguments['gameId']}&civID={arguments['civID']}", safe="=&?")
        status, body = _http("GET", f"{brain}/v1/trust?{query}")
        return f"HTTP {status}: {body}"
    return json.dumps({"error": f"unknown tool {name}"})


def handle(request: dict) -> dict:
    method = request.get("method", "")
    request_id = request.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "unciv-mcp", "version": "0.1.0"},
        }}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = request.get("params", {})
        text = call_tool(params.get("name", ""), params.get("arguments", {}) or {})
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "content": [{"type": "text", "text": text}]
        }}
    if request_id is not None:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": method}}
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except Exception:
            continue
        response = handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
