# Handover: App Bootstrap & Schema Safety

## Files
- `app/__init__.py`
- `run.py`

## App Factory
- `create_app(test_config=None)` configures Flask + SQLAlchemy, registers routes blueprint, and runs `_ensure_schema()` inside app context.

## `_ensure_schema()` Strategy
This project uses lightweight in-app schema evolution (no Alembic):
- Adds `project.public_access` if missing.
- Adds `comparison.aspect_id` if missing.
- Calls `db.create_all()` for full table creation.
- Backfills each project with at least one aspect (`Overall`).
- Backfills existing comparisons with `aspect_id` of first aspect.

## Implications
- Safe for local/dev iterative changes.
- For complex production migrations, move to explicit migration tooling.

## Run/Test Entry
- Start app: `python run.py`
- Tests: `pytest -q`

