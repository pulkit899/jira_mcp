# Free hosting options

All three give you a public HTTPS URL. In every case, secrets go in the
host's secret settings, never in the code or GitHub.

| | Render (free) | Hugging Face Spaces | Google Cloud Run |
|---|---|---|---|
| Card needed | No | No | Yes (billing account) |
| Sleeps when idle | After 15 min, ~1 min to wake | After a period of no use (length not published) | Scales to zero, wakes in seconds |
| Fix for sleeping | Free pinger every 10 min | Pinger helps; not guaranteed | Not needed |
| Code visibility | Private repo OK | Space must be public for Coworker to reach it, so code is visible (secrets are not) | Private |
| Region near India | Singapore | Not selectable | Mumbai |

---

## Option A: Render + keep-alive

1. Put this folder in a **private** GitHub repo.
2. Sign up at render.com with GitHub. New → **Blueprint** → pick the repo.
   It reads `render.yaml` and asks for `JIRA_BASE_URL`, `JIRA_EMAIL`,
   `JIRA_API_TOKEN`, `JIRA_DEFAULT_PROJECT`. `MCP_API_KEY` is generated for you.
3. When it's live, open the service → Environment → copy `MCP_API_KEY`.
4. Check `https://<name>.onrender.com/health` shows `{"ok":true}`.
5. Keep it awake: create a free job at cron-job.org that GETs
   `https://<name>.onrender.com/health` every 10 minutes.
   One service running all month uses ~744 of Render's 750 free hours, so
   don't run a second free service in the same workspace.

## Option B: Hugging Face Spaces

1. Sign up at huggingface.co → New Space → SDK **Docker**, hardware **CPU basic (free)**, visibility **Public**.
2. Upload `server.py`, `requirements.txt`, `Dockerfile`, and use
   `huggingface/README.md` as the Space's `README.md`.
3. Space → Settings → **Variables and secrets**: add `JIRA_API_TOKEN` and
   `MCP_API_KEY` as **secrets**; `JIRA_BASE_URL`, `JIRA_EMAIL`,
   `JIRA_DEPLOYMENT=cloud`, `JIRA_READ_ONLY=true` as variables.
4. URL is `https://<user>-<space>.hf.space/mcp`.

## Option C: Google Cloud Run (Mumbai)

```bash
gcloud services enable run.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com
printf '%s' 'YOUR_JIRA_TOKEN' | gcloud secrets create jira-token --data-file=-
python -c "import secrets;print(secrets.token_urlsafe(32),end='')" | gcloud secrets create mcp-key --data-file=-
gcloud run deploy jira-mcp --source . --region asia-south1 --allow-unauthenticated \
  --min-instances 0 --max-instances 2 \
  --set-env-vars JIRA_BASE_URL=https://you.atlassian.net,JIRA_EMAIL=you@x.com,JIRA_DEPLOYMENT=cloud,JIRA_READ_ONLY=true \
  --set-secrets JIRA_API_TOKEN=jira-token:latest,MCP_API_KEY=mcp-key:latest
```
Set a budget alert (Billing → Budgets) at a small amount so a mistake can't cost money.

---

## Then in Coworker

MCP Servers / Integrations → Add → URL `https://<host>/mcp` →
header `X-Api-Key: <MCP_API_KEY>` (not Passthrough) → Add → new chat:
*"List my Jira projects"*.

## Security checklist

- Repo private; no `.env` committed.
- Jira token from an account that only sees the projects you need.
- Start with `JIRA_READ_ONLY=true`.
- Rotate `MCP_API_KEY` if it is ever pasted somewhere shared.
- Never share the key in chat, screenshots or docs.
