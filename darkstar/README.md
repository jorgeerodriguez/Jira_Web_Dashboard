# darkstar — Platform Engineering Jira dashboards (v2)

A self-contained FastAPI app that replaces the manual "Connect / Fetch" flow of the v1
Streamlit report with an automated store the dashboards read from. It lives in this repo but
shares no code with the Streamlit `app.py`; it has its own entrypoint, image, and workload.

## Dashboards

Served as static HTML that fetch their data client-side from `/api/*` (no Jira call at request
time — everything reads the local store):

| Route | What |
|---|---|
| `/velocity` | Completed delivery tickets per engineer per month (changelog-derived) |
| `/lead-time` | Lead / cycle time for delivered stories |
| `/delivery-forecast` | Monte-Carlo burn-down for the open Initiative + Features |
| `/intake` | Triage queue, team capacity, and the SME suggestion matrix |

## Architecture

- **Store** (`store.py`) — one DuckDB file (on a PVC in prod). Tables: `issues`,
  `transitions` (append-only status changes from changelogs), `sync_meta`, and — for the SME
  matrix — `merge_requests` + `mr_files`. All timestamps are naive UTC.
- **Jira poller** (`ingest.py`) — incremental `updated >= watermark` pull plus per-changed-issue
  changelog; completion is measured as the earliest transition to `Done` (resolutiondate is
  null on ~85% of issues), attributed to the America/Denver business month.
- **GitLab ingest** (`gitlab_ingest.py`) — pulls merged MRs (rolling **6-month** window) from
  the PE groups `audacy-inc/devops` + `audacy-inc/gcp`, attributes each to a roster member
  (`roster.GITLAB_USERNAMES`), and stores changed file paths. `gitlab_domains.py` tags each MR
  to expertise domains from its repo + file paths — a far denser signal than Jira titles.
- **App** (`app.py`) — read-only `/api/*` endpoints, `/health`, and the one write path,
  `POST /api/overrides` (shared SME overrides, see below).

## Intake specifics

- **Capacity** — spare = `max(0, velocity − done_this_month) − WIP`, so it reflects month-to-date
  progress rather than a static monthly figure.
- **SME matrix** — per-domain top contributor + runners-up, blending Jira title tagging with
  GitLab MR authorship (additive). Domains are grouped **GCP / AWS / Other**; specialized
  services (EKS, GKE, ECS, OpenSearch, MSK, VertexAI, Kubeflow Pipelines, Bedrock Agents,
  AI Plugins) are pulled out of the coarse `AWS Core` / `GCP Core` buckets.
- **Overrides** — the lead can force an SME/runner-up per domain via the panel above the matrix;
  persisted to a shared JSON on the PVC (`overrides.py`) so edits are team-wide.

## Run locally

```bash
pip install -r darkstar/requirements.txt

# read-only (dashboards) — points at an existing store
export DARKSTAR_DB_PATH=/path/to/darkstar.duckdb
uvicorn darkstar.app:app --port 8080

# Jira poll (populates issues/transitions) — needs Jira creds
export JIRA_SERVER=... JIRA_EMAIL=... JIRA_API_TOKEN=...
python -m darkstar.ingest              # or call ingest.run_sync

# GitLab MR crawl (populates merge_requests/mr_files) — needs a GitLab token
export GITLAB_TOKEN=...                 # locally: $(glab config get token -h gitlab.com)
python -c "from darkstar import store, gitlab_ingest; from datetime import datetime, timezone; \
c=store.connect('$DARKSTAR_DB_PATH'); store.initialize_schema(c); \
gitlab_ingest.run_gitlab_sync(c, datetime.now(timezone.utc).replace(tzinfo=None), 180)"
```

Env: `DARKSTAR_DB_PATH` (store path), `DARKSTAR_OVERRIDES_PATH` (SME overrides JSON; defaults
alongside the store), `DARKSTAR_POLL_INTERVAL_SECONDS`, `DARKSTAR_FULL_RECONCILE_SECONDS`.

## Not done yet

Deploy: convert the workload to a 1-replica StatefulSet + gp3 PVC, point the readiness/liveness
probes at `/health`, start the Jira + GitLab pollers from app startup, and add a `GITLAB_TOKEN`
secret alongside the Jira SOPS secret. The 6-month GitLab crawl has so far been run manually.
