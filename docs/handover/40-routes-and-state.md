# Handover: Routes, Auth, State Loading

## File
- `app/routes.py`

## Auth & Access
- Session auth via `session["user_id"]`.
- Sign-in auto-creates unknown users (hashed password).
- Effective role resolver combines:
  - project `public_access`
  - optional `ProjectAccess` override.
- Helpers:
  - `_require_viewer(project)`
  - `_require_editor(project)`

## Project State Loader
- `_load_project_state(...)` is the main data gateway for dashboards/fragments.
- Supports:
  - `scoring_mode` derived from view
  - `simulation_mode` from query param
- Simulated mode caching:
  - `_SIMULATION_CACHE` keyed by project/version/mode signature.
  - `_project_signature(...)` uses item/aspect/comparison counts + max ids.
  - `_invalidate_simulation_cache(project_id)` called after mutating actions.
- Observed-mode only: persists project status and item score/confidence caches.

## Dedupe Behavior
- `_dedupe_project_comparisons(project_id)` removes older duplicate comparisons per `(aspect_id, unordered item pair)`.
- `compare(...)`:
  - removes existing same-pair rows for each aspect before insert,
  - runs project-wide dedupe pass,
  - commits, invalidates simulation cache, recalculates state.

## Display/Mode Parameters
- `_dashboard_params()` parses:
  - `view`, `history`, `sort_by`, `graph_aspect_id`, `simulation`
- `_scoring_mode_for_view()` maps view to engine.

## Important Endpoints
- Auth: `POST /signin`, `POST /signout`
- Home: `GET /`, `POST /projects`, `POST /projects/import`
- Project shell: `GET /projects/<id>`
- Ranking fragments: `GET /projects/<id>/ranking-fragment`
- Pair card / submit: `GET /projects/<id>/next-pair`, `POST /projects/<id>/compare`
- Graph: `GET /projects/<id>/aspect-graph`, `GET /projects/<id>/aspect-graph-data`
- Access/aspects/items/public access mutators under `/projects/<id>/...`
- Export: `GET /projects/<id>/export`

