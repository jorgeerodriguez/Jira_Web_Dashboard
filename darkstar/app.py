"""darkstar FastAPI application: entrypoint, health probe, landing page, and read APIs.

Run locally with:  uvicorn darkstar.app:app --port 8080
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timezone

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from darkstar import config, delivery, intake, leadtime, overrides, store, velocity

app = FastAPI(title="darkstar", description="Platform Engineering Jira dashboards (v2)")

_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>darkstar</title>
</head>
<body>
<h1>darkstar</h1>
<p>Platform Engineering Jira dashboards (v2).</p>
<ul>
<li><a href="/velocity">Velocity — Completed Tickets by Month</a></li>
<li><a href="/delivery-forecast">Delivery Forecast</a></li>
<li><a href="/lead-time">Lead/Cycle Time</a></li>
<li><a href="/intake">Intake</a></li>
</ul>
</body>
</html>"""

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


@app.get("/health")
async def health() -> JSONResponse:
    """Readiness/liveness probe target for the darkstar workload."""
    return JSONResponse({"status": "ok", "service": "darkstar"})


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Landing page linking the dashboards."""
    return HTMLResponse(_INDEX_HTML)


@app.get("/velocity", response_class=HTMLResponse)
async def velocity_dashboard() -> HTMLResponse:
    """The Completed-Tickets-by-Month dashboard (fetches /api/velocity client-side)."""
    return _dashboard("velocity")


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
