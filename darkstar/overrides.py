"""Shared, editable SME overrides persisted as JSON on the PVC.

The lead curates which engineer is the SME (or a runner-up) per domain; these override or
augment the corpus/MR-derived ranking and drive the queue suggestions. Persisted to a small
JSON file so edits are team-wide and survive restarts — darkstar's only write path (the
dashboards are otherwise read-only and the poller is the store's sole writer).

Shape: {"overrides": {domain: {"order": [key, ...], "why": str, "set": "YYYY-MM"}}, "add": {domain: [key, ...]}}
  - overrides: a full curated ranking for the domain (order[0] is the SME) — replaces the derived list.
  - add:       runner-up(s) appended to the derived list WITHOUT overriding the derived SME.
"""
from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()

# Written when the file does not yet exist — the overrides curated to date (was hardcoded in the dashboard).
_SEED: dict = {
    "overrides": {
        "VDI/WorkSpaces": {"order": ["adam", "zack", "omar"], "why": "Adam created the WorkSpaces epic and did the research; Zack is building it out", "set": "2026-07"},
        "GCP Core": {"order": ["trevor", "omar", "randall"], "why": "Trevor has 6 years of GCP experience but is new to the team, so no ticket history yet", "set": "2026-07"},
        "Composer": {"order": ["trevor", "randall"], "why": "Trevor owns the in-flight Cloud Composer 3 upgrade and has deep prior Composer experience", "set": "2026-07"},
        "IAM/RBAC": {"order": ["simon", "tom"], "why": "Simon is a staff-level engineer in an advisory role; his guidance is heavy on IAM/RBAC work", "set": "2026-07"},
        "Kubernetes/GitOps": {"order": ["simon", "adam", "vlad", "bolanle"], "why": "Simon (staff, advisory) guides most k8s work; Adam, Vlad and Bolanle are the top hands-on contributors", "set": "2026-07"},
    },
    "add": {"GitLab": ["adam"]},
}


def read(path: str) -> dict:
    """Return the persisted overrides, seeding the file on first use."""
    with _LOCK:
        return _read_locked(path)


def _read_locked(path: str) -> dict:
    if not os.path.exists(path):
        _write_locked(path, _SEED)
        return json.loads(json.dumps(_SEED))
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_locked(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, path)


def apply(path: str, op: str, domain: str, engineer: str, role: str, reason: str, month: str) -> dict:
    """Add or remove one (domain, engineer) override entry; returns the updated overrides dict.

    op "add" with role "sme" makes the engineer the domain's SME (order[0]); role "runner"
    appends them (to the override order if one exists, else to the non-overriding add list).
    op "remove" strips the engineer from both lists for the domain.
    """
    if op not in ("add", "remove"):
        raise ValueError(f"unknown op: {op!r}")
    with _LOCK:
        data = _read_locked(path)
        overrides = data.setdefault("overrides", {})
        add = data.setdefault("add", {})
        if op == "add":
            _apply_add(overrides, add, domain, engineer, role, reason, month)
        else:
            _apply_remove(overrides, add, domain, engineer)
        _write_locked(path, data)
        return data


def _apply_add(overrides: dict, add: dict, domain: str, engineer: str, role: str, reason: str, month: str) -> None:
    if role == "sme":
        entry = overrides.get(domain) or {"order": [], "why": reason or "set via UI", "set": month}
        entry["order"] = [engineer] + [k for k in entry["order"] if k != engineer]
        if reason:
            entry["why"] = reason
        entry.setdefault("set", month)
        overrides[domain] = entry
    elif role == "runner":
        if domain in overrides:
            if engineer not in overrides[domain]["order"]:
                overrides[domain]["order"].append(engineer)
        else:
            runners = add.setdefault(domain, [])
            if engineer not in runners:
                runners.append(engineer)
    else:
        raise ValueError(f"unknown role: {role!r}")


def _apply_remove(overrides: dict, add: dict, domain: str, engineer: str) -> None:
    if domain in overrides:
        overrides[domain]["order"] = [k for k in overrides[domain]["order"] if k != engineer]
        if not overrides[domain]["order"]:
            del overrides[domain]
    if domain in add:
        add[domain] = [k for k in add[domain] if k != engineer]
        if not add[domain]:
            del add[domain]
