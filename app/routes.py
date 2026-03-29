from __future__ import annotations

import json
import copy
from datetime import datetime

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models import Aspect, Comparison, Item, Project, ProjectAccess, User
from app.ranking import OUTCOME_TO_WEIGHT, compute_multi_aspect_state, suggest_outcome_from_state


bp = Blueprint("routes", __name__)
ROLE_NONE = "none"
ROLE_VIEWER = "viewer"
ROLE_EDITOR = "editor"
ROLE_LEVEL = {ROLE_NONE: 0, ROLE_VIEWER: 1, ROLE_EDITOR: 2}
PUBLIC_TO_ROLE = {"private": ROLE_NONE, "public_view": ROLE_VIEWER, "public_edit": ROLE_EDITOR}
_SIMULATION_CACHE: dict[tuple, dict] = {}


def _project_signature(items: list[Item], aspects: list[Aspect], comparisons: list[Comparison]) -> tuple:
    return (
        len(items),
        max((item.id for item in items), default=0),
        len(aspects),
        max((aspect.id for aspect in aspects), default=0),
        len(comparisons),
        max((comparison.id for comparison in comparisons), default=0),
    )


def _invalidate_simulation_cache(project_id: int) -> None:
    stale_keys = [key for key in _SIMULATION_CACHE.keys() if key[0] == project_id]
    for key in stale_keys:
        _SIMULATION_CACHE.pop(key, None)


def _dedupe_project_comparisons(project_id: int) -> int:
    rows = (
        Comparison.query.filter_by(project_id=project_id)
        .order_by(Comparison.created_at.desc(), Comparison.id.desc())
        .all()
    )
    kept: set[tuple[int | None, int, int]] = set()
    removed = 0
    for row in rows:
        left, right = sorted((row.item_a_id, row.item_b_id))
        key = (row.aspect_id, left, right)
        if key in kept:
            db.session.delete(row)
            removed += 1
            continue
        kept.add(key)
    return removed


def _current_user() -> User | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


def _require_user() -> User:
    user = _current_user()
    if not user:
        abort(401)
    return user


def _effective_role(project: Project, user: User | None) -> str:
    role = PUBLIC_TO_ROLE.get(project.public_access or "private", ROLE_NONE)
    if user:
        has_entries = ProjectAccess.query.filter_by(project_id=project.id).first() is not None
        if not has_entries:
            return ROLE_EDITOR
        entry = ProjectAccess.query.filter_by(project_id=project.id, user_id=user.id).first()
        if entry and ROLE_LEVEL.get(entry.role, 0) > ROLE_LEVEL.get(role, 0):
            role = entry.role
    return role


def _require_viewer(project: Project) -> tuple[User | None, str]:
    user = _current_user()
    role = _effective_role(project, user)
    if ROLE_LEVEL[role] < ROLE_LEVEL[ROLE_VIEWER]:
        abort(403)
    return user, role


def _require_editor(project: Project) -> User:
    user = _require_user()
    role = _effective_role(project, user)
    if ROLE_LEVEL[role] < ROLE_LEVEL[ROLE_EDITOR]:
        abort(403)
    return user


def _project_or_404(project_id: int) -> Project:
    return Project.query.get_or_404(project_id)


def _project_aspects(project_id: int) -> list[Aspect]:
    aspects = (
        Aspect.query.filter_by(project_id=project_id).order_by(Aspect.order_index.asc(), Aspect.id.asc()).all()
    )
    if aspects:
        return aspects
    aspect = Aspect(project_id=project_id, name="Overall", order_index=0)
    db.session.add(aspect)
    db.session.commit()
    return [aspect]


def _load_project_state(
    project: Project,
    score_mode: str = "average",
    selected_aspect_id: int | None = None,
    scoring_mode: str = "bt",
    simulation_mode: str = "observed",
) -> dict:
    items = Item.query.filter_by(project_id=project.id).order_by(Item.created_at.asc()).all()
    comparisons = (
        Comparison.query.filter_by(project_id=project.id).order_by(Comparison.created_at.desc()).all()
    )
    aspects = _project_aspects(project.id)
    if simulation_mode == "simulated":
        signature = _project_signature(items, aspects, comparisons)
        cache_key = (
            project.id,
            scoring_mode,
            simulation_mode,
            score_mode,
            selected_aspect_id or 0,
            signature,
        )
        cached_state = _SIMULATION_CACHE.get(cache_key)
        if cached_state is not None:
            state = copy.deepcopy(cached_state)
        else:
            state = compute_multi_aspect_state(
                items=items,
                aspects=aspects,
                comparisons=comparisons,
                score_mode=score_mode,
                selected_aspect_id=selected_aspect_id,
                scoring_mode=scoring_mode,
                simulation_mode=simulation_mode,
            )
            _SIMULATION_CACHE[cache_key] = copy.deepcopy(state)
    else:
        state = compute_multi_aspect_state(
            items=items,
            aspects=aspects,
            comparisons=comparisons,
            score_mode=score_mode,
            selected_aspect_id=selected_aspect_id,
            scoring_mode=scoring_mode,
            simulation_mode=simulation_mode,
        )

    if simulation_mode == "observed":
        project.status = state["status"]
        for item in items:
            item.score = state["aggregate_scores"].get(item.id, 0.0)
            variance = state["aggregate_variances"].get(item.id, 1.0)
            item.confidence = 1.0 / (1.0 + variance)
        db.session.commit()

    return {"items": items, "comparisons": comparisons, "aspects": aspects, "state": state}


def _dashboard_params() -> dict:
    view_mode = request.args.get("view", "bt_scores")
    if view_mode not in {"bt_scores", "graph_scores", "elo_scores", "wdl_scores", "details"}:
        view_mode = "bt_scores"
    history_mode = request.args.get("history", "show")
    if history_mode not in {"show", "hide"}:
        history_mode = "show"
    score_mode = "average"
    selected_aspect_id = None
    sort_by = request.args.get("sort_by", "avg")
    if sort_by != "avg" and not sort_by.startswith("aspect:"):
        sort_by = "avg"
    graph_aspect_text = request.args.get("graph_aspect_id", "").strip()
    graph_aspect_id = int(graph_aspect_text) if graph_aspect_text.isdigit() else None
    simulation_mode = request.args.get("simulation", "observed")
    if simulation_mode not in {"observed", "simulated"}:
        simulation_mode = "observed"
    return {
        "view_mode": view_mode,
        "history_mode": history_mode,
        "score_mode": score_mode,
        "selected_aspect_id": selected_aspect_id,
        "sort_by": sort_by,
        "graph_aspect_id": graph_aspect_id,
        "simulation_mode": simulation_mode,
    }


def _scoring_mode_for_view(view_mode: str) -> str:
    if view_mode == "graph_scores":
        return "graph"
    if view_mode == "elo_scores":
        return "elo"
    if view_mode == "wdl_scores":
        return "wdl"
    return "bt"


def _outcome_choices() -> list[tuple[str, str]]:
    return [
        ("better", "Better than"),
        ("slightly_better", "Slightly better than"),
        ("comparable", "Comparable"),
        ("slightly_worse", "Slightly worse than"),
        ("worse", "Worse than"),
    ]


def _invert_outcome(outcome: str) -> str:
    return {
        "better": "worse",
        "slightly_better": "slightly_worse",
        "comparable": "comparable",
        "slightly_worse": "slightly_better",
        "worse": "better",
    }.get(outcome, "comparable")


def _latest_pair_outcomes(project_id: int, item_a_id: int, item_b_id: int, aspects: list[Aspect]) -> dict[int, str]:
    rows = (
        Comparison.query.filter_by(project_id=project_id)
        .filter(
            db.or_(
                db.and_(Comparison.item_a_id == item_a_id, Comparison.item_b_id == item_b_id),
                db.and_(Comparison.item_a_id == item_b_id, Comparison.item_b_id == item_a_id),
            )
        )
        .order_by(Comparison.created_at.desc(), Comparison.id.desc())
        .all()
    )
    allowed_aspects = {aspect.id for aspect in aspects}
    selected: dict[int, str] = {}
    for row in rows:
        if row.aspect_id not in allowed_aspects or row.aspect_id in selected:
            continue
        if row.item_a_id == item_a_id and row.item_b_id == item_b_id:
            selected[row.aspect_id] = row.outcome
        else:
            selected[row.aspect_id] = _invert_outcome(row.outcome)
    return selected


def _suggest_outcome_from_state(item_a_id: int, item_b_id: int, aspect_state: dict) -> str:
    return suggest_outcome_from_state(item_a_id, item_b_id, aspect_state)


def _pair_outcome_defaults(
    project_id: int,
    item_a_id: int,
    item_b_id: int,
    aspects: list[Aspect],
    aspect_states: dict,
) -> tuple[dict[int, str], dict[int, bool]]:
    previous = _latest_pair_outcomes(project_id, item_a_id, item_b_id, aspects)
    outcomes: dict[int, str] = {}
    suggested: dict[int, bool] = {}
    for aspect in aspects:
        if aspect.id in previous:
            outcomes[aspect.id] = previous[aspect.id]
            suggested[aspect.id] = False
        else:
            outcomes[aspect.id] = _suggest_outcome_from_state(
                item_a_id, item_b_id, aspect_states.get(aspect.id, {})
            )
            suggested[aspect.id] = True
    return outcomes, suggested


def _aspect_item_insights(items: list[Item], aspects: list[Aspect], comparisons: list[Comparison], aspect_states: dict) -> dict:
    item_names = {item.id: item.name for item in items}
    item_descriptions = {item.id: (item.description or "") for item in items}
    total_opponents = max(len(items) - 1, 0)
    by_aspect: dict[int, list[Comparison]] = {aspect.id: [] for aspect in aspects}
    for comp in comparisons:
        if comp.aspect_id in by_aspect:
            by_aspect[comp.aspect_id].append(comp)

    insights: dict[int, dict[int, dict]] = {aspect.id: {} for aspect in aspects}
    for aspect in aspects:
        scores = aspect_states.get(aspect.id, {}).get("scores", {})
        variances = aspect_states.get(aspect.id, {}).get("variances", {})
        per_item = {
            item.id: {
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "win_rows": [],
                "draw_rows": [],
                "loss_rows": [],
                "opponents": set(),
            }
            for item in items
        }
        for comp in by_aspect.get(aspect.id, []):
            item_a = comp.item_a_id
            item_b = comp.item_b_id
            if item_a not in per_item or item_b not in per_item:
                continue
            if comp.weight > 0:
                per_item[item_a]["wins"] += 1
                per_item[item_b]["losses"] += 1
                per_item[item_a]["opponents"].add(item_b)
                per_item[item_b]["opponents"].add(item_a)
                per_item[item_a]["win_rows"].append(
                    (scores.get(item_b, 0.0), abs(comp.weight), item_b)
                )
                per_item[item_b]["loss_rows"].append(
                    (abs(scores.get(item_b, 0.0) - scores.get(item_a, 0.0)), abs(comp.weight), item_a)
                )
            elif comp.weight < 0:
                per_item[item_b]["wins"] += 1
                per_item[item_a]["losses"] += 1
                per_item[item_a]["opponents"].add(item_b)
                per_item[item_b]["opponents"].add(item_a)
                per_item[item_b]["win_rows"].append(
                    (scores.get(item_a, 0.0), abs(comp.weight), item_a)
                )
                per_item[item_a]["loss_rows"].append(
                    (abs(scores.get(item_b, 0.0) - scores.get(item_a, 0.0)), abs(comp.weight), item_b)
                )
            else:
                per_item[item_a]["draws"] += 1
                per_item[item_b]["draws"] += 1
                per_item[item_a]["opponents"].add(item_b)
                per_item[item_b]["opponents"].add(item_a)
                per_item[item_a]["draw_rows"].append(
                    (scores.get(item_b, 0.0), abs(comp.weight), item_b)
                )
                per_item[item_b]["draw_rows"].append(
                    (scores.get(item_a, 0.0), abs(comp.weight), item_a)
                )

        for item in items:
            data = per_item[item.id]
            top_wins = [item_names[opponent_id] for _, _, opponent_id in sorted(data["win_rows"], reverse=True)[:3]]
            top_draws = [item_names[opponent_id] for _, _, opponent_id in sorted(data["draw_rows"], reverse=True)[:3]]
            closest_losses = [
                item_names[opponent_id] for _, _, opponent_id in sorted(data["loss_rows"], key=lambda row: (row[0], row[1]))[:3]
            ]
            compared = len(data["opponents"])
            insights[aspect.id][item.id] = {
                "wins": data["wins"],
                "draws": data["draws"],
                "losses": data["losses"],
                "top_wins": top_wins,
                "top_draws": top_draws,
                "closest_losses": closest_losses,
                "description": item_descriptions.get(item.id, ""),
                "uncertainty": float(variances.get(item.id, 1.0)),
                "progress_compared": compared,
                "progress_total": total_opponents,
                "progress_ratio": (compared / total_opponents) if total_opponents else 0.0,
            }
    return insights


def _insight_tooltip(insight: dict | None) -> str:
    if not insight:
        return "W-D-L: 0-0-0"
    lines = [f"W-D-L: {insight['wins']}-{insight['draws']}-{insight['losses']}"]
    if insight.get("top_wins"):
        lines.append(f"Top wins: {', '.join(insight['top_wins'])}")
    if insight.get("top_draws"):
        lines.append(f"Top draws: {', '.join(insight['top_draws'])}")
    if insight.get("closest_losses"):
        lines.append(f"Closest losses: {', '.join(insight['closest_losses'])}")
    if "uncertainty" in insight:
        lines.append(f"Uncertainty: {insight['uncertainty']:.3f}")
    return "\n".join(lines)


def _is_hx_request() -> bool:
    return bool(request.headers.get("HX-Request"))


def _home_context(auth_error: bool = False, import_error: bool = False) -> dict:
    user = _current_user()
    projects = Project.query.order_by(Project.updated_at.desc()).all()
    visible_projects = []
    access_map = {}
    for project in projects:
        role = _effective_role(project, user)
        if ROLE_LEVEL[role] < ROLE_LEVEL[ROLE_VIEWER]:
            continue
        visible_projects.append(project)
        access_map[project.id] = role
    return {
        "user": user,
        "projects": visible_projects,
        "access_map": access_map,
        "auth_error": auth_error,
        "import_error": import_error,
        "show_create_panel": bool(session.get("show_create_panel", True)),
    }


def _render_home(auth_error: bool = False, import_error: bool = False):
    template = "_home_shell.html" if _is_hx_request() else "index.html"
    return render_template(template, **_home_context(auth_error=auth_error, import_error=import_error))


def _render_project(project: Project):
    user, role = _require_viewer(project)
    params = _dashboard_params()
    scoring_mode = _scoring_mode_for_view(params["view_mode"])
    simulation_mode = params["simulation_mode"] if params["view_mode"] != "details" else "observed"
    selected_aspect_id = params["selected_aspect_id"] if params["score_mode"] == "aspect" else None
    data = _load_project_state(
        project,
        score_mode=params["score_mode"] if params["score_mode"] in {"total", "average"} else "average",
        selected_aspect_id=selected_aspect_id,
        scoring_mode=scoring_mode,
        simulation_mode=simulation_mode,
    )
    graph_aspect_id = params["graph_aspect_id"]
    if graph_aspect_id is None and data["aspects"]:
        graph_aspect_id = data["aspects"][0].id
    template = "_project_shell.html" if _is_hx_request() else "project.html"
    return render_template(
        template,
        user=user,
        role=role,
        can_edit=ROLE_LEVEL[role] >= ROLE_LEVEL[ROLE_EDITOR],
        project=project,
        items=data["items"],
        comparisons=data["comparisons"],
        aspects=data["aspects"],
        state=data["state"],
        outcome_choices=_outcome_choices(),
        view_mode=params["view_mode"],
        history_mode=params["history_mode"],
        score_mode=params["score_mode"],
        selected_aspect_id=selected_aspect_id,
        sort_by=params["sort_by"],
        scoring_mode=scoring_mode,
        graph_aspect_id=graph_aspect_id,
        pair_progress=data["state"].get("pair_progress", {}),
        simulation_mode=params["simulation_mode"],
    )


def _serialize_project(project: Project, mode: str) -> dict:
    aspects = _project_aspects(project.id)
    items = Item.query.filter_by(project_id=project.id).order_by(Item.id.asc()).all()
    if mode == "items_only":
        return {
            "schema_version": 1,
            "mode": "items_only",
            "project_name": project.name,
            "description": project.description,
            "items": [{"name": item.name, "description": item.description} for item in items],
        }

    comparisons = Comparison.query.filter_by(project_id=project.id).order_by(Comparison.id.asc()).all()
    return {
        "schema_version": 1,
        "mode": "full_project",
        "project": {
            "name": project.name,
            "description": project.description,
            "status": project.status,
            "public_access": project.public_access,
        },
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "score": item.score,
                "confidence": item.confidence,
            }
            for item in items
        ],
        "aspects": [{"id": aspect.id, "name": aspect.name, "order_index": aspect.order_index} for aspect in aspects],
        "comparisons": [
            {
                "aspect_id": comparison.aspect_id,
                "item_a_id": comparison.item_a_id,
                "item_b_id": comparison.item_b_id,
                "outcome": comparison.outcome,
                "weight": comparison.weight,
                "created_at": comparison.created_at.isoformat(),
            }
            for comparison in comparisons
        ],
    }


def _create_project_from_export(payload: dict, user: User) -> Project:
    mode = payload.get("mode")
    if mode not in {"items_only", "full_project"}:
        raise ValueError("Invalid export mode")

    if mode == "items_only":
        project = Project(
            name=(payload.get("project_name") or "Imported Project").strip() or "Imported Project",
            description=payload.get("description"),
            status="unstarted",
            public_access="private",
            password_hash=generate_password_hash("legacy-unused"),
        )
        db.session.add(project)
        db.session.flush()
        db.session.add(ProjectAccess(project_id=project.id, user_id=user.id, role=ROLE_EDITOR))
        db.session.add(Aspect(project_id=project.id, name="Overall", order_index=0))
        for item_payload in payload.get("items", []):
            name = (item_payload.get("name") or "").strip()
            if not name:
                continue
            db.session.add(
                Item(project_id=project.id, name=name, description=item_payload.get("description"))
            )
        db.session.commit()
        return project

    project_data = payload.get("project") or {}
    project = Project(
        name=(project_data.get("name") or "Imported Project").strip() or "Imported Project",
        description=project_data.get("description"),
        status=project_data.get("status") or "in_progress",
        public_access="private",
        password_hash=generate_password_hash("legacy-unused"),
    )
    db.session.add(project)
    db.session.flush()
    db.session.add(ProjectAccess(project_id=project.id, user_id=user.id, role=ROLE_EDITOR))

    item_id_map: dict[int, int] = {}
    for item_payload in payload.get("items", []):
        name = (item_payload.get("name") or "").strip()
        if not name:
            continue
        item = Item(
            project_id=project.id,
            name=name,
            description=item_payload.get("description"),
            score=float(item_payload.get("score", 0.0)),
            confidence=float(item_payload.get("confidence", 0.0)),
        )
        db.session.add(item)
        db.session.flush()
        old_id = item_payload.get("id")
        if isinstance(old_id, int):
            item_id_map[old_id] = item.id

    aspect_id_map: dict[int, int] = {}
    aspects_payload = payload.get("aspects") or []
    if not aspects_payload:
        aspects_payload = [{"id": 1, "name": "Overall", "order_index": 0}]
    for aspect_payload in aspects_payload:
        aspect = Aspect(
            project_id=project.id,
            name=(aspect_payload.get("name") or "Aspect").strip() or "Aspect",
            order_index=int(aspect_payload.get("order_index", 0)),
        )
        db.session.add(aspect)
        db.session.flush()
        old_id = aspect_payload.get("id")
        if isinstance(old_id, int):
            aspect_id_map[old_id] = aspect.id

    default_aspect_id = (
        Aspect.query.filter_by(project_id=project.id).order_by(Aspect.order_index.asc()).first().id
    )
    for comp_payload in payload.get("comparisons", []):
        old_a = comp_payload.get("item_a_id")
        old_b = comp_payload.get("item_b_id")
        mapped_a = item_id_map.get(old_a)
        mapped_b = item_id_map.get(old_b)
        if not mapped_a or not mapped_b or mapped_a == mapped_b:
            continue
        outcome = comp_payload.get("outcome")
        weight = comp_payload.get("weight")
        if outcome not in OUTCOME_TO_WEIGHT:
            continue
        if not isinstance(weight, int):
            weight = OUTCOME_TO_WEIGHT[outcome]
        created_at_text = comp_payload.get("created_at")
        try:
            created_at = datetime.fromisoformat(created_at_text) if created_at_text else datetime.utcnow()
        except ValueError:
            created_at = datetime.utcnow()
        db.session.add(
            Comparison(
                project_id=project.id,
                aspect_id=aspect_id_map.get(comp_payload.get("aspect_id"), default_aspect_id),
                item_a_id=mapped_a,
                item_b_id=mapped_b,
                outcome=outcome,
                weight=weight,
                created_at=created_at,
            )
        )
    db.session.commit()
    return project


@bp.post("/signin")
def sign_in():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        if _is_hx_request():
            return _render_home()
        return redirect(url_for("routes.index"))

    user = User.query.filter_by(username=username).first()
    if not user:
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
    elif not check_password_hash(user.password_hash, password):
        if _is_hx_request():
            return _render_home(auth_error=True)
        return redirect(url_for("routes.index", auth_error="1"))

    session["user_id"] = user.id
    orphan_projects = []
    for project in Project.query.order_by(Project.id.asc()).all():
        has_access = ProjectAccess.query.filter_by(project_id=project.id).first() is not None
        if not has_access:
            orphan_projects.append(project)
    for project in orphan_projects:
        db.session.add(ProjectAccess(project_id=project.id, user_id=user.id, role=ROLE_EDITOR))
    if orphan_projects:
        db.session.commit()
    session.modified = True
    if _is_hx_request():
        return _render_home()
    return redirect(url_for("routes.index"))


@bp.post("/signout")
def sign_out():
    session.pop("user_id", None)
    session.modified = True
    if _is_hx_request():
        return _render_home()
    return redirect(url_for("routes.index"))


@bp.post("/toggle-create-panel")
def toggle_create_panel():
    session["show_create_panel"] = not bool(session.get("show_create_panel", True))
    session.modified = True
    if _is_hx_request():
        return _render_home()
    return redirect(url_for("routes.index"))


@bp.get("/")
def index():
    return _render_home(
        auth_error=request.args.get("auth_error") == "1",
        import_error=request.args.get("import_error") == "1",
    )


@bp.get("/projects")
def projects_alias():
    return redirect(url_for("routes.index"))


@bp.post("/projects")
def create_project():
    user = _require_user()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    public_access = request.form.get("public_access", "private")
    if public_access not in {"private", "public_view", "public_edit"}:
        public_access = "private"
    if not name:
        if _is_hx_request():
            return _render_home()
        return redirect(url_for("routes.index"))

    project = Project(
        name=name,
        description=description,
        status="unstarted",
        public_access=public_access,
        password_hash=generate_password_hash("legacy-unused"),
    )
    db.session.add(project)
    db.session.flush()
    db.session.add(ProjectAccess(project_id=project.id, user_id=user.id, role=ROLE_EDITOR))
    db.session.add(Aspect(project_id=project.id, name="Overall", order_index=0))
    db.session.commit()
    if _is_hx_request():
        return _render_home()
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.post("/projects/import")
def import_project():
    user = _require_user()
    file = request.files.get("import_file")
    if not file:
        if _is_hx_request():
            return _render_home()
        return redirect(url_for("routes.index"))
    try:
        payload = json.load(file.stream)
        project = _create_project_from_export(payload, user)
    except (ValueError, json.JSONDecodeError, TypeError):
        if _is_hx_request():
            return _render_home(import_error=True)
        return redirect(url_for("routes.index", import_error="1"))
    if _is_hx_request():
        return _render_home()
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.get("/projects/<int:project_id>")
def project_dashboard(project_id: int):
    project = _project_or_404(project_id)
    return _render_project(project)


@bp.post("/projects/<int:project_id>/items")
def create_item(project_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    if name:
        db.session.add(Item(project_id=project.id, name=name, description=description))
        db.session.commit()
        _invalidate_simulation_cache(project.id)
    if _is_hx_request():
        return _render_project(project)
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.post("/projects/<int:project_id>/items/<int:item_id>/update")
def update_item(project_id: int, item_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    item = Item.query.filter_by(project_id=project_id, id=item_id).first_or_404()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    if name:
        item.name = name
    item.description = description
    db.session.commit()
    _invalidate_simulation_cache(project.id)
    if _is_hx_request():
        return _render_project(project)
    return redirect(url_for("routes.project_dashboard", project_id=project_id))


@bp.post("/projects/<int:project_id>/items/<int:item_id>/delete")
def delete_item(project_id: int, item_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    item = Item.query.filter_by(project_id=project_id, id=item_id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    _invalidate_simulation_cache(project.id)
    if _is_hx_request():
        return _render_project(project)
    return redirect(url_for("routes.project_dashboard", project_id=project_id))


@bp.post("/projects/<int:project_id>/aspects")
def create_aspect(project_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    name = request.form.get("name", "").strip() or "Aspect"
    max_order = (
        db.session.query(db.func.max(Aspect.order_index)).filter_by(project_id=project.id).scalar() or 0
    )
    db.session.add(Aspect(project_id=project.id, name=name, order_index=max_order + 1))
    db.session.commit()
    _invalidate_simulation_cache(project.id)
    if _is_hx_request():
        return _render_project(project)
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.post("/projects/<int:project_id>/aspects/<int:aspect_id>/rename")
def rename_aspect(project_id: int, aspect_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    aspect = Aspect.query.filter_by(project_id=project.id, id=aspect_id).first_or_404()
    name = request.form.get("name", "").strip()
    if name:
        aspect.name = name
        db.session.commit()
        _invalidate_simulation_cache(project.id)
    if _is_hx_request():
        return _render_project(project)
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.post("/projects/<int:project_id>/aspects/<int:aspect_id>/move")
def move_aspect(project_id: int, aspect_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    direction = request.form.get("direction", "up")
    aspects = _project_aspects(project.id)
    ordered_ids = [aspect.id for aspect in aspects]
    if aspect_id not in ordered_ids:
        abort(404)
    idx = ordered_ids.index(aspect_id)
    if direction == "up" and idx > 0:
        ordered_ids[idx - 1], ordered_ids[idx] = ordered_ids[idx], ordered_ids[idx - 1]
    elif direction == "down" and idx < len(ordered_ids) - 1:
        ordered_ids[idx + 1], ordered_ids[idx] = ordered_ids[idx], ordered_ids[idx + 1]
    for new_index, current_aspect_id in enumerate(ordered_ids):
        Aspect.query.filter_by(project_id=project.id, id=current_aspect_id).update({"order_index": new_index})
    db.session.commit()
    _invalidate_simulation_cache(project.id)
    if _is_hx_request():
        return _render_project(project)
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.post("/projects/<int:project_id>/aspects/<int:aspect_id>/delete")
def delete_aspect(project_id: int, aspect_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    aspects = _project_aspects(project.id)
    if len(aspects) <= 1:
        if _is_hx_request():
            return _render_project(project)
        return redirect(url_for("routes.project_dashboard", project_id=project.id))
    aspect = Aspect.query.filter_by(project_id=project.id, id=aspect_id).first_or_404()
    fallback_aspect = next(existing for existing in aspects if existing.id != aspect.id)
    Comparison.query.filter_by(project_id=project.id, aspect_id=aspect.id).update(
        {"aspect_id": fallback_aspect.id}
    )
    db.session.delete(aspect)
    db.session.commit()
    _invalidate_simulation_cache(project.id)
    if _is_hx_request():
        return _render_project(project)
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.post("/projects/<int:project_id>/public-access")
def set_public_access(project_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    public_access = request.form.get("public_access", "private")
    if public_access not in {"private", "public_view", "public_edit"}:
        public_access = "private"
    project.public_access = public_access
    db.session.commit()
    _invalidate_simulation_cache(project.id)
    if _is_hx_request():
        return _render_project(project)
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.post("/projects/<int:project_id>/access")
def update_project_access(project_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    username = request.form.get("username", "").strip()
    role = request.form.get("role", ROLE_VIEWER)
    action = request.form.get("action", "set")
    if role not in {ROLE_VIEWER, ROLE_EDITOR}:
        role = ROLE_VIEWER
    if not username:
        if _is_hx_request():
            return _render_project(project)
        return redirect(url_for("routes.project_dashboard", project_id=project.id))

    user = User.query.filter_by(username=username).first()
    if not user:
        if _is_hx_request():
            return _render_project(project)
        return redirect(url_for("routes.project_dashboard", project_id=project.id))

    entry = ProjectAccess.query.filter_by(project_id=project.id, user_id=user.id).first()
    if action == "remove":
        if entry:
            db.session.delete(entry)
            db.session.commit()
            _invalidate_simulation_cache(project.id)
        if _is_hx_request():
            return _render_project(project)
        return redirect(url_for("routes.project_dashboard", project_id=project.id))

    if entry:
        entry.role = role
    else:
        db.session.add(ProjectAccess(project_id=project.id, user_id=user.id, role=role))
    db.session.commit()
    _invalidate_simulation_cache(project.id)
    if _is_hx_request():
        return _render_project(project)
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.get("/projects/<int:project_id>/next-pair")
def next_pair(project_id: int):
    project = _project_or_404(project_id)
    _, role = _require_viewer(project)
    params = _dashboard_params()
    scoring_mode = _scoring_mode_for_view(params["view_mode"])
    simulation_mode = params["simulation_mode"] if params["view_mode"] != "details" else "observed"
    selected_aspect_id = params["selected_aspect_id"] if params["score_mode"] == "aspect" else None
    data = _load_project_state(
        project,
        score_mode=params["score_mode"] if params["score_mode"] in {"total", "average"} else "average",
        selected_aspect_id=selected_aspect_id,
        scoring_mode=scoring_mode,
        simulation_mode=simulation_mode,
    )
    pair = data["state"]["next_pair"]
    pair_items = None
    if pair:
        item_map = {item.id: item for item in data["items"]}
        pair_items = (item_map[pair[0]], item_map[pair[1]])
    selected_outcomes, suggested_outcomes = (
        _pair_outcome_defaults(
            project.id,
            pair_items[0].id,
            pair_items[1].id,
            data["aspects"],
            data["state"]["aspect_states"],
        )
        if pair_items
        else ({}, {})
    )

    return render_template(
        "_pair_card.html",
        project=project,
        pair_items=pair_items,
        aspects=data["aspects"],
        status=data["state"]["status"],
        selected_outcomes=selected_outcomes,
        suggested_outcomes=suggested_outcomes,
        can_edit=ROLE_LEVEL[role] >= ROLE_LEVEL[ROLE_EDITOR],
        outcome_choices=_outcome_choices(),
    )


@bp.post("/projects/<int:project_id>/compare")
def compare(project_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    item_a_id = int(request.form["item_a_id"])
    item_b_id = int(request.form["item_b_id"])
    if item_a_id == item_b_id:
        abort(400)

    Item.query.filter_by(project_id=project.id, id=item_a_id).first_or_404()
    Item.query.filter_by(project_id=project.id, id=item_b_id).first_or_404()
    aspects = _project_aspects(project.id)
    for aspect in aspects:
        existing_rows = (
            Comparison.query.filter_by(project_id=project.id, aspect_id=aspect.id)
            .filter(
                db.or_(
                    db.and_(Comparison.item_a_id == item_a_id, Comparison.item_b_id == item_b_id),
                    db.and_(Comparison.item_a_id == item_b_id, Comparison.item_b_id == item_a_id),
                )
            )
            .all()
        )
        for existing in existing_rows:
            db.session.delete(existing)

        field = f"outcome_{aspect.id}"
        outcome = request.form.get(field, "comparable")
        weight = OUTCOME_TO_WEIGHT.get(outcome)
        if weight is None:
            continue
        db.session.add(
            Comparison(
                project_id=project.id,
                aspect_id=aspect.id,
                item_a_id=item_a_id,
                item_b_id=item_b_id,
                outcome=outcome,
                weight=weight,
            )
        )
    _dedupe_project_comparisons(project.id)
    db.session.commit()
    _invalidate_simulation_cache(project.id)

    params = _dashboard_params()
    scoring_mode = _scoring_mode_for_view(params["view_mode"])
    simulation_mode = params["simulation_mode"] if params["view_mode"] != "details" else "observed"
    selected_aspect_id = params["selected_aspect_id"] if params["score_mode"] == "aspect" else None
    data = _load_project_state(
        project,
        score_mode=params["score_mode"] if params["score_mode"] in {"total", "average"} else "average",
        selected_aspect_id=selected_aspect_id,
        scoring_mode=scoring_mode,
        simulation_mode=simulation_mode,
    )
    pair = data["state"]["next_pair"]
    pair_items = None
    if pair:
        item_map = {item.id: item for item in data["items"]}
        pair_items = (item_map[pair[0]], item_map[pair[1]])
    selected_outcomes, suggested_outcomes = (
        _pair_outcome_defaults(
            project.id,
            pair_items[0].id,
            pair_items[1].id,
            data["aspects"],
            data["state"]["aspect_states"],
        )
        if pair_items
        else ({}, {})
    )

    response = make_response(
        render_template(
            "_pair_card.html",
            project=project,
            pair_items=pair_items,
            aspects=data["aspects"],
            status=data["state"]["status"],
            selected_outcomes=selected_outcomes,
            suggested_outcomes=suggested_outcomes,
            can_edit=True,
            outcome_choices=_outcome_choices(),
        )
    )
    response.headers["HX-Trigger"] = "ranking-updated"
    return response


@bp.post("/projects/<int:project_id>/recompute")
def recompute(project_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    params = _dashboard_params()
    scoring_mode = _scoring_mode_for_view(params["view_mode"])
    simulation_mode = params["simulation_mode"] if params["view_mode"] != "details" else "observed"
    selected_aspect_id = params["selected_aspect_id"] if params["score_mode"] == "aspect" else None
    _load_project_state(
        project,
        score_mode=params["score_mode"] if params["score_mode"] in {"total", "average"} else "average",
        selected_aspect_id=selected_aspect_id,
        scoring_mode=scoring_mode,
        simulation_mode=simulation_mode,
    )
    if request.headers.get("HX-Request"):
        response = make_response("", 204)
        response.headers["HX-Trigger"] = "ranking-updated"
        return response
    return redirect(url_for("routes.project_dashboard", project_id=project.id))


@bp.get("/projects/<int:project_id>/ranking-fragment")
def ranking_fragment(project_id: int):
    project = _project_or_404(project_id)
    _, role = _require_viewer(project)
    params = _dashboard_params()
    scoring_mode = _scoring_mode_for_view(params["view_mode"])
    simulation_mode = params["simulation_mode"] if params["view_mode"] != "details" else "observed"
    selected_aspect_id = params["selected_aspect_id"] if params["score_mode"] == "aspect" else None
    data = _load_project_state(
        project,
        score_mode=params["score_mode"] if params["score_mode"] in {"total", "average"} else "average",
        selected_aspect_id=selected_aspect_id,
        scoring_mode=scoring_mode,
        simulation_mode=simulation_mode,
    )
    sort_by = params["sort_by"]
    graph_aspect_id = params["graph_aspect_id"]
    if graph_aspect_id is None and data["aspects"]:
        graph_aspect_id = data["aspects"][0].id
    aspect_states = data["state"]["aspect_states"]
    insights = _aspect_item_insights(data["items"], data["aspects"], data["comparisons"], aspect_states)
    score_rows = []
    for item in data["items"]:
        aggregate_wins = 0
        aggregate_draws = 0
        aggregate_losses = 0
        aspect_display_scores = {}
        aspect_sort_scores = {}
        aspect_tooltips = {}
        for aspect in data["aspects"]:
            aspect_score = aspect_states.get(aspect.id, {}).get("scores", {}).get(item.id, 0.0)
            aspect_insight = insights.get(aspect.id, {}).get(item.id)
            aspect_sort_scores[aspect.id] = aspect_score
            aspect_tooltips[aspect.id] = _insight_tooltip(aspect_insight)
            if params["view_mode"] == "wdl_scores":
                wins = int(aspect_insight.get("wins", 0) if aspect_insight else 0)
                draws = int(aspect_insight.get("draws", 0) if aspect_insight else 0)
                losses = int(aspect_insight.get("losses", 0) if aspect_insight else 0)
                aggregate_wins += wins
                aggregate_draws += draws
                aggregate_losses += losses
                aspect_display_scores[aspect.id] = f"{wins}W-{draws}D-{losses}L"
            elif params["view_mode"] == "elo_scores":
                elo_rating = data["state"]["elo_aspect_states"].get(aspect.id, {}).get("ratings", {}).get(item.id, 1500.0)
                aspect_display_scores[aspect.id] = f"{elo_rating:.0f}"
            else:
                aspect_display_scores[aspect.id] = f"{aspect_score:.3f}"
        row = {
            "item": item,
            "avg_score": data["state"]["aggregate_scores"].get(item.id, 0.0),
            "aspect_scores": aspect_sort_scores,
            "display_aspect_scores": aspect_display_scores,
            "aspect_tooltips": aspect_tooltips,
            "aspect_insights": {aspect.id: insights.get(aspect.id, {}).get(item.id, {}) for aspect in data["aspects"]},
        }
        if params["view_mode"] == "wdl_scores":
            row["display_avg_score"] = f"{aggregate_wins}W-{aggregate_draws}D-{aggregate_losses}L"
            row["avg_sort_score"] = row["avg_score"]
        else:
            if params["view_mode"] == "elo_scores":
                rating_values = []
                for aspect in data["aspects"]:
                    rating_values.append(
                        data["state"]["elo_aspect_states"].get(aspect.id, {}).get("ratings", {}).get(item.id, 1500.0)
                    )
                avg_rating = (sum(rating_values) / len(rating_values)) if rating_values else 1500.0
                row["display_avg_score"] = f"{avg_rating:.0f}"
            else:
                row["display_avg_score"] = f"{row['avg_score']:.3f}"
            row["avg_sort_score"] = row["avg_score"]
        row["description"] = item.description or ""
        row["avg_uncertainty"] = float(data["state"]["aggregate_variances"].get(item.id, 1.0))
        row["avg_aspect_progress"] = {
            aspect.id: {
                "compared": int(row["aspect_insights"].get(aspect.id, {}).get("progress_compared", 0)),
                "total": int(row["aspect_insights"].get(aspect.id, {}).get("progress_total", max(len(data["items"]) - 1, 0))),
                "ratio": float(row["aspect_insights"].get(aspect.id, {}).get("progress_ratio", 0.0)),
            }
            for aspect in data["aspects"]
        }
        score_rows.append(row)

    if sort_by.startswith("aspect:"):
        maybe_id = sort_by.split(":", 1)[1]
        aspect_id = int(maybe_id) if maybe_id.isdigit() else None
        if aspect_id and any(aspect.id == aspect_id for aspect in data["aspects"]):
            score_rows.sort(key=lambda row: row["aspect_scores"].get(aspect_id, 0.0), reverse=True)
        else:
            sort_by = "avg"
            score_rows.sort(key=lambda row: row["avg_sort_score"], reverse=True)
    else:
        sort_by = "avg"
        score_rows.sort(key=lambda row: row["avg_sort_score"], reverse=True)

    return render_template(
        "_ranking_fragment.html",
        project=project,
        state=data["state"],
        items=data["items"],
        ranking=data["state"]["ranking"],
        scores=data["state"]["aggregate_scores"],
        variances=data["state"]["aggregate_variances"],
        status=data["state"]["status"],
        can_edit=ROLE_LEVEL[role] >= ROLE_LEVEL[ROLE_EDITOR],
        view_mode=params["view_mode"],
        history_mode=params["history_mode"],
        score_mode=params["score_mode"],
        selected_aspect_id=selected_aspect_id,
        sort_by=sort_by,
        aspects=data["aspects"],
        aspect_states=data["state"]["aspect_states"],
        aggregate_scores=data["state"]["aggregate_scores"],
        score_rows=score_rows,
        scoring_mode=scoring_mode,
        graph_aspect_id=graph_aspect_id,
        simulation_mode=params["simulation_mode"],
    )


@bp.get("/projects/<int:project_id>/aspect-graph")
def aspect_graph_fragment(project_id: int):
    project = _project_or_404(project_id)
    _require_viewer(project)
    params = _dashboard_params()
    simulation_mode = params["simulation_mode"] if params["view_mode"] != "details" else "observed"
    data = _load_project_state(project, scoring_mode="graph", simulation_mode=simulation_mode)
    graph_aspect_id = params["graph_aspect_id"]
    if graph_aspect_id is None and data["aspects"]:
        graph_aspect_id = data["aspects"][0].id
    aspect = next((row for row in data["aspects"] if row.id == graph_aspect_id), None)
    if not aspect:
        return render_template("_aspect_graph_fragment.html", graph_data={"nodes": [], "edges": []}, aspect=None)

    item_names = {item.id: item.name for item in data["items"]}
    aspect_state = data["state"]["aspect_states"].get(aspect.id, {})
    graph_data = aspect_state.get("graph", {"nodes": [], "edges": []})
    nodes = [
        {
            "id": node["id"],
            "label": item_names.get(node["id"], f"Item {node['id']}"),
            "score": node["score"],
            "in_cycle": bool(node.get("in_cycle", False)),
        }
        for node in graph_data.get("nodes", [])
    ]
    edges = graph_data.get("edges", [])
    return render_template(
        "_aspect_graph_fragment.html",
        graph_data={"nodes": nodes, "edges": edges},
        aspect=aspect,
        scoring_mode="graph",
    )


@bp.get("/projects/<int:project_id>/aspect-graph-data")
def aspect_graph_data(project_id: int):
    project = _project_or_404(project_id)
    _require_viewer(project)
    params = _dashboard_params()
    simulation_mode = params["simulation_mode"] if params["view_mode"] != "details" else "observed"
    data = _load_project_state(project, scoring_mode="graph", simulation_mode=simulation_mode)
    graph_aspect_id = params["graph_aspect_id"]
    if graph_aspect_id is None and data["aspects"]:
        graph_aspect_id = data["aspects"][0].id
    aspect = next((row for row in data["aspects"] if row.id == graph_aspect_id), None)
    if not aspect:
        return jsonify({"aspect_id": None, "nodes": [], "edges": []})

    item_names = {item.id: item.name for item in data["items"]}
    aspect_state = data["state"]["aspect_states"].get(aspect.id, {})
    graph_data = aspect_state.get("graph", {"nodes": [], "edges": []})
    nodes = [
        {
            "id": node["id"],
            "label": item_names.get(node["id"], f"Item {node['id']}"),
            "score": node["score"],
            "in_cycle": bool(node.get("in_cycle", False)),
        }
        for node in graph_data.get("nodes", [])
    ]
    return jsonify({"aspect_id": aspect.id, "aspect_name": aspect.name, "nodes": nodes, "edges": graph_data.get("edges", [])})


@bp.get("/projects/<int:project_id>/history-fragment")
def history_fragment(project_id: int):
    project = _project_or_404(project_id)
    _require_viewer(project)
    comparisons = (
        Comparison.query.filter_by(project_id=project.id).order_by(Comparison.created_at.desc()).limit(40).all()
    )
    return render_template("_history_fragment.html", comparisons=comparisons)


@bp.get("/projects/<int:project_id>/access-fragment")
def access_fragment(project_id: int):
    project = _project_or_404(project_id)
    _require_editor(project)
    entries = (
        ProjectAccess.query.filter_by(project_id=project.id)
        .join(User, ProjectAccess.user_id == User.id)
        .order_by(User.username.asc())
        .all()
    )
    return render_template("_access_fragment.html", project=project, entries=entries)


@bp.get("/projects/<int:project_id>/export")
def export_project(project_id: int):
    project = _project_or_404(project_id)
    _require_viewer(project)
    mode = request.args.get("mode", "items_only")
    if mode not in {"items_only", "full_project"}:
        abort(400)
    payload = _serialize_project(project, mode)
    response = make_response(json.dumps(payload, indent=2), 200)
    response.headers["Content-Type"] = "application/json"
    response.headers["Content-Disposition"] = f'attachment; filename="project-{project.id}-{mode}.json"'
    return response


@bp.get("/healthz")
def healthz() -> Response:
    return jsonify({"ok": True})
