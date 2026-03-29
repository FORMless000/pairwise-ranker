# Handover: System Overview

## Purpose
Toy pairwise ranking web app with:
- project-level access control (`viewer` / `editor`, plus public modes),
- multi-aspect comparisons,
- multiple scoring views (BT, graph, ELO, W-D-L),
- optional simulated-fill ranking mode,
- HTMX-based no-reload UI.

## Runtime Stack
- Flask + Jinja templates
- Flask-SQLAlchemy + SQLite
- HTMX + Pico.css
- D3 + Dagre (aspect graph rendering)

## Main Modules
- `app/__init__.py`: app factory + lightweight schema/backfill
- `app/models.py`: ORM entities
- `app/ranking.py`: scoring engines, uncertainty/convergence, pair selection, simulation fill
- `app/routes.py`: all HTTP endpoints, auth/access, HTMX fragments, cache/dedupe logic
- `app/templates/*`: server-rendered shells + fragments

## Key Data Flow
1. UI calls project route or fragment route.
2. `routes._load_project_state(...)` fetches items/comparisons/aspects and computes state via `compute_multi_aspect_state(...)`.
3. Route builds derived display rows/tooltips and returns template.
4. HTMX swaps local fragment/shell without full reload.

## Current Notable Behaviors
- Duplicate pair comparisons are deduped per `(aspect, unordered pair)`; newest survives.
- Suggested pair outcomes are used for unrated pairs (pending-style UI).
- `simulation=simulated` computes ephemeral synthetic comparisons for all missing pairs (no DB writes).

