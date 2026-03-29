# Toy Pairwise Ranker

Minimal Flask web app for ranking items through pairwise comparisons and multi-aspect scoring:

- `better than`
- `slightly better than`
- `comparable`
- `slightly worse than`
- `worse than`

It uses a lightweight Bradley-Terry-style score model with uncertainty-aware next-pair selection.  
It supports:
- user sign-in (auto-create first login, hashed passwords)
- project roles (`viewer` / `editor`) + public access modes
- aspect-specific and aggregate rankings (total/average)
- multiple score views (BT, Graph, ELO, W-D-L)
- deterministic simulated-fill ranking mode (`observed` vs `simulated`)
- import/export (items-only and full-project JSON)
- pair dedupe (latest result wins per aspect+pair)

## Stack

- Flask + Jinja
- HTMX
- Pico.css
- SQLite + Flask-SQLAlchemy

## Run

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Then open `http://127.0.0.1:5000`.

## Test

```powershell
pytest -q
```

## Handover Docs (for AI/engineer onboarding)

See `docs/handover/`:

- `docs/handover/00-system-overview.md` - architecture and runtime flow
- `docs/handover/10-app-and-schema.md` - app factory and schema/backfill behavior
- `docs/handover/20-data-model.md` - ORM entities and relationships
- `docs/handover/30-ranking-engine.md` - scoring engines, pair selection, simulation
- `docs/handover/40-routes-and-state.md` - routes, auth, caching, dedupe
- `docs/handover/50-templates-and-ui.md` - HTMX shell/fragments and UX behavior
- `docs/handover/60-tests-and-regressions.md` - test scope and regression expectations

