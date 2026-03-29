# Handover: Templates & UI Behavior

## Files
- `app/templates/base.html`
- `app/templates/index.html`, `_home_shell.html`
- `app/templates/project.html`, `_project_shell.html`
- `_ranking_fragment.html`, `_pair_card.html`, `_history_fragment.html`, `_access_fragment.html`, `_aspect_graph_fragment.html`

## Rendering Pattern
- Full page wrappers (`index.html`, `project.html`) embed shell partials.
- HTMX updates shells/fragments (`#home-shell`, `#project-shell`, `#ranking-fragment`, `#pair-card`, etc.).
- Scroll retention and no auto-jump configured in `base.html`.

## Ranking UI
- View modes:
  - `details`
  - `bt_scores`
  - `graph_scores`
  - `elo_scores`
  - `wdl_scores`
- Data source toggle:
  - `simulation=observed`
  - `simulation=simulated`
- Simulated mode badge shown in ranking fragment.

## Pairwise Card
- Shows next pair and per-aspect choices.
- Defaults:
  - previous stored comparison if exists,
  - otherwise suggested outcome from current state.
- Suggested defaults render in pending style and switch to confirmed style after interaction.

## Tooltip/Hover Cards
- Ranking cells use custom hover cards (not browser-only title tooltip).
- Contains W-D-L, top wins/draws, closest losses, uncertainty, progress, description.
- Legacy title tooltip path retained via template flag.

## Graph Panel
- D3 + Dagre layout.
- Better items to the right (`rankdir: RL`).
- Cycle nodes/edges visually highlighted.
- Edge simplification reduces trivial transitive edges while avoiding isolation.

