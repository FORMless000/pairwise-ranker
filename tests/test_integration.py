import io
import json

from app import db
from app.models import Aspect, Comparison, Item, Project, ProjectAccess, User


def _signin(client, username: str, password: str):
    return client.post(
        "/signin",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def _create_project(client, name: str = "P1"):
    response = client.post(
        "/projects",
        data={"name": name, "description": "d", "public_access": "private"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.application.app_context():
        project = Project.query.filter_by(name=name).first()
        return project.id


def test_auto_create_user_on_signin(client):
    response = _signin(client, "alice", "pw")
    assert response.status_code == 200
    with client.application.app_context():
        user = User.query.filter_by(username="alice").first()
        assert user is not None
        assert user.password_hash != "pw"


def test_private_project_hidden_without_access(client):
    _signin(client, "owner", "pw")
    project_id = _create_project(client, "Private Project")
    _ = project_id
    client.post("/signout", follow_redirects=True)
    _signin(client, "other", "pw")
    index = client.get("/")
    assert b"Private Project" not in index.data


def test_viewer_cannot_edit_editor_can(client):
    _signin(client, "viewer1", "pw")
    client.post("/signout", follow_redirects=True)
    _signin(client, "owner", "pw")
    project_id = _create_project(client, "Role Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A", "description": ""}, follow_redirects=True)
    client.post(f"/projects/{project_id}/items", data={"name": "B", "description": ""}, follow_redirects=True)
    client.post(f"/projects/{project_id}/access", data={"username": "viewer1", "role": "viewer", "action": "set"})
    client.post("/signout", follow_redirects=True)

    _signin(client, "viewer1", "pw")
    forbidden = client.post(f"/projects/{project_id}/items", data={"name": "C"})
    assert forbidden.status_code == 403
    page = client.get(f"/projects/{project_id}")
    assert page.status_code == 200

    client.post("/signout", follow_redirects=True)
    _signin(client, "owner", "pw")
    ok = client.post(f"/projects/{project_id}/items", data={"name": "C"}, follow_redirects=False)
    assert ok.status_code == 302


def test_compare_writes_all_aspects(client):
    _signin(client, "owner", "pw")
    project_id = _create_project(client, "Aspect Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    client.post(f"/projects/{project_id}/aspects", data={"name": "Speed"})
    with client.application.app_context():
        items = Item.query.filter_by(project_id=project_id).all()
        aspects = Aspect.query.filter_by(project_id=project_id).all()
        payload = {"item_a_id": items[0].id, "item_b_id": items[1].id}
        for aspect in aspects:
            payload[f"outcome_{aspect.id}"] = "better"
    response = client.post(f"/projects/{project_id}/compare", data=payload)
    assert response.status_code == 200
    with client.application.app_context():
        count = Comparison.query.filter_by(project_id=project_id).count()
        aspect_count = Aspect.query.filter_by(project_id=project_id).count()
        assert count == aspect_count


def test_compare_replaces_old_repetitive_pair(client):
    _signin(client, "dedupe-user", "pw")
    project_id = _create_project(client, "Dedupe Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    with client.application.app_context():
        items = Item.query.filter_by(project_id=project_id).order_by(Item.id.asc()).all()
        aspects = Aspect.query.filter_by(project_id=project_id).all()
        payload = {"item_a_id": items[0].id, "item_b_id": items[1].id}
        for aspect in aspects:
            payload[f"outcome_{aspect.id}"] = "better"
    client.post(f"/projects/{project_id}/compare", data=payload)
    for aspect in aspects:
        payload[f"outcome_{aspect.id}"] = "worse"
    client.post(f"/projects/{project_id}/compare", data=payload)
    with client.application.app_context():
        rows = Comparison.query.filter_by(project_id=project_id).all()
        assert len(rows) == len(aspects)
        assert all(row.outcome == "worse" for row in rows)


def test_compare_cleans_existing_legacy_repetitive_pairs(client):
    _signin(client, "legacy-dedupe-user", "pw")
    project_id = _create_project(client, "Legacy Dedupe Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    client.post(f"/projects/{project_id}/items", data={"name": "C"})
    with client.application.app_context():
        items = Item.query.filter_by(project_id=project_id).order_by(Item.id.asc()).all()
        aspect = Aspect.query.filter_by(project_id=project_id).first()
        item_a_id = items[0].id
        item_b_id = items[1].id
        item_c_id = items[2].id
        aspect_id = aspect.id
        db.session.add(
            Comparison(
                project_id=project_id,
                aspect_id=aspect_id,
                item_a_id=item_a_id,
                item_b_id=item_b_id,
                outcome="better",
                weight=2,
            )
        )
        db.session.add(
            Comparison(
                project_id=project_id,
                aspect_id=aspect_id,
                item_a_id=item_b_id,
                item_b_id=item_a_id,
                outcome="better",
                weight=2,
            )
        )
        db.session.commit()
    client.post(
        f"/projects/{project_id}/compare",
        data={"item_a_id": item_a_id, "item_b_id": item_c_id, f"outcome_{aspect_id}": "better"},
    )
    with client.application.app_context():
        rows = Comparison.query.filter_by(project_id=project_id, aspect_id=aspect_id).all()
        pair_counts = {}
        for row in rows:
            key = tuple(sorted((row.item_a_id, row.item_b_id)))
            pair_counts[key] = pair_counts.get(key, 0) + 1
        assert all(count == 1 for count in pair_counts.values())


def test_export_import_items_only(client):
    _signin(client, "owner", "pw")
    project_id = _create_project(client, "Export Source")
    client.post(f"/projects/{project_id}/items", data={"name": "One"})
    export_resp = client.get(f"/projects/{project_id}/export?mode=items_only")
    assert export_resp.status_code == 200
    payload = json.loads(export_resp.data)
    assert payload["mode"] == "items_only"

    file_data = io.BytesIO(json.dumps(payload).encode("utf-8"))
    import_resp = client.post(
        "/projects/import",
        data={"import_file": (file_data, "items.json")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert import_resp.status_code == 302
    with client.application.app_context():
        imported = Project.query.filter(Project.name.like("%Export Source%")).order_by(Project.id.desc()).first()
        assert imported is not None


def test_export_import_full_project_excludes_access(client):
    _signin(client, "owner", "pw")
    project_id = _create_project(client, "Full Export")
    client.post(f"/projects/{project_id}/items", data={"name": "X"})
    client.post(f"/projects/{project_id}/items", data={"name": "Y"})
    with client.application.app_context():
        items = Item.query.filter_by(project_id=project_id).all()
        aspect = Aspect.query.filter_by(project_id=project_id).first()
    client.post(
        f"/projects/{project_id}/compare",
        data={
            "item_a_id": items[0].id,
            "item_b_id": items[1].id,
            f"outcome_{aspect.id}": "better",
        },
    )
    export_resp = client.get(f"/projects/{project_id}/export?mode=full_project")
    payload = json.loads(export_resp.data)
    assert payload["mode"] == "full_project"
    assert "access_entries" not in payload

    file_data = io.BytesIO(json.dumps(payload).encode("utf-8"))
    import_resp = client.post(
        "/projects/import",
        data={"import_file": (file_data, "full.json")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert import_resp.status_code == 302
    with client.application.app_context():
        imported = Project.query.filter(Project.name == "Full Export").order_by(Project.id.desc()).first()
        owner = User.query.filter_by(username="owner").first()
        owner_entry = ProjectAccess.query.filter_by(project_id=imported.id, user_id=owner.id).first()
        assert owner_entry is not None


def test_hx_signin_returns_shell_fragment(client):
    response = client.post(
        "/signin",
        data={"username": "hx-user", "password": "pw"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b'id="home-shell"' in response.data


def test_hx_project_mutation_returns_project_shell(client):
    _signin(client, "owner-hx", "pw")
    project_id = _create_project(client, "HX Project")
    response = client.post(
        f"/projects/{project_id}/items?view=bt_scores&history=show&score_mode=average",
        data={"name": "New item", "description": "d"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b'id="project-shell"' in response.data


def test_project_page_shows_convergence_table(client):
    _signin(client, "conv-user", "pw")
    project_id = _create_project(client, "Conv Project")
    page = client.get(f"/projects/{project_id}")
    assert page.status_code == 200
    assert b"Convergence by aspect" in page.data


def test_ranking_fragment_supports_scores_and_details_views(client):
    _signin(client, "view-user", "pw")
    project_id = _create_project(client, "View Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A", "description": "alpha"})
    scores_view = client.get(f"/projects/{project_id}/ranking-fragment?view=bt_scores&sort_by=avg")
    assert scores_view.status_code == 200
    assert b"Avg score" in scores_view.data

    details_view = client.get(f"/projects/{project_id}/ranking-fragment?view=details")
    assert details_view.status_code == 200
    assert b"Description" in details_view.data


def test_ranking_fragment_graph_mode(client):
    _signin(client, "graph-user", "pw")
    project_id = _create_project(client, "Graph Rank Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    graph_view = client.get(
        f"/projects/{project_id}/ranking-fragment?view=graph_scores&sort_by=avg"
    )
    assert graph_view.status_code == 200
    assert b"Avg score" in graph_view.data


def test_ranking_fragment_supports_elo_and_wdl_views_with_tooltips(client):
    _signin(client, "extra-mode-user", "pw")
    project_id = _create_project(client, "Extra Mode Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    with client.application.app_context():
        items = Item.query.filter_by(project_id=project_id).order_by(Item.id.asc()).all()
        aspect = Aspect.query.filter_by(project_id=project_id).first()
    client.post(
        f"/projects/{project_id}/compare",
        data={"item_a_id": items[0].id, "item_b_id": items[1].id, f"outcome_{aspect.id}": "better"},
    )
    elo_view = client.get(f"/projects/{project_id}/ranking-fragment?view=elo_scores&sort_by=avg")
    assert elo_view.status_code == 200
    assert b"Avg score" in elo_view.data
    assert (b">1512<" in elo_view.data) or (b">1488<" in elo_view.data)
    wdl_view = client.get(f"/projects/{project_id}/ranking-fragment?view=wdl_scores&sort_by=avg")
    assert wdl_view.status_code == 200
    assert b"W-D-L" in wdl_view.data
    assert b"class=\"hover-card\"" in wdl_view.data
    assert b"Top wins" in wdl_view.data
    assert b"Closest losses" in wdl_view.data
    assert b"Uncertainty:" in wdl_view.data


def test_simulated_ranking_fragment_differs_and_does_not_persist(client):
    _signin(client, "sim-user", "pw")
    project_id = _create_project(client, "Sim Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    client.post(f"/projects/{project_id}/items", data={"name": "C"})
    observed = client.get(f"/projects/{project_id}/ranking-fragment?view=bt_scores&sort_by=avg&simulation=observed")
    simulated = client.get(f"/projects/{project_id}/ranking-fragment?view=bt_scores&sort_by=avg&simulation=simulated")
    assert observed.status_code == 200
    assert simulated.status_code == 200
    assert b"Mode: <code>Simulated</code>" in simulated.data
    assert observed.data != simulated.data
    with client.application.app_context():
        assert Comparison.query.filter_by(project_id=project_id).count() == 0


def test_simulated_toggle_supported_for_all_score_views(client):
    _signin(client, "sim-modes", "pw")
    project_id = _create_project(client, "Sim Modes Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    for view in ("bt_scores", "graph_scores", "elo_scores", "wdl_scores"):
        response = client.get(f"/projects/{project_id}/ranking-fragment?view={view}&sort_by=avg&simulation=simulated")
        assert response.status_code == 200


def test_simulated_cache_returns_stable_output(client):
    _signin(client, "sim-cache", "pw")
    project_id = _create_project(client, "Sim Cache Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    first = client.get(f"/projects/{project_id}/ranking-fragment?view=bt_scores&sort_by=avg&simulation=simulated")
    second = client.get(f"/projects/{project_id}/ranking-fragment?view=bt_scores&sort_by=avg&simulation=simulated")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data == second.data


def test_aspect_graph_endpoints(client):
    _signin(client, "graph-view", "pw")
    project_id = _create_project(client, "Graph View Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    fragment = client.get(f"/projects/{project_id}/aspect-graph")
    assert fragment.status_code == 200
    assert b"aspect-graph-canvas" in fragment.data

    data_resp = client.get(f"/projects/{project_id}/aspect-graph-data")
    assert data_resp.status_code == 200
    payload = data_resp.get_json()
    assert "nodes" in payload
    assert "edges" in payload


def test_project_shows_progress_panel_and_no_pair_after_exhaustion(client):
    _signin(client, "pair-user", "pw")
    project_id = _create_project(client, "Pair State Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    with client.application.app_context():
        items = Item.query.filter_by(project_id=project_id).order_by(Item.id.asc()).all()
        aspect = Aspect.query.filter_by(project_id=project_id).first()
    client.post(
        f"/projects/{project_id}/compare",
        data={"item_a_id": items[1].id, "item_b_id": items[0].id, f"outcome_{aspect.id}": "better"},
    )

    page = client.get(f"/projects/{project_id}?view=bt_scores")
    assert page.status_code == 200
    assert b"Comparison progress" in page.data
    assert b"Exhaustive progress" in page.data
    card = client.get(f"/projects/{project_id}/next-pair?view=bt_scores")
    assert card.status_code == 200
    assert b"No pair available yet." in card.data


def test_next_pair_uses_suggested_pending_selection_when_unrated(client):
    _signin(client, "suggest-user", "pw")
    project_id = _create_project(client, "Suggest Project")
    client.post(f"/projects/{project_id}/items", data={"name": "A"})
    client.post(f"/projects/{project_id}/items", data={"name": "B"})
    card = client.get(f"/projects/{project_id}/next-pair?view=bt_scores")
    assert card.status_code == 200
    assert b"choice-pending" in card.data
    assert b"data-suggested=\"1\"" in card.data
