"""
Jira MCP server for Adobe CX Coworker (or any remote-MCP client).

Transport : Streamable HTTP at  https://<your-host>/mcp
Server auth: every request must send header  X-Api-Key: <MCP_API_KEY>
Jira auth : Jira Cloud  -> email + API token (basic)
            Jira Server/Data Center -> personal access token (bearer)

Built on the MCP Python SDK v2 (`MCPServer`, formerly `FastMCP`).
"""

from __future__ import annotations

import hmac
import os
from typing import Any

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# ---------------------------------------------------------------- config ---
JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")  # https://acme.atlassian.net
JIRA_DEPLOYMENT = os.getenv("JIRA_DEPLOYMENT", "cloud").lower()  # cloud | server
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
MCP_API_KEY = os.environ["MCP_API_KEY"]  # shared secret Coworker sends
READ_ONLY = os.getenv("JIRA_READ_ONLY", "false").lower() == "true"
DEFAULT_PROJECT = os.getenv("JIRA_DEFAULT_PROJECT", "")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

IS_CLOUD = JIRA_DEPLOYMENT == "cloud"
API = f"{JIRA_BASE_URL}/rest/api/{'3' if IS_CLOUD else '2'}"

if IS_CLOUD:
    if not JIRA_EMAIL:
        raise SystemExit("JIRA_EMAIL is required for Jira Cloud")
    _auth: dict[str, Any] = {"auth": (JIRA_EMAIL, JIRA_API_TOKEN)}
else:
    _auth = {"headers": {"Authorization": f"Bearer {JIRA_API_TOKEN}"}}

client = httpx.AsyncClient(
    timeout=30,
    headers={"Accept": "application/json", **_auth.pop("headers", {})},
    **_auth,
)


class JiraError(ToolError):
    """Raised as ToolError so the message reaches the agent verbatim."""


async def jira(method: str, path: str, **kw: Any) -> Any:
    try:
        r = await client.request(method, f"{API}{path}", **kw)
    except httpx.HTTPError as exc:
        raise JiraError(f"Could not reach Jira ({type(exc).__name__}). Check JIRA_BASE_URL.") from exc
    if r.status_code >= 400:
        try:
            body = r.json()
            msg = "; ".join(body.get("errorMessages", [])) or str(body.get("errors", body))
        except ValueError:
            msg = r.text[:300]
        raise JiraError(f"Jira {r.status_code}: {msg}")
    return r.json() if r.content else None


# ------------------------------------------------------------- helpers ---
def to_adf(text: str) -> dict:
    """Plain text -> Atlassian Document Format (Cloud v3 needs ADF)."""
    paras = [p for p in text.split("\n\n")] or [""]
    content = []
    for p in paras:
        nodes: list[dict] = []
        for i, line in enumerate(p.split("\n")):
            if i:
                nodes.append({"type": "hardBreak"})
            if line:
                nodes.append({"type": "text", "text": line})
        content.append({"type": "paragraph", "content": nodes})
    return {"type": "doc", "version": 1, "content": content}


def rich(text: str) -> Any:
    return to_adf(text) if IS_CLOUD else text


def adf_to_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    inner = "".join(adf_to_text(c) for c in node.get("content", []))
    return inner + ("\n" if t in {"paragraph", "heading", "listItem", "codeBlock"} else "")


def slim(issue: dict) -> dict:
    f = issue.get("fields", {})
    who = lambda u: (u or {}).get("displayName")  # noqa: E731
    return {
        "key": issue.get("key"),
        "summary": f.get("summary"),
        "status": (f.get("status") or {}).get("name"),
        "type": (f.get("issuetype") or {}).get("name"),
        "priority": (f.get("priority") or {}).get("name"),
        "assignee": who(f.get("assignee")),
        "reporter": who(f.get("reporter")),
        "labels": f.get("labels"),
        "created": f.get("created"),
        "updated": f.get("updated"),
        "url": f"{JIRA_BASE_URL}/browse/{issue.get('key')}",
    }


def guard_write() -> None:
    if READ_ONLY:
        raise JiraError("Server is in read-only mode (JIRA_READ_ONLY=true).")


READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

mcp = MCPServer(
    name="jira",
    instructions=(
        "Tools for Jira. Use search_issues with JQL to find work items, "
        "get_issue for detail, and the write tools only when the user asks "
        "to create or change something. Always quote issue keys back."
    ),
)


# ---------------------------------------------------------------- tools ---
@mcp.tool(annotations=READ)
async def search_issues(jql: str, max_results: int = 20) -> dict:
    """Search Jira with JQL, e.g. 'project = AEP AND status != Done ORDER BY updated DESC'.
    Returns key, summary, status, assignee, priority and URL for each issue."""
    fields = "summary,status,issuetype,priority,assignee,reporter,labels,created,updated"
    max_results = max(1, min(max_results, 100))
    if IS_CLOUD:
        data = await jira("GET", "/search/jql",
                          params={"jql": jql, "maxResults": max_results, "fields": fields})
    else:
        data = await jira("GET", "/search",
                          params={"jql": jql, "maxResults": max_results, "fields": fields})
    issues = [slim(i) for i in data.get("issues", [])]
    return {"count": len(issues), "issues": issues,
            "more": bool(data.get("nextPageToken")) or data.get("total", 0) > len(issues)}


@mcp.tool(annotations=READ)
async def get_issue(issue_key: str, include_comments: bool = True) -> dict:
    """Get one issue's full detail: description, status, people, and recent comments."""
    data = await jira("GET", f"/issue/{issue_key}",
                      params={"fields": "*navigable,comment,description"})
    out = slim(data)
    f = data.get("fields", {})
    desc = f.get("description")
    out["description"] = adf_to_text(desc).strip() if IS_CLOUD else (desc or "")
    if include_comments:
        comments = (f.get("comment") or {}).get("comments", [])[-10:]
        out["comments"] = [{
            "author": (c.get("author") or {}).get("displayName"),
            "created": c.get("created"),
            "body": adf_to_text(c.get("body")).strip() if IS_CLOUD else c.get("body"),
        } for c in comments]
    return out


@mcp.tool(annotations=READ)
async def list_projects() -> list[dict]:
    """List Jira projects the configured account can see (key and name)."""
    if IS_CLOUD:
        data = await jira("GET", "/project/search", params={"maxResults": 100})
        items = data.get("values", [])
    else:
        items = await jira("GET", "/project")
    return [{"key": p["key"], "name": p["name"]} for p in items]


@mcp.tool(annotations=WRITE)
async def create_issue(summary: str, description: str = "", project_key: str = "",
                       issue_type: str = "Task", priority: str = "",
                       labels: list[str] | None = None,
                       assignee_account_id: str = "") -> dict:
    """Create a Jira issue. issue_type is e.g. Task, Story, Bug, Epic.
    project_key falls back to the server's default project if omitted."""
    guard_write()
    project = project_key or DEFAULT_PROJECT
    if not project:
        raise JiraError("project_key is required (no JIRA_DEFAULT_PROJECT set).")
    fields: dict[str, Any] = {
        "project": {"key": project},
        "summary": summary,
        "issuetype": {"name": issue_type},
    }
    if description:
        fields["description"] = rich(description)
    if priority:
        fields["priority"] = {"name": priority}
    if labels:
        fields["labels"] = labels
    if assignee_account_id:
        fields["assignee"] = ({"accountId": assignee_account_id} if IS_CLOUD
                              else {"name": assignee_account_id})
    data = await jira("POST", "/issue", json={"fields": fields})
    return {"key": data["key"], "url": f"{JIRA_BASE_URL}/browse/{data['key']}"}


@mcp.tool(annotations=WRITE)
async def add_comment(issue_key: str, comment: str) -> dict:
    """Add a comment to an issue."""
    guard_write()
    data = await jira("POST", f"/issue/{issue_key}/comment", json={"body": rich(comment)})
    return {"issue": issue_key, "comment_id": data.get("id")}


@mcp.tool(annotations=READ)
async def get_transitions(issue_key: str) -> list[dict]:
    """List the workflow transitions currently available for an issue."""
    data = await jira("GET", f"/issue/{issue_key}/transitions")
    return [{"id": t["id"], "name": t["name"], "to_status": t["to"]["name"]}
            for t in data.get("transitions", [])]


@mcp.tool(annotations=WRITE)
async def transition_issue(issue_key: str, to_status: str, comment: str = "") -> dict:
    """Move an issue to a new status by status or transition name (e.g. 'In Progress', 'Done')."""
    guard_write()
    data = await jira("GET", f"/issue/{issue_key}/transitions")
    want = to_status.strip().lower()
    match = next((t for t in data.get("transitions", [])
                  if want in (t["name"].lower(), t["to"]["name"].lower())), None)
    if not match:
        options = ", ".join(t["to"]["name"] for t in data.get("transitions", []))
        raise JiraError(f"No transition to '{to_status}'. Available: {options or 'none'}")
    body: dict[str, Any] = {"transition": {"id": match["id"]}}
    if comment:
        body["update"] = {"comment": [{"add": {"body": rich(comment)}}]}
    await jira("POST", f"/issue/{issue_key}/transitions", json=body)
    return {"issue": issue_key, "status": match["to"]["name"]}


@mcp.tool(annotations=WRITE)
async def update_issue(issue_key: str, summary: str = "", description: str = "",
                       priority: str = "", labels: list[str] | None = None) -> dict:
    """Update summary, description, priority or labels on an existing issue."""
    guard_write()
    fields: dict[str, Any] = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = rich(description)
    if priority:
        fields["priority"] = {"name": priority}
    if labels is not None:
        fields["labels"] = labels
    if not fields:
        raise JiraError("Nothing to update.")
    await jira("PUT", f"/issue/{issue_key}", json={"fields": fields})
    return {"issue": issue_key, "updated": sorted(fields)}


# ------------------------------------------------------------ app + auth ---
class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        sent = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(sent, MCP_API_KEY):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


app = mcp.streamable_http_app(stateless_http=True, host=HOST)
app.add_middleware(ApiKeyMiddleware)

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
