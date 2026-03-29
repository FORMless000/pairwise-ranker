from app.ranking import (
    OUTCOME_TO_WEIGHT,
    build_weighted_preference_graph,
    choose_next_pair,
    component_sets,
    compute_multi_aspect_state,
    compute_graph_ranking_state,
    convergence_metrics,
    compute_ranking_state,
    fit_scores,
    graph_payload,
    pagerank_scores,
    suggest_outcome_from_state,
    weight_to_target,
)


class DummyItem:
    def __init__(self, item_id: int):
        self.id = item_id
        self.name = f"Item {item_id}"


class DummyComparison:
    def __init__(self, a: int, b: int, weight: int, outcome: str = "better", aspect_id: int = 1):
        self.item_a_id = a
        self.item_b_id = b
        self.weight = weight
        self.outcome = outcome
        self.aspect_id = aspect_id


class DummyAspect:
    def __init__(self, aspect_id: int, name: str):
        self.id = aspect_id
        self.name = name


def test_outcome_mapping():
    assert OUTCOME_TO_WEIGHT == {
        "better": 2,
        "slightly_better": 1,
        "comparable": 0,
        "slightly_worse": -1,
        "worse": -2,
    }
    assert weight_to_target(2) == 1.0
    assert weight_to_target(0) == 0.5
    assert weight_to_target(-2) == 0.0


def test_monotonicity_for_consistent_wins():
    item_ids = [1, 2]
    comparisons = [{"item_a_id": 1, "item_b_id": 2, "weight": 2} for _ in range(8)]
    scores = fit_scores(item_ids, comparisons)
    assert scores[1] > scores[2]


def test_cycle_does_not_crash():
    items = [DummyItem(1), DummyItem(2), DummyItem(3)]
    comparisons = [
        DummyComparison(1, 2, 2, "better"),
        DummyComparison(2, 3, 2, "better"),
        DummyComparison(3, 1, 2, "better"),
    ]
    state = compute_ranking_state(items, comparisons)
    assert set(state["scores"].keys()) == {1, 2, 3}
    assert state["next_pair"] is None


def test_pair_selector_promotes_coverage():
    item_ids = [1, 2, 3, 4]
    comparisons = [{"item_a_id": 1, "item_b_id": 2, "weight": 2}]
    variances = {1: 0.2, 2: 0.2, 3: 1.0, 4: 1.0}
    pair = choose_next_pair(item_ids, comparisons, variances)
    comps = component_sets(item_ids, comparisons)
    assert len(comps) > 1
    assert pair in {(1, 3), (1, 4), (2, 3), (2, 4)}


def test_pair_selector_penalizes_repetitive_pairs():
    item_ids = [1, 2, 3]
    comparisons = [{"item_a_id": 1, "item_b_id": 2, "weight": 2} for _ in range(7)]
    variances = {1: 1.0, 2: 1.0, 3: 1.0}
    pair = choose_next_pair(item_ids, comparisons, variances)
    assert pair in {(1, 3), (2, 3)}


def test_pair_selector_stops_when_exhausted():
    item_ids = [1, 2, 3]
    comparisons = [
        {"item_a_id": 1, "item_b_id": 2, "weight": 2},
        {"item_a_id": 1, "item_b_id": 3, "weight": 2},
        {"item_a_id": 2, "item_b_id": 3, "weight": 2},
    ]
    variances = {1: 0.4, 2: 0.4, 3: 0.4}
    assert choose_next_pair(item_ids, comparisons, variances) is None


def test_multi_aspect_aggregate_modes():
    items = [DummyItem(1), DummyItem(2)]
    aspects = [DummyAspect(1, "A"), DummyAspect(2, "B")]
    comparisons = [
        DummyComparison(1, 2, 2, "better", aspect_id=1),
        DummyComparison(2, 1, 2, "better", aspect_id=2),
    ]
    state_total = compute_multi_aspect_state(items, aspects, comparisons, score_mode="total")
    state_avg = compute_multi_aspect_state(items, aspects, comparisons, score_mode="average")
    assert set(state_total["aggregate_scores"].keys()) == {1, 2}
    assert set(state_avg["aggregate_scores"].keys()) == {1, 2}


def test_convergence_metrics_pass_with_dense_consistent_data():
    item_ids = [1, 2, 3]
    comparisons = []
    for _ in range(4):
        comparisons.append({"item_a_id": 1, "item_b_id": 2, "weight": 2})
        comparisons.append({"item_a_id": 1, "item_b_id": 3, "weight": 2})
        comparisons.append({"item_a_id": 2, "item_b_id": 3, "weight": 2})
    scores = fit_scores(item_ids, comparisons)
    variances = {1: 0.2, 2: 0.2, 3: 0.2}
    convergence = convergence_metrics(item_ids, comparisons, scores, variances)
    assert convergence["connected"] is True
    assert convergence["min_comparisons_per_item"] >= 3
    assert 0.0 <= convergence["score"] <= 1.0


def test_weighted_graph_construction_for_outcomes():
    item_ids = [1, 2]
    comparisons = [
        {"item_a_id": 1, "item_b_id": 2, "weight": 2},
        {"item_a_id": 1, "item_b_id": 2, "weight": -1},
        {"item_a_id": 1, "item_b_id": 2, "weight": 0},
    ]
    edges = build_weighted_preference_graph(item_ids, comparisons)
    assert edges[1][2] > 0
    assert edges[2][1] > 0


def test_pagerank_stable_on_simple_chain():
    item_ids = [1, 2, 3]
    edges = {
        1: {2: 3.0, 3: 1.0},
        2: {3: 3.0},
        3: {},
    }
    scores = pagerank_scores(item_ids, edges)
    assert set(scores.keys()) == {1, 2, 3}
    assert scores[3] >= scores[2] >= scores[1]


def test_graph_ranking_state_shape():
    items = [DummyItem(1), DummyItem(2), DummyItem(3)]
    comparisons = [
        DummyComparison(1, 2, 2, "better", aspect_id=1),
        DummyComparison(2, 3, 1, "slightly_better", aspect_id=1),
    ]
    state = compute_graph_ranking_state(items, comparisons)
    assert "graph" in state
    assert "nodes" in state["graph"]
    assert "edges" in state["graph"]
    assert state["scores"][1] > state["scores"][2] > state["scores"][3]


def test_graph_payload_reduces_trivial_transitive_edge():
    item_ids = [1, 2, 3]
    edges = {
        1: {2: 2.0, 3: 1.0},
        2: {3: 2.0},
        3: {},
    }
    payload = graph_payload(item_ids, edges, {1: 1.0, 2: 0.0, 3: -1.0})
    pairs = {(row["source"], row["target"]) for row in payload["edges"]}
    assert (1, 2) in pairs
    assert (2, 3) in pairs
    assert (1, 3) not in pairs


def test_graph_payload_keeps_edge_when_reverse_evidence_exists():
    item_ids = [1, 2, 3]
    edges = {
        1: {2: 2.0, 3: 1.0},
        2: {3: 2.0},
        3: {1: 1.0},
    }
    payload = graph_payload(item_ids, edges, {1: 0.6, 2: 0.0, 3: -0.6})
    pairs = {(row["source"], row["target"]) for row in payload["edges"]}
    assert (1, 3) in pairs
    assert any(row.get("in_cycle") for row in payload["edges"])


def test_graph_payload_avoids_isolating_nodes_after_reduction():
    item_ids = [1, 2, 3, 4]
    edges = {
        1: {2: 3.0, 3: 3.0, 4: 3.0},
        2: {3: 2.0, 4: 2.0},
        3: {4: 1.0},
        4: {},
    }
    payload = graph_payload(item_ids, edges, {1: 1.0, 2: 0.3, 3: -0.1, 4: -1.2})
    incident = {item_id: 0 for item_id in item_ids}
    for row in payload["edges"]:
        incident[row["source"]] += 1
        incident[row["target"]] += 1
    assert all(incident[item_id] > 0 for item_id in item_ids)


def test_simulation_adds_missing_pairs_per_aspect():
    items = [DummyItem(1), DummyItem(2), DummyItem(3)]
    aspects = [DummyAspect(1, "Overall")]
    comparisons = [DummyComparison(1, 2, 2, "better", aspect_id=1)]
    observed = compute_multi_aspect_state(items, aspects, comparisons, simulation_mode="observed")
    simulated = compute_multi_aspect_state(items, aspects, comparisons, simulation_mode="simulated")
    assert observed["simulation"]["synthetic_total"] == 0
    assert simulated["simulation"]["synthetic_total"] == 2
    assert simulated["simulation"]["synthetic_by_aspect"][1] == 2


def test_simulation_adds_zero_when_exhaustive():
    items = [DummyItem(1), DummyItem(2), DummyItem(3)]
    aspects = [DummyAspect(1, "Overall")]
    comparisons = [
        DummyComparison(1, 2, 2, "better", aspect_id=1),
        DummyComparison(1, 3, 2, "better", aspect_id=1),
        DummyComparison(2, 3, 2, "better", aspect_id=1),
    ]
    simulated = compute_multi_aspect_state(items, aspects, comparisons, simulation_mode="simulated")
    assert simulated["simulation"]["synthetic_total"] == 0


def test_suggestion_logic_is_symmetric():
    state = {
        "scores": {1: 0.8, 2: -0.2},
        "variances": {1: 0.2, 2: 0.2},
    }
    forward = suggest_outcome_from_state(1, 2, state)
    backward = suggest_outcome_from_state(2, 1, state)
    assert forward in {"better", "slightly_better"}
    assert backward in {"worse", "slightly_worse"}
