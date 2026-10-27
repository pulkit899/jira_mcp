# Jira MCP server for Adobe CX Coworker

A small remote MCP server (Streamable HTTP) that lets Coworker search, read,
create, comment on and transition Jira issues. Works with Jira Cloud and
Jira Server/Data Center.

## Tools

| Tool | Type | What it does |
|---|---|---|
| `search_issues` | read | JQL search, returns key/summary/status/assignee/URL |
| `get_issue` | read | Full issue with description and last 10 comments |
| `list_projects` | read | Projects the account can see |
| `get_transitions` | read | Workflow moves available for an issue |
| `create_issue` | write | New Task/Story/Bug/Epic |
| `update_issue` | write | Summary, description, priority, labels |
| `add_comment` | write | Comment on an issue |
| `transition_issue` | write | Move to a status by name, optional comment |

Write tools are disabled when `JIRA_READ_ONLY=true`.

## 1. Get credentials

- **Jira Cloud:** create an API token at
  `id.atlassian.com/manage-profile/security/api-tokens`. Use a service account
  if this is shared, since every action runs with that account's permissions.
- **Server/Data Center:** create a Personal Access Token in your Jira profile and
  set `JIRA_DEPLOYMENT=server`.
- Generate a server key for Coworker: `python -c "import secrets;print(secrets.token_urlsafe(32))"`

## 2. Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill it in
set -a; source .env; set +a
python server.py       # -> http://localhost:8000/mcp , health at /health
```

## 3. Deploy (needs a public HTTPS URL)

Coworker runs in Adobe's cloud, so `localhost` won't work. Any container host is fine:

```bash
docker build -t jira-mcp .
# Google Cloud Run example
gcloud run deploy jira-mcp --source . --region asia-south1 \
  --set-env-vars JIRA_BASE_URL=...,JIRA_EMAIL=...,JIRA_DEPLOYMENT=cloud \
  --set-secrets JIRA_API_TOKEN=jira-token:latest,MCP_API_KEY=mcp-key:latest \
  --allow-unauthenticated
```

Keep `JIRA_API_TOKEN` and `MCP_API_KEY` in a secret manager, not plain env vars.
`--allow-unauthenticated` only opens the URL; every request still needs the API key.

## 4. Add to Coworker

1. Side rail → **MCP Servers** → **Add MCP Server**
2. Server name: `jira`
3. MCP URL: `https://<your-host>/mcp`
4. Authentication: pick the non-Passthrough / custom-header option (labels may vary).
   This server checks its own key, not your Adobe identity.
5. **Add headers** → `X-Api-Key` = your `MCP_API_KEY`
6. **Add**, then start a new chat and try:
   *"Find my open AEP issues updated this week"* or
   *"Create a Story in AEP: QA the transaction-frequency audience"*

## Notes

- Built on MCP Python SDK **v2** (`MCPServer`). v1 code using `FastMCP` won't run on v2.
- Cloud uses REST API v3 (`/search/jql`, ADF text); Server/DC uses REST API v2.
- Runs stateless, so you can scale it horizontally.
