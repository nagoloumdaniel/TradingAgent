"""The read-only monitoring dashboard (cahier v3 §23 to §34, §45, §51).

Stack: FastAPI + Jinja2 + Server-Sent Events, Python only. One process, one language, one
test runner — and no build chain to deploy beside the agent on the same Windows host.

The dashboard is a *reader*: it connects to the same database as the agent, issues SELECT
statements, and renders what ``analytics`` and the storage readers already produced. It
never recomputes an indicator, never opens an order and never writes a row (§34).

Start it with ``uv run tradingagent-web``; see ``docs/web/README.md``.
"""
