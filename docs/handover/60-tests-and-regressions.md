# Handover: Tests & Regression Expectations

## Files
- `tests/test_ranking.py`
- `tests/test_integration.py`
- `tests/conftest.py`

## Current Test Coverage Themes
- Outcome/weight mapping and scoring monotonicity.
- Robustness on cycles and pair-selection behavior.
- Graph payload simplification/cycle handling.
- Simulation mode:
  - fills missing pairs
  - no synthetic rows when exhaustive
  - suggestion symmetry
  - simulated fragment differs from observed
  - simulated state not persisted to DB
  - all display score modes support simulated view
  - cache-stable response behavior
- Auth/access constraints and HTMX fragment responses.
- Duplicate comparison dedupe behavior.

## Useful Commands
- Full suite: `pytest -q`
- Ranking-only: `pytest -q tests/test_ranking.py`
- Integration-only: `pytest -q tests/test_integration.py`

## Known Noise
- SQLAlchemy emits `Query.get()` legacy warnings in tests; functional behavior still passes.

