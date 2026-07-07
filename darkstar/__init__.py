"""darkstar — Platform Engineering Jira dashboards (v2).

Self-contained FastAPI app. Polls Jira on an interval into a local DuckDB store
(on a persistent volume) and serves read-only dashboards from that store.
"""
