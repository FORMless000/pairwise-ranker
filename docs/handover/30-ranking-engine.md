# Handover: Ranking Engine

## File
- `app/ranking.py`

## Core Concepts
- Outcome scale: `better`, `slightly_better`, `comparable`, `slightly_worse`, `worse`.
- Shared outcome weights in `OUTCOME_TO_WEIGHT`.
- Multiple scoring engines per aspect:
  - BT-like latent score (`compute_ranking_state`)
  - Graph/PageRank variant (`compute_graph_ranking_state`)
  - ELO rating (`compute_elo_ranking_state`)
  - W-D-L points (`compute_wdl_ranking_state`)

## Pair Selection
- `choose_next_pair(...)` prioritizes uncertainty and penalizes repeat asking.
- Coverage-aware fallback connects disconnected graph components first.

## Stability/Quality Signals
- `estimate_variances(...)`
- `convergence_metrics(...)`
- `derive_status(...)`
- `pair_progress_metrics(...)` for exhaustive pair coverage tracking.

## Graph Display Payload
- `graph_payload(...)` simplifies transitive edges while preserving reverse-evidence/cycle-relevant edges.
- Detects cycles and tags nodes/edges with `in_cycle`.

## Simulation Mode
- `compute_multi_aspect_state(..., simulation_mode="observed|simulated")`
- In simulated mode:
  - Computes mode-specific baseline state first.
  - Generates one synthetic comparison for each missing unordered pair per aspect using `suggest_outcome_from_state(...)`.
  - Recomputes all score engines on augmented comparisons.
  - Returns `state["simulation"]` metadata:
    - `synthetic_total`
    - `synthetic_by_aspect`
    - `coverage_before` / `coverage_after`

## Return Shape
`compute_multi_aspect_state` returns merged data used by UI:
- mode-specific `aspect_states`
- per-engine states (`bt_aspect_states`, `graph_aspect_states`, etc.)
- aggregate scores/variances/ranking/status
- convergence and pair progress
- simulation metadata

