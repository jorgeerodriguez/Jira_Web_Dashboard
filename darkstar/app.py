"""darkstar FastAPI application: entrypoint, health probe, landing page, and read APIs.

Run locally with:  uvicorn darkstar.app:app --port 8080
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import threading
from datetime import datetime, timezone

import duckdb
import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from darkstar import (
    config, delivery, gitlab_ingest, ingest, intake, leadtime, metrics, mr_authors, mrflow,
    overrides, slas, store, velocity,
)

logger = logging.getLogger("darkstar.app")

# The in-process pollers and the dashboards share one DuckDB connection (EBS is single-writer);
# _write_lock serializes the two pollers' writes while dashboard reads use independent cursors.
_write_lock = threading.Lock()
_GITLAB_WINDOW_DAYS: int = 180

app = FastAPI(title="darkstar", description="Platform Engineering Jira dashboards (v2)")

_DASHBOARDS = pathlib.Path(__file__).parent / "dashboards"


def _dashboard(name: str) -> HTMLResponse:
    """Serve a self-contained dashboard, read fresh per request so edits show on refresh."""
    return HTMLResponse(
        (_DASHBOARDS / f"{name}.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )

_db_handle: duckdb.DuckDBPyConnection | None = None


def _db() -> duckdb.DuckDBPyConnection:
    """Return the shared store handle (opened lazily); reads use per-call cursors.

    When the poller runs in-process it must share this same handle (wired in the
    app-integration step) — DuckDB allows only one writer per file.
    """
    global _db_handle
    if _db_handle is None:
        _db_handle = store.connect(config.db_path())
        store.initialize_schema(_db_handle)
    return _db_handle


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _run_jira_cycle(jira: object, jira_tz: object) -> None:
    """One Jira sync cycle through the shared connection, serialized against the GitLab crawl."""
    with _write_lock:
        ingest.sync_cycle(_db().cursor(), jira, _utcnow(), jira_tz)


def _run_gitlab_cycle() -> None:
    """One GitLab MR crawl through the shared connection, serialized against the Jira poll."""
    with _write_lock:
        gitlab_ingest.run_gitlab_sync(_db().cursor(), _utcnow(), _GITLAB_WINDOW_DAYS)


def _recrawl_now() -> None:
    """Crawl immediately after a roster change, logging rather than raising.

    Clearing the watermark only records that the next crawl should be a full one; on the default
    24-hour poll interval that meant a newly added author's merge requests did not appear for up to
    a day, which is indistinguishable from the add having failed. This runs the crawl there and
    then. It is serialized against the poller by _write_lock in _run_gitlab_cycle.
    """
    try:
        _run_gitlab_cycle()
    except Exception:
        logger.exception("post-add GitLab re-crawl failed; the next scheduled crawl will retry")


async def _jira_poll_loop(cfg: config.Config) -> None:
    """Poll Jira forever on the configured interval; a failed cycle is logged, not fatal."""
    jira = ingest.connect_jira(cfg)
    jira_tz = ingest.get_jira_timezone(jira)
    logger.info("jira poller started (interval %ss)", cfg.poll_interval_seconds)
    while True:
        try:
            await asyncio.to_thread(_run_jira_cycle, jira, jira_tz)
        except Exception:
            logger.exception("jira sync cycle failed")
        await asyncio.sleep(cfg.poll_interval_seconds)


async def _gitlab_poll_loop(interval_seconds: int) -> None:
    """Crawl merged MRs forever on an interval; a failed crawl is logged, not fatal."""
    logger.info("gitlab poller started (interval %ss, window %sd)", interval_seconds, _GITLAB_WINDOW_DAYS)
    while True:
        try:
            await asyncio.to_thread(_run_gitlab_cycle)
        except Exception:
            logger.exception("gitlab sync failed")
        await asyncio.sleep(interval_seconds)


@app.on_event("startup")
async def _start_pollers() -> None:
    """Launch the in-process Jira + GitLab pollers, skipping either if its secret is absent.

    Both share the app's single DuckDB connection (EBS is single-writer per file); dashboard
    reads use independent cursors. `/health` is independent of the store, so probes pass while
    the first crawl runs.
    """
    _db()  # open + initialize the shared connection before the pollers write
    try:
        cfg = config.load_config()
        asyncio.create_task(_jira_poll_loop(cfg))
    except RuntimeError as exc:
        logger.warning("jira poller not started: %s", exc)
    if os.environ.get("GITLAB_TOKEN"):
        asyncio.create_task(_gitlab_poll_loop(int(os.environ.get("DARKSTAR_GITLAB_INTERVAL_SECONDS", "86400"))))
    else:
        logger.warning("gitlab poller not started: GITLAB_TOKEN unset")


@app.get("/health")
async def health() -> JSONResponse:
    """Readiness/liveness probe target for the darkstar workload."""
    return JSONResponse({"status": "ok", "service": "darkstar"})


@app.get("/")
async def index() -> RedirectResponse:
    """Redirect the root to the intake dashboard — the team's default landing page."""
    return RedirectResponse(url="/intake")


@app.get("/velocity", response_class=HTMLResponse)
async def velocity_dashboard() -> HTMLResponse:
    """The Completed-Tickets-by-Month dashboard (fetches /api/velocity client-side)."""
    return _dashboard("velocity")


# Unlinked from the nav on request — not currently useful — but deliberately still served, so an
# existing bookmark keeps working and nothing has to be rebuilt to bring it back. Re-add the
# `<a href="lead-time">` entry to the four dashboard navs to restore it.
@app.get("/lead-time", response_class=HTMLResponse)
async def lead_time_dashboard() -> HTMLResponse:
    """The lead/cycle-time dashboard (fetches /api/lead-time client-side)."""
    return _dashboard("lead-time")


@app.get("/intake", response_class=HTMLResponse)
async def intake_dashboard() -> HTMLResponse:
    """The intake dashboard (fetches /api/intake client-side)."""
    return _dashboard("intake")


@app.get("/delivery-forecast", response_class=HTMLResponse)
async def delivery_forecast_dashboard() -> HTMLResponse:
    """The delivery-forecast dashboard (fetches /api/delivery-forecast client-side)."""
    return _dashboard("delivery-forecast")


@app.get("/slas", response_class=HTMLResponse)
async def slas_dashboard() -> HTMLResponse:
    """The self-service SLA dashboard (fetches /api/slas client-side)."""
    return _dashboard("slas")


@app.get("/api/velocity")
def api_velocity() -> JSONResponse:
    """Monthly delivery completions per engineer, read from the store (no Jira call)."""
    return JSONResponse(velocity.velocity_report(_db().cursor(), _utcnow()))


@app.get("/api/lead-time")
def api_lead_time() -> JSONResponse:
    """Lead/cycle time per delivered story, read from the store (no Jira call)."""
    return JSONResponse(leadtime.lead_time_report(_db().cursor(), _utcnow()))


@app.get("/api/intake")
def api_intake() -> JSONResponse:
    """Triage queue, capacity, SME corpus, and shared SME overrides (no Jira call)."""
    report = intake.intake_report(_db().cursor(), _utcnow())
    report["overrides"] = overrides.read(config.overrides_path())
    return JSONResponse(report)


@app.post("/api/overrides")
def set_override(payload: dict) -> JSONResponse:
    """Add or remove one shared SME override entry; writes the JSON and returns the updated set."""
    op, domain, engineer = payload.get("op"), payload.get("domain"), payload.get("engineer")
    if not op or not domain or not engineer:
        raise HTTPException(status_code=400, detail="op, domain, and engineer are required")
    role, reason = payload.get("role", "sme"), payload.get("reason", "")
    month = _utcnow().strftime("%Y-%m")
    return JSONResponse(overrides.apply(config.overrides_path(), op, domain, engineer, role, reason, month))


@app.get("/api/delivery-forecast")
def api_delivery_forecast() -> JSONResponse:
    """Forecast items (initiatives/features scope + recent pace), read from the store."""
    return JSONResponse(delivery.delivery_report(_db().cursor(), _utcnow()))


def _day(value: str, field: str) -> datetime:
    """Parse a YYYY-MM-DD lookback bound as midnight in the business timezone.

    A bad date is rejected rather than silently ignored — a dashboard quietly showing a different
    window than the control says is worse than an error.
    """
    try:
        day = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be YYYY-MM-DD, got {value!r}")
    return day.replace(tzinfo=metrics.BUSINESS_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def _since(value: str | None, fallback: datetime) -> datetime:
    """The window's lower bound: the page's shared lookback, or this view's default."""
    return fallback if not value else _day(value, "since")


def _until(value: str | None, since: datetime) -> datetime | None:
    """The window's exclusive upper bound. None means "up to now", which is the usual case.

    Bounded ranges exist because the presets include them: yesterday, last week and last month all
    end before today. An inverted window is rejected rather than served, because it produces empty
    panels that read as "the team did nothing" instead of "you asked for nothing".
    """
    if not value:
        return None
    until = _day(value, "until")
    if until <= since:
        raise HTTPException(
            status_code=400,
            detail=f"until ({value}) must be after since ({since.date().isoformat()})")
    return until


@app.get("/api/slas")
def api_slas(since: str | None = None, until: str | None = None,
             grain: str | None = None) -> JSONResponse:
    """Self-service SLA compliance, turnaround, and agent success rate (no Jira call).

    `grain` forces day/week/month row grouping; omitted, it follows the width of the window.
    """
    now = _utcnow()
    start = _since(since, slas.default_window_start(now))
    if grain is not None and grain not in metrics.GRAINS:
        raise HTTPException(
            status_code=400,
            detail=f"grain must be one of {', '.join(metrics.GRAINS)}, got {grain!r}")
    return JSONResponse(
        slas.slas_report(_db().cursor(), now, start, _until(until, start), grain))


@app.get("/api/mr-turnaround")
def api_mr_turnaround(since: str | None = None, until: str | None = None,
                      authors: str | None = None, env: str | None = None) -> JSONResponse:
    """Merge-request ready->merged turnaround per author and per day, read from the store.

    `authors` is a comma-separated list of name substrings, and `env` one of prod/nonprod/other/all.
    Both are applied server-side because the daily medians cannot be re-derived from per-author
    medians in the page.
    """
    now = _utcnow()
    roster = mr_authors.read(mr_authors.authors_path(config.db_path()))
    terms = [t.strip().lower() for t in (authors or "").split(",") if t.strip()]
    environment = (env or "all").strip().lower()
    if environment not in ("all", "prod", "nonprod", "other"):
        raise HTTPException(status_code=400, detail=f"env must be all/prod/nonprod/other, got {env!r}")
    start = _since(since, mrflow.default_window_start(now))
    return JSONResponse(mrflow.mr_turnaround_report(
        _db().cursor(), start, roster, terms, environment, _until(until, start)))


@app.get("/api/gitlab-users")
def api_gitlab_users(q: str = "") -> JSONResponse:
    """Search GitLab for users to add to the MR-turnaround table.

    Returns 503 rather than an empty list when no token is configured, so the page can say the
    picker is unavailable instead of looking like nobody matched.
    """
    query = q.strip()
    if len(query) < 2:
        return JSONResponse({"users": []})
    try:
        return JSONResponse({"users": gitlab_ingest.search_members(query, 20)})
    except RuntimeError as exc:            # no GITLAB_TOKEN configured
        raise HTTPException(status_code=503, detail=str(exc))
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"GitLab user search failed: {exc}")


@app.get("/api/mr-authors")
def api_mr_authors() -> JSONResponse:
    """The editable MR-author roster: usernames added by hand and names hidden from the table."""
    return JSONResponse(mr_authors.read(mr_authors.authors_path(config.db_path())))


@app.post("/api/mr-authors")
def set_mr_authors(payload: dict, background: BackgroundTasks) -> JSONResponse:
    """Add/remove a tracked GitLab author, or hide/show one; returns the updated roster.

    Adding bumps the roster version, which forces the next crawl to cover the full window, and
    starts that crawl immediately: an incremental pull only returns MRs updated since the
    watermark, so a newly tracked author's history would never arrive, and waiting for the next
    scheduled poll meant up to 24 hours of the author simply not appearing.
    """
    op = payload.get("op")
    username, display_name = payload.get("username", ""), payload.get("display_name", "")
    if op in ("add", "remove") and not username:
        raise HTTPException(status_code=400, detail="username is required for add/remove")
    if op == "add" and "@" in username:
        raise HTTPException(
            status_code=400,
            detail=f"{username!r} looks like an email address. Merge requests are attributed by "
                   f"GitLab username (e.g. audacy-jeremy.williams), so an email matches nothing.")
    if op in ("hide", "show") and not display_name:
        raise HTTPException(status_code=400, detail="display_name is required for hide/show")
    try:
        roster, needs_recrawl = mr_authors.apply(
            mr_authors.authors_path(config.db_path()), op, username, display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if needs_recrawl:
        # mr_authors.apply already bumped the roster version, which is what forces the next crawl
        # to be a full one — including the crawl started here, and any crawl that follows it if a
        # second author is added while this one runs. The watermark is deliberately left alone.
        background.add_task(_recrawl_now)
        logger.info("mr-author %s added; roster version bumped and a full re-crawl started", username)
    return JSONResponse({**roster, "recrawl_queued": needs_recrawl})
