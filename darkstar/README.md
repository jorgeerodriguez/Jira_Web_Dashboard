# darkstar — Platform Engineering Jira dashboards (v2)

A self-contained FastAPI app that replaces the manual "Connect / Fetch" flow of the v1
Streamlit report with an automated store the dashboards read from. It lives in this repo but
shares no code with the Streamlit `app.py`; it has its own entrypoint and workload, and rides
the **same container image** as pe-reports (an isolated virtualenv keeps their dependencies
apart — see Deploy).

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
  the PE groups `audacy-inc/devops` + `audacy-inc/gcp`, plus a few tracked repos that live
  outside those groups (`_PE_PROJECT_IDS`, e.g. `tf-org`/`tf-org-v2` under secops). Each MR is
  attributed to a roster member (`roster.GITLAB_USERNAMES`) and its changed file paths stored;
  `gitlab_domains.py` tags each MR to expertise domains from its **repo + changed file paths**
  (not the diff contents or the MR description) — a far denser signal than Jira titles.
- **App** (`app.py`) — read-only `/api/*` endpoints, `/health`, and the one write path,
  `POST /api/overrides` (shared SME overrides, see below).

## How intake works

The intake queue recommends **who should pick up each unassigned ticket** by combining two
independent per-engineer signals and routing on skill first, availability second.

### Availability — spare capacity

`spare = max(0, velocity - done_this_month) - WIP`

- **velocity** is a recency-weighted forecast of a typical month's delivery output, derived from
  each engineer's completed Jira tickets over the last three complete months (`velocity._forecast`).
- **done_this_month** (completions so far) and **WIP** (active in-progress tickets) are subtracted,
  so the number is month-to-date headroom rather than a static monthly figure.
- The capacity gauge plots `done` + WIP against the velocity tick; the 1–4 signal bars (red→green)
  shown on each suggestion encode this spare capacity.

### Expertise — the GitLab-derived domain signal (the bespoke part)

This is what makes the routing accurate. Jira ticket titles are terse and inconsistent, so tagging
them recognizes a domain in only **~63%** of the work. The primary expertise signal instead comes
from **what engineers actually build**, read from GitLab:

- For every merged MR by a roster member (rolling 6-month window, from the PE groups plus a few
  tracked repos), we fetch the MR's **changed file paths** — not the diff contents, and not the MR
  title/description — capped at 60 paths per MR.
- `gitlab_domains.py` tags each MR to expertise domains with regex over the **repo name + those
  file paths**. For the IaC / GitOps / config work that is most of PE's output, the directory
  layout *is* the taxonomy: `.../eks-nodegroups/.../terragrunt.hcl` → EKS + Terraform;
  `clusters/.../helmrelease.yaml` → Kubernetes/GitOps; the `tf-sharedservices` repo → Route53;
  `tf-org` → IAM/RBAC.
- Each domain is counted **once per MR** (so a 300-file refactor can't dominate), and those counts
  are added to the Jira-title counts to form each engineer's per-domain `+N` skill score.

**Why it's accurate:** file paths are a dense, standardized signal, so **98.4%** of MRs tag to at
least one domain (~3.3 domains per MR) — versus ~63% from Jira titles. The expertise picture is
near-complete and reflects hands-on authorship, not merely who a ticket was assigned to.
**Tradeoff:** a path tells you *where* a change lives, not *what* it did — a one-line fix in an EKS
file still counts as EKS work.

### Domains and overrides

Domains are grouped **AWS / GCP / Other** (alpha-sorted within each, group-collapsible), with
specialized services (EKS, GKE, ECS, OpenSearch, MSK, Route53, VertexAI, Kubeflow Pipelines,
Bedrock Agents, AI Plugins) pulled out of the coarse `AWS Core` / `GCP Core` buckets, plus
3rd-party tools called out on their own (e.g. **Fastly**, a CDN, grouped under Other). Per domain
the top scorer is the **SME** and the next are **runners-up**. The lead can override the
SME/runner-up per domain from the panel above the matrix; overrides persist to a shared JSON on the
PVC (`overrides.py`) so they are team-wide.

### Putting it together

For an unassigned ticket, its summary is tagged to a **primary domain**; the suggestion is that
domain's **SME**, plus a **runner-up** and a **stretch** pick, ranked by domain skill (`+N`) first
and spare capacity second. A generic ticket that matches no domain falls back to availability alone.

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

## Deploy

darkstar ships **inside the pe-reports image**, not a separate one. The root `Dockerfile` installs
Streamlit into the system environment (pe-reports, unchanged) and darkstar into an isolated
`/opt/darkstar-venv`; `docker-entrypoint.sh` runs Streamlit by default and uvicorn/darkstar when
`APP_ENTRYPOINT=darkstar`. The two dependency sets can't co-resolve in one environment (pe-reports
pins `starlette==1.0.0`, darkstar's `fastapi` needs `starlette<0.42`), which is what forces the
venv split. So the existing `publish:pe-reports` job builds one image for both apps — there is no
separate darkstar image or pipeline. The **in-process Jira + GitLab pollers** launch from app
startup, share the store connection under a write-lock, and are each skipped if their secret is
absent; `/health` is independent of the store so probes pass during the first crawl.

darkstar's workload is a HelmRelease in `gitops-k8s-team-a2` (dev namespace) — a **1-replica
StatefulSet + gp3 PVC** at `DARKSTAR_DB_PATH`, `/health` probes, ingress, `APP_ENTRYPOINT=darkstar`,
and `image.repository` pointed at the shared `pe-reports` repo (its ImagePolicy resolves the same
tag — it's the same image). The a2 statefulSet allows a single secret, so darkstar reuses the
`pe-reports` secret for Jira creds; `GITLAB_TOKEN` is added to that same secret to enable the
GitLab poller. Because both apps share one image + tag stream, a rebuild redeploys both.
