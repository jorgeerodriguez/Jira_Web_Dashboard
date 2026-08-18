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
| `/intake` | Triage queue, team capacity, and the SME suggestion matrix |
| `/delivery-forecast` | Monte-Carlo burn-down for the open Initiative + Features |
| `/velocity` | Completed delivery tickets per engineer per month (changelog-derived) |
| `/lead-time` | Lead / cycle time for delivered stories |
| `/slas` | Self-service SLA compliance, agent success, and MR turnaround by author |

`/` redirects to `/intake` (the default landing page); the nav lists the dashboards in this order.

## The turnaround clock

Turnaround used to be raw calendar elapsed time, which is what made a request filed at 16:00 and
closed at 09:00 next morning read as 17 hours.

One clock, everywhere: **business hours**, `metrics.business_hours_between` — Mon–Fri 08:00–17:00
`metrics.BUSINESS_TZ` (America/Los_Angeles), **excluding company holidays**. One business day is 9h
and one business week 45h, so `SLA_TARGETS_HOURS` reads directly in working days. Audacy's users are
overwhelmingly North American, so turnaround is judged against their working day. On the pilot data
this cut the iac-request median from 21.3 calendar hours to 6.3 business hours; the difference was
nights, weekends and holidays.

Raw calendar elapsed time is **not reported anywhere**. It bills a request for nights, weekends and
holidays nobody was working, which says nothing useful about delivery speed.

Holidays come from the `holidays` package, so the rules — including observance shifts, e.g.
Independence Day 2026 falls on a Saturday and is observed Friday 2026-07-03 — never go stale.
`metrics.OBSERVED_HOLIDAY_NAMES` decides which federal days Audacy actually closes for; it excludes
Washington's Birthday, Columbus Day and Veterans Day, which most private employers work. Add
company-specific closures (floating days, shutdown weeks) to `metrics.EXTRA_HOLIDAYS`.

One property to know when reading a single row of the per-author MR table: PE also has engineers
working EET, whose own working day falls inside the Pacific night, so an MR they open and merge
inside their own hours can score near 0.0 on the business clock.

Both aggregations use `metrics.window_start` — a rolling window floored at
`metrics.SELF_SERVICE_EPOCH` (2026-03-01), including the current month, unlike
`metrics.window_months` which yields complete months only and suits the month-bucketed charts. The
floor is measured, not assumed: the first MR carrying an agent footer opened 2026-03-18, and volume
ramps 6 (Mar) / 21 (Apr) / 74 (May) / 96 (Jun) / 211 (Jul).

The two views window differently, on purpose:

- **SLA (`slas.py`) — 3 months.** Delivery turnaround improved roughly 100x over the pilot (p50 by
  month created: 406.7h Apr, 114.0h May, 18.0h Jun, 13.9h Jul, 3.0h Aug), so six months calibrates
  against a team that no longer exists. Three keeps ~200 delivered requests, enough for a stable p90.
- **MR turnaround (`mrflow.py`) — 6 months.** Tracked back to the epoch, because the point of that
  table is the whole self-service era, not just the current quarter.

## SLA targets

`SLA_TARGETS_HOURS` holds **two tiers per bucket** rather than one number, because delivery
turnaround is bimodal: on August data 39% of requests closed inside 2h while the p90 sat at 29.8h. A
single "% under T" score blends those into a figure that is wrong about both ends. `p50` is what the
common case should hit; `p90` is the tail backstop. Each is scored on the distribution — bucket
median vs `p50`, bucket p90 vs `p90` — and `within_target_pct` is reported alongside as the "how
often does the fast path actually happen" read, against the `p50` target only.

Targets are calibrated to **August 2026** capability while the window is **3 months**, so most
buckets currently read as breaching. That is deliberate and not a fault: it shows the gap between a
good month and the trailing quarter, and it resolves itself as Jun/Jul age out of the window.

Actuals below are on the Pacific 08:00–17:00 clock (p50 / p90 business hours):

| bucket | p50 target | p90 target | 3-month actual | August actual |
|---|---|---|---|---|
| iac-request | 4h | 24h | 12.4h / 101.1h | 3.3h / 20.6h |
| troubleshoot | 2h | 16h | 3.6h / 26.0h | 1.9h / 14.7h |
| tf-module | 2h | 8h | 2.3h / 4.1h | 2.3h / 2.8h |
| other | 4h | 24h | 5.4h / 55.7h | 2.7h / 29.8h |

Widening the day from 8h to 9h nudged every figure up, so **tf-module's p50 now just misses its 2h
target on August data (2.3h)** where it previously met it. Targets have not been retuned for the new
clock — that is a team call.

One further lever, independent of any target: roughly **18% of median turnaround falls after the MR
merged** — work shipped, ticket still open (August p90 for that phase alone is 14h). An
auto-transition on merge would reduce every figure here for no engineering effort.

## The turnaround clock runs only while an MR is ready

`opened_at → merged_at` measured the wrong thing. Across the 20 slowest MRs, **67% of all
attributed hours were draft time** — one 612-hour MR was marked ready fifteen minutes before it
merged, and eight MRs from a single branch each carried 140.6h of which essentially all was draft.
An MR in draft is not waiting on review; the author is still working.

`mrflow.ready_hours` therefore accrues only the spells an MR spent marked ready, from GitLab's own
`marked this merge request as **draft**` / `**ready**` system notes, stored in `mr_events`. It
handles repeated toggling, and an MR whose first event is `ready` was opened as a draft so its
clock starts there. An MR with no events at all was never a draft and is measured whole.

Three signals were tested and rejected before landing on this one, and are worth not re-trying:

| signal | verdict |
|---|---|
| comment count (`user_notes_count`) | Spearman **+0.055** against turnaround — nothing. 37% of slow MRs have zero comments, and MRs with 11+ comments have a *median of 0.3h*: discussion means attention, and attention means merged. |
| commit timestamps | Unusable. Rebasing rewrites them — a three-week-old MR carried a single commit dated four hours before its merge, with squash off. |
| batching (many MRs per branch) | Real (37% of MRs share a branch, one produced 78) but not the driver: collapsing to one figure per branch leaves p90 unchanged at 18.0h. It distorts small samples, not the aggregate. |

**Time to first review** (`first_review`) rides the same ready-clock: the first note from a human
who is neither a bot nor the MR's own author. Bots are excluded by account name
(`gitlab_ingest._BOT_USERNAMES`) because GitLab Duo comments on essentially every MR and would make
each one look reviewed within seconds. This separates "nobody looked" from "reviewed, then
iterated" — the distinction the raw turnaround cannot make.

Reading the notes costs one extra API call per MR at ingest, doubling the per-MR calls (the crawl
already fetches changed paths). Completeness is tracked by `merge_requests.events_fetched_at`, not
by whether any events exist: an MR that was never a draft and drew no comments legitimately has
none, so keying on that would make the backfill run forever.

## Production vs non-production

`gitlab_domains.environment_of` reads the deployment environment from the repo name. The check is
**ordered and token-based**, never a substring test, because `nonprod` contains `prod` — a naive
`"prod" in name` files every non-production repo as production. On the current corpus: 45 prod
repos / 737 MRs, 48 nonprod / 856, and 84 / 884 in neither. That third bucket is `other`, not a
failure: it covers dev/qa/shd repos and shared env-less ones like `gitops-k8s-team-a2` and
`tf-coreservices`, and folding it into either side would misreport both.

## Page controls

Two controls on `/slas`, both server-backed rather than cosmetic:

- **Lookback** — a date that overrides the window on *every* panel, sent as `?since=YYYY-MM-DD` to
  both `/api/slas` and `/api/mr-turnaround`. Empty means each panel uses its own default (SLA 3
  months, MR turnaround 6). Held in `localStorage` so it survives a refresh. A malformed date is a
  400, never a silently different window than the box shows.
- **MR authors** — add a GitLab username to the table, or hide a row. Persisted to
  `darkstar_mr_authors.json` beside the store (`mr_authors.py`), so edits are team-wide and survive
  restarts, exactly like the SME overrides. Hidden names are dropped from the rows *and* the team
  totals, so the total always describes what is on screen.

Adding an author is the one edit the store cannot serve on its own: the GitLab crawl is
incremental, so an author it has never attributed has no rows and an incremental pull will not
fetch their history. `POST /api/mr-authors` with `op: add` therefore clears the GitLab watermark
(`store.clear_gitlab_watermark`) to force one full-window re-crawl, and returns
`recrawl_queued: true` so the page can say so. Added authors are keyed in the store by their
**GitLab username** rather than a Jira accountId, precisely so they cannot leak into the
roster-gated views — velocity, capacity and the SME matrix all look up `ROSTER` by accountId and
simply miss.

## Population

A request counts iff it is a `metrics.DELIVERY_TYPES` issue (Story/Task/Bug/Hotfix/Sub-task) — a
Feature or Epic is a *container* for requests, and its months-long lifetime inflates the median
badly (it moved iac-request's p50 from 6.6h to 10.6h), which is why `leadtime`/`velocity`/`intake`
scope the same way.

Agent success counts terminal requests that are not abandoned. Note DEVOPS spells the abandon status
**`Will Not Do`**, not `Won't Do`; `_ABANDONED` holds both spellings plus Cancelled/Rejected, since
matching only the latter scored every abandoned request as a success and pinned the rate at 100%.

## Detecting a self-service request

Three independent signals, unioned — any one is enough, because each alone misses a slice:

| Signal | Where | Note |
|---|---|---|
| `pe-*` / `ai-generated` / `self-service` label | Jira issue | the `pe-*` labels only began 2026-05-27 |
| `pe:<skill>` label | linked GitLab MR | 302 of 2477 crawled MRs |
| `Generated with Claude Code` footer | linked GitLab MR description | 522 of 2477 — the widest signal, and it predates the labels by two months |

The MR is matched to the issue by the `DEVOPS-<n>` key in its title. On the crawled corpus the
footer alone catches **254 MRs the label misses**, and **127 issues** carrying no `pe-*` label at
all — against 135 found by labels alone. The footer usually also names the originating skill
(`via /iac-request`), which buckets the request when no `pe:` label is present.

Bucketing takes the MR's `pe:` label first, then the footer's skill, then the Jira label. Note the
Jira fallback cannot separate k8s from iac (`pe-tf-module` issues also carry `pe-iac-request`), and
15 crawled MRs carry the label as the literal string `["pe:iac-request"]` — a quoting bug in
whatever sets it, tolerated in `_MR_BUCKET_BY_LABEL` but still worth fixing at the source.

MR turnaround needs `merge_requests.opened_at` and `description` on every in-window row. Because
the GitLab crawl is incremental, rows written before those columns existed — and MRs by an author added to `MR_AUTHORS`
later — cannot be repaired by an incremental pull, so `gitlab_ingest._needs_backfill` forces **one**
full-window re-crawl while any in-window row is missing either, then returns to incremental.
Descriptions are stored verbatim (~1.3 MiB for the whole corpus) so the footer heuristics can be
retuned without another crawl.

## Architecture

- **Store** (`store.py`) — one DuckDB file (on a PVC in prod). Tables: `issues`,
  `transitions` (append-only status changes from changelogs), `sync_meta`, and — for the SME
  matrix — `merge_requests` + `mr_files`. All timestamps are naive UTC.
- **Jira poller** (`ingest.py`) — a one-time full crawl on the first run, then **incremental only**
  (`updated >= watermark`, no periodic full reconcile) plus a per-changed-issue changelog;
  completion is measured as the earliest transition to `Done` (resolutiondate is null on ~85% of
  issues), attributed to the business month (`metrics.BUSINESS_TZ`).
- **GitLab ingest** (`gitlab_ingest.py`) — a one-time full **6-month** crawl on the first run, then
  **incremental** pulls of only the MRs updated since the last sync (watermark in `gitlab_sync_meta`,
  minus a small margin), from the PE groups `audacy-inc/devops` + `audacy-inc/gcp`, plus a few
  tracked repos that live outside those groups (`_PE_PROJECT_IDS`, e.g. `tf-org`/`tf-org-v2` under
  secops). Each MR is
  attributed to a tracked author (`roster.MR_AUTHORS` = the PE roster plus `TRACKED_MR_AUTHORS`,
  non-roster contributors whose MR turnaround is measured but who must stay out of the roster-gated
  velocity/capacity/SME views) and its changed file paths stored;
  `gitlab_domains.py` tags each MR to expertise domains from its **repo + changed file paths**
  (not the diff contents or the MR description) — a far denser signal than Jira titles.
- **App** (`app.py`) — read-only `/api/*` endpoints, `/health`, and the one write path,
  `POST /api/overrides` (shared SME overrides, see below).

## How intake works

The intake queue recommends **who should pick up each unassigned ticket** by combining two
independent per-engineer signals and routing on skill first, availability second.

### Availability — spare capacity

`spare = max(0, velocity - done_this_month) - WIP`

- **velocity** is each engineer's typical monthly output — the recency-weighted average of their
  completed Jira tickets over the last three complete months (`velocity._forecast`'s `baseline`).
  The velocity dashboard additionally blends this toward the current month's pace for its own
  display, but intake intentionally uses the unblended baseline so this term does not double-count
  `done_this_month`.
- **done_this_month** (completions so far) and **WIP** (active in-progress tickets) are subtracted,
  so the number is month-to-date headroom rather than a static monthly figure.
- The capacity gauge plots `done` + WIP against the velocity tick; the 1–4 signal bars (red→green)
  shown on each suggestion encode this spare capacity.

### Expertise — the GitLab-derived domain signal (the bespoke part)

This is what makes the routing accurate. Jira ticket titles are terse and inconsistent, so tagging
them recognizes a domain in only **~63%** of the work. The primary expertise signal instead comes
from **what engineers actually build**, read from GitLab:

- For every merged MR by a tracked author (a 6-month baseline on the first sync, then kept current
  by incremental syncs, from the PE groups plus a few tracked repos), we fetch the MR's **changed
  file paths** — not the diff contents, and not the MR title/description — capped at 60 paths per MR.
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
alongside the store), `DARKSTAR_POLL_INTERVAL_SECONDS` (Jira poll cadence),
`DARKSTAR_GITLAB_INTERVAL_SECONDS` (GitLab poll cadence).

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
