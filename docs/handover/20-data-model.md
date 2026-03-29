# Handover: Data Model

## File
- `app/models.py`

## Entities
- `Project`: name/description/status/public_access/timestamps; owns items, comparisons, aspects, access entries.
- `Item`: belongs to project; name, optional description, cached `score` and `confidence`.
- `Aspect`: ranking dimension within a project (`name`, `order_index`).
- `Comparison`: one judgment between two items for one aspect (`outcome`, `weight`, `created_at`).
- `User`: username + hashed password.
- `ProjectAccess`: user-project role mapping (`viewer` or `editor`), unique per `(project_id, user_id)`.

## Important Constraints/Conventions
- Comparisons are directional in storage (`item_a_id`, `item_b_id`) but many operations treat pair as unordered for dedupe/history checks.
- `password_hash` on `Project` is legacy and unused by current auth model.
- Project status is derived from computed state and persisted during observed-mode recomputes.

## Relationship Notes
- Deleting project cascades into items/comparisons/aspects/access entries.
- `Comparison.item_a`, `Comparison.item_b`, `Comparison.aspect` are eager-joined (`lazy="joined"`).

