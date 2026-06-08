"""NumProof MCP server (stdio) — exposes NumProof as tools to MCP-capable agents.

Bridges stdio MCP clients (Claude Desktop, etc.) to the hosted NumProof Streamable-HTTP
MCP endpoint. Tools: `verify_claim`, `audit_rows`, `diff_rows`. No verification logic here
— it forwards to the hosted engine.

Run:    python -m numproof.mcp
Config: {"mcpServers": {"numproof": {"command": "python", "args": ["-m", "numproof.mcp"]}}}
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = os.environ.get("NUMPROOF_URL") or os.environ.get("VERIFY_URL") or "https://numproof.com"
MCP_PATH = os.environ.get("NUMPROOF_MCP_PATH", "/mcp")

TOOLS = [
    {"name": "verify_claim",
     "description": "Exactly verify a math/finance claim -> VERIFY/REFUTE/ABSTAIN with certificate and counterexample.",
     "inputSchema": {"type": "object", "properties": {"claim": {"type": "string"}}, "required": ["claim"]}},
    {"name": "audit_rows",
     "description": "Audit spreadsheet-like rows for footing, margins, formula cells, and cell provenance.",
     "inputSchema": {"type": "object", "properties": {"rows": {"type": "array"}}, "required": ["rows"]}},
    {"name": "diff_rows",
     "description": "Compare two report versions by numeric row labels with provenance.",
     "inputSchema": {"type": "object",
                     "properties": {"rows_before": {"type": "array"}, "rows_after": {"type": "array"}},
                     "required": ["rows_before", "rows_after"]}},
]


def call_remote_tool(name: str, arguments: dict) -> dict:
    payload = {"jsonrpc": "2.0", "id": "stdio-shim", "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    req = urllib.request.Request(BASE.rstrip("/") + MCP_PATH,
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            rpc = json.loads(r.read())
        text = rpc.get("result", {}).get("content", [{}])[0].get("text", "{}")
        try:
            return json.loads(text)
        except Exception:
            return {"text": text}
    except Exception as e:
        return {"error": type(e).__name__}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            out = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                   "serverInfo": {"name": "numproof", "version": "0.1.0"}}
        elif method == "tools/list":
            out = {"tools": TOOLS}
        elif method == "tools/call":
            params = msg.get("params", {})
            res = call_remote_tool(str(params.get("name", "verify_claim")),
                                   params.get("arguments", {}) or {})
            out = {"content": [{"type": "text", "text": json.dumps(res)}]}
        else:
            out = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": out}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
