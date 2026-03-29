from __future__ import annotations

import itertools
import math
from collections import defaultdict


OUTCOME_TO_WEIGHT = {
    "better": 2,
    "slightly_better": 1,
    "comparable": 0,
    "slightly_worse": -1,
    "worse": -2,
}


def _comparison_to_dict(comparison) -> dict:
    if isinstance(comparison, dict):
        return {
            "item_a_id": int(comparison["item_a_id"]),
            "item_b_id": int(comparison["item_b_id"]),
            "weight": int(comparison["weight"]),
            "outcome": comparison.get("outcome", "comparable"),
        }
    return {
        "item_a_id": comparison.item_a_id,
        "item_b_id": comparison.item_b_id,
        "weight": comparison.weight,
        "outcome": comparison.outcome,
    }


def sigmoid(value: float) -> float:
    if value >= 0:
        exp_term = math.exp(-value)
        return 1.0 / (1.0 + exp_term)
    exp_term = math.exp(value)
    return exp_term / (1.0 + exp_term)


def weight_to_target(weight: int) -> float:
    return (weight + 2) / 4.0


def weight_strength(weight: int) -> float:
    return 1.0 + abs(weight)


def fit_scores(
    item_ids: list[int],
    comparisons: list[dict],
    steps: int = 250,
    learning_rate: float = 0.08,
    regularization: float = 0.02,
) -> dict[int, float]:
    scores = {item_id: 0.0 for item_id in item_ids}
    if len(item_ids) < 2 or not comparisons:
        return scores

    for _ in range(steps):
        gradients = {item_id: 2.0 * regularization * score for item_id, score in scores.items()}

        for comp in comparisons:
            item_a = comp["item_a_id"]
            item_b = comp["item_b_id"]
            target = weight_to_target(comp["weight"])
            strength = weight_strength(comp["weight"])
            delta = scores[item_a] - scores[item_b]
            prob = sigmoid(delta)
            grad_delta = strength * (prob - target)
            gradients[item_a] += grad_delta
            gradients[item_b] -= grad_delta

        for item_id in item_ids:
            scores[item_id] -= learning_rate * gradients[item_id]

        mean_score = sum(scores.values()) / len(scores)
        for item_id in item_ids:
            scores[item_id] -= mean_score

    return scores


def estimate_variances(
    item_ids: list[int], scores: dict[int, float], comparisons: list[dict], regularization: float = 0.02
) -> dict[int, float]:
    hessian_diag = {item_id: 2.0 * regularization for item_id in item_ids}
    for comp in comparisons:
        item_a = comp["item_a_id"]
        item_b = comp["item_b_id"]
        strength = weight_strength(comp["weight"])
        delta = scores[item_a] - scores[item_b]
        prob = sigmoid(delta)
        curvature = strength * prob * (1.0 - prob)
        hessian_diag[item_a] += curvature
        hessian_diag[item_b] += curvature

    return {item_id: 1.0 / max(hessian_diag[item_id], 1e-6) for item_id in item_ids}


def component_sets(item_ids: list[int], comparisons: list[dict]) -> list[set[int]]:
    adjacency: dict[int, set[int]] = {item_id: set() for item_id in item_ids}
    for comp in comparisons:
        adjacency[comp["item_a_id"]].add(comp["item_b_id"])
        adjacency[comp["item_b_id"]].add(comp["item_a_id"])

    seen: set[int] = set()
    components = []
    for node in item_ids:
        if node in seen:
            continue
        stack = [node]
        component = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.add(current)
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    stack.append(neighbor)
        components.append(component)
    return components


def choose_next_pair(
    item_ids: list[int],
    comparisons: list[dict],
    variances: dict[int, float],
    scores: dict[int, float] | None = None,
) -> tuple[int, int] | None:
    if len(item_ids) < 2:
        return None

    pair_counts: defaultdict[tuple[int, int], int] = defaultdict(int)
    for comp in comparisons:
        key = tuple(sorted((comp["item_a_id"], comp["item_b_id"])))
        pair_counts[key] += 1

    total_pairs = (len(item_ids) * (len(item_ids) - 1)) // 2
    if len(pair_counts) >= total_pairs:
        return None

    components = component_sets(item_ids, comparisons)
    if len(components) > 1:
        components.sort(key=len, reverse=True)
        left = max(components[0], key=lambda item_id: variances.get(item_id, 1.0))
        right_component = max(components[1:], key=len)
        right = max(right_component, key=lambda item_id: variances.get(item_id, 1.0))
        return (left, right)

    best_pair = None
    best_score = -1.0
    for item_a, item_b in itertools.combinations(item_ids, 2):
        key = tuple(sorted((item_a, item_b)))
        direct_count = pair_counts.get(key, 0)
        if direct_count > 0:
            continue
        uncertainty = variances.get(item_a, 1.0) + variances.get(item_b, 1.0)
        if scores is None:
            tightness = 1.0
        else:
            probability_a_better = sigmoid(scores.get(item_a, 0.0) - scores.get(item_b, 0.0))
            tightness = 1.0 - abs(probability_a_better - 0.5) * 2.0
        pair_score = (0.7 * tightness) + (0.3 * uncertainty)
        if pair_score > best_score:
            best_score = pair_score
            best_pair = (item_a, item_b)
    return best_pair


def pair_progress_metrics(item_ids: list[int], comparisons: list[dict]) -> dict:
    total_pairs = (len(item_ids) * (len(item_ids) - 1)) // 2
    seen_pairs: set[tuple[int, int]] = set()
    for comp in comparisons:
        seen_pairs.add(tuple(sorted((comp["item_a_id"], comp["item_b_id"]))))
    compared_pairs = len(seen_pairs)
    remaining_pairs = max(total_pairs - compared_pairs, 0)
    progress = (compared_pairs / total_pairs) if total_pairs else 0.0
    return {
        "total_pairs": total_pairs,
        "compared_pairs": compared_pairs,
        "remaining_pairs": remaining_pairs,
        "progress": progress,
    }


def derive_status(item_ids: list[int], comparisons: list[dict], variances: dict[int, float]) -> str:
    if len(item_ids) < 2 or not comparisons:
        return "unstarted"

    components = component_sets(item_ids, comparisons)
    if len(components) > 1:
        return "in_progress"

    incident_counts = {item_id: 0 for item_id in item_ids}
    for comp in comparisons:
        incident_counts[comp["item_a_id"]] += 1
        incident_counts[comp["item_b_id"]] += 1

    avg_uncertainty = sum(variances.values()) / max(len(variances), 1)
    min_incident = min(incident_counts.values()) if incident_counts else 0

    if min_incident >= 2 and avg_uncertainty < 0.35:
        return "stable"
    if min_incident >= 1 and avg_uncertainty < 0.8:
        return "provisionally_ranked"
    return "in_progress"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def convergence_metrics(
    item_ids: list[int], comparisons: list[dict], scores: dict[int, float], variances: dict[int, float]
) -> dict:
    if len(item_ids) < 2:
        return {
            "score": 0.0,
            "pass": False,
            "avg_uncertainty": 1.0,
            "adjacent_ambiguity": 1.0,
            "volatility_proxy": 1.0,
            "connected": False,
            "min_comparisons_per_item": 0,
            "thresholds": {
                "avg_uncertainty_max": 0.35,
                "adjacent_ambiguity_max": 0.15,
                "volatility_proxy_max": 0.20,
                "min_comparisons_per_item": 3,
            },
        }

    components = component_sets(item_ids, comparisons)
    connected = len(components) == 1
    incident_counts = {item_id: 0 for item_id in item_ids}
    for comp in comparisons:
        incident_counts[comp["item_a_id"]] += 1
        incident_counts[comp["item_b_id"]] += 1
    min_incident = min(incident_counts.values()) if incident_counts else 0
    avg_uncertainty = sum(variances.values()) / max(len(variances), 1)

    ordered = sorted(item_ids, key=lambda item_id: scores.get(item_id, 0.0), reverse=True)
    ambiguous_count = 0
    volatility_sum = 0.0
    adjacent_count = max(len(ordered) - 1, 1)
    for index in range(len(ordered) - 1):
        left = ordered[index]
        right = ordered[index + 1]
        probability_left_better = sigmoid(scores.get(left, 0.0) - scores.get(right, 0.0))
        tie_closeness = 1.0 - abs(probability_left_better - 0.5) * 2.0
        volatility_sum += tie_closeness
        if 0.45 <= probability_left_better <= 0.55:
            ambiguous_count += 1
    adjacent_ambiguity = ambiguous_count / adjacent_count
    volatility_proxy = volatility_sum / adjacent_count

    thresholds = {
        "avg_uncertainty_max": 0.35,
        "adjacent_ambiguity_max": 0.15,
        "volatility_proxy_max": 0.20,
        "min_comparisons_per_item": 3,
    }

    uncertainty_component = _clamp(1.0 - (avg_uncertainty / thresholds["avg_uncertainty_max"]), 0.0, 1.0)
    ambiguity_component = _clamp(
        1.0 - (adjacent_ambiguity / thresholds["adjacent_ambiguity_max"]), 0.0, 1.0
    )
    volatility_component = _clamp(
        1.0 - (volatility_proxy / thresholds["volatility_proxy_max"]), 0.0, 1.0
    )
    coverage_component = _clamp(min_incident / thresholds["min_comparisons_per_item"], 0.0, 1.0)
    if not connected:
        coverage_component = 0.0

    score = (uncertainty_component + ambiguity_component + volatility_component + coverage_component) / 4.0
    converged = (
        connected
        and min_incident >= thresholds["min_comparisons_per_item"]
        and avg_uncertainty <= thresholds["avg_uncertainty_max"]
        and adjacent_ambiguity <= thresholds["adjacent_ambiguity_max"]
        and volatility_proxy <= thresholds["volatility_proxy_max"]
    )

    return {
        "score": score,
        "pass": converged,
        "avg_uncertainty": avg_uncertainty,
        "adjacent_ambiguity": adjacent_ambiguity,
        "volatility_proxy": volatility_proxy,
        "connected": connected,
        "min_comparisons_per_item": min_incident,
        "thresholds": thresholds,
    }


def compute_ranking_state(items: list, comparisons: list) -> dict:
    item_ids = [item.id for item in items]
    comp_dicts = [_comparison_to_dict(comp) for comp in comparisons]
    scores = fit_scores(item_ids, comp_dicts)
    variances = estimate_variances(item_ids, scores, comp_dicts)
    next_pair = choose_next_pair(item_ids, comp_dicts, variances, scores=scores)
    status = derive_status(item_ids, comp_dicts, variances)
    convergence = convergence_metrics(item_ids, comp_dicts, scores, variances)
    pair_progress = pair_progress_metrics(item_ids, comp_dicts)
    if convergence["pass"]:
        status = "stable"
    elif status == "stable":
        status = "provisionally_ranked"

    ranking = sorted(items, key=lambda item: scores.get(item.id, 0.0), reverse=True)
    return {
        "scores": scores,
        "variances": variances,
        "next_pair": next_pair,
        "status": status,
        "ranking": ranking,
        "convergence": convergence,
        "pair_progress": pair_progress,
        "graph": {"nodes": [], "edges": []},
    }


def _status_rank(status: str) -> int:
    order = {"unstarted": 0, "in_progress": 1, "provisionally_ranked": 2, "stable": 3}
    return order.get(status, 0)


def build_weighted_preference_graph(item_ids: list[int], comparisons: list[dict]) -> dict:
    edges: dict[int, dict[int, float]] = {item_id: {} for item_id in item_ids}
    for comp in comparisons:
        item_a = comp["item_a_id"]
        item_b = comp["item_b_id"]
        weight = int(comp["weight"])
        magnitude = max(abs(weight), 1)
        if weight > 0:
            edges[item_a][item_b] = edges[item_a].get(item_b, 0.0) + float(magnitude)
        elif weight < 0:
            edges[item_b][item_a] = edges[item_b].get(item_a, 0.0) + float(magnitude)
        else:
            edges[item_a][item_b] = edges[item_a].get(item_b, 0.0) + 0.5
            edges[item_b][item_a] = edges[item_b].get(item_a, 0.0) + 0.5
    return edges


def pagerank_scores(
    item_ids: list[int],
    edges: dict[int, dict[int, float]],
    damping: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-8,
) -> dict[int, float]:
    count = len(item_ids)
    if count == 0:
        return {}
    scores = {item_id: 1.0 / count for item_id in item_ids}
    out_weight = {item_id: sum(edges.get(item_id, {}).values()) for item_id in item_ids}
    base = (1.0 - damping) / count

    for _ in range(max_iter):
        next_scores = {item_id: base for item_id in item_ids}
        dangling_mass = sum(scores[item_id] for item_id in item_ids if out_weight[item_id] <= 1e-12)
        for target in item_ids:
            next_scores[target] += damping * dangling_mass / count
        for source in item_ids:
            total = out_weight[source]
            if total <= 1e-12:
                continue
            for target, weight in edges.get(source, {}).items():
                next_scores[target] += damping * scores[source] * (weight / total)

        delta = sum(abs(next_scores[item_id] - scores[item_id]) for item_id in item_ids)
        scores = next_scores
        if delta < tol:
            break
    return scores


def center_scores(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    mean_value = sum(scores.values()) / len(scores)
    centered = {item_id: value - mean_value for item_id, value in scores.items()}
    std = math.sqrt(sum(value * value for value in centered.values()) / max(len(centered), 1))
    if std <= 1e-9:
        return centered
    return {item_id: value / std for item_id, value in centered.items()}


def graph_payload(item_ids: list[int], edges: dict[int, dict[int, float]], scores: dict[int, float]) -> dict:
    def has_path(adjacency: dict[int, set[int]], source: int, target: int) -> bool:
        stack = [source]
        seen = {source}
        while stack:
            node = stack.pop()
            for neighbor in adjacency.get(node, set()):
                if neighbor == target:
                    return True
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        return False

    original_adjacency: dict[int, set[int]] = {item_id: set() for item_id in item_ids}
    original_edges: list[tuple[int, int, float]] = []
    for source in item_ids:
        for target, weight in edges.get(source, {}).items():
            if weight <= 0:
                continue
            original_adjacency[source].add(target)
            original_edges.append((source, target, float(weight)))

    original_edges.sort(key=lambda row: row[2], reverse=True)
    simplified_adjacency: dict[int, set[int]] = {item_id: set() for item_id in item_ids}
    simplified_weights: dict[tuple[int, int], float] = {}
    for source, target, weight in original_edges:
        reverse_exists = edges.get(target, {}).get(source, 0.0) > 0.0
        redundant = has_path(simplified_adjacency, source, target)
        if redundant and not reverse_exists:
            continue
        simplified_adjacency[source].add(target)
        simplified_weights[(source, target)] = weight

    # Keep graph readable but connected: if reduction isolated a node, reattach its strongest edge.
    for node in item_ids:
        had_any = bool(original_adjacency.get(node)) or any(node in dests for dests in original_adjacency.values())
        if not had_any:
            continue
        has_any = bool(simplified_adjacency.get(node)) or any(node in dests for dests in simplified_adjacency.values())
        if has_any:
            continue
        candidate = None
        candidate_weight = -1.0
        for src, tgt, weight in original_edges:
            if src == node or tgt == node:
                if weight > candidate_weight:
                    candidate = (src, tgt)
                    candidate_weight = weight
        if candidate:
            src, tgt = candidate
            simplified_adjacency[src].add(tgt)
            simplified_weights[(src, tgt)] = candidate_weight

    index_counter = 0
    indices: dict[int, int] = {}
    lowlink: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    sccs: list[set[int]] = []

    def strong_connect(node: int) -> None:
        nonlocal index_counter
        indices[node] = index_counter
        lowlink[node] = index_counter
        index_counter += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in simplified_adjacency.get(node, set()):
            if neighbor not in indices:
                strong_connect(neighbor)
                lowlink[node] = min(lowlink[node], lowlink[neighbor])
            elif neighbor in on_stack:
                lowlink[node] = min(lowlink[node], indices[neighbor])
        if lowlink[node] == indices[node]:
            component = set()
            while stack:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            sccs.append(component)

    for item_id in item_ids:
        if item_id not in indices:
            strong_connect(item_id)

    cycle_nodes: set[int] = set()
    for component in sccs:
        if len(component) > 1:
            cycle_nodes.update(component)
            continue
        single = next(iter(component))
        if single in simplified_adjacency.get(single, set()):
            cycle_nodes.add(single)

    edge_rows = [
        {"source": source, "target": target, "weight": float(weight)}
        for (source, target), weight in simplified_weights.items()
    ]
    for edge in edge_rows:
        edge["in_cycle"] = edge["source"] in cycle_nodes and edge["target"] in cycle_nodes

    node_rows = [
        {"id": item_id, "score": float(scores.get(item_id, 0.0)), "in_cycle": item_id in cycle_nodes}
        for item_id in item_ids
    ]
    return {"nodes": node_rows, "edges": edge_rows}


def compute_graph_ranking_state(items: list, comparisons: list) -> dict:
    item_ids = [item.id for item in items]
    comp_dicts = [_comparison_to_dict(comp) for comp in comparisons]
    edges = build_weighted_preference_graph(item_ids, comp_dicts)
    raw_scores = pagerank_scores(item_ids, edges)
    centered_scores = center_scores(raw_scores)
    scores = {item_id: -value for item_id, value in centered_scores.items()}
    variances = estimate_variances(item_ids, scores, comp_dicts)
    next_pair = choose_next_pair(item_ids, comp_dicts, variances, scores=scores)
    status = derive_status(item_ids, comp_dicts, variances)
    convergence = convergence_metrics(item_ids, comp_dicts, scores, variances)
    pair_progress = pair_progress_metrics(item_ids, comp_dicts)
    if convergence["pass"]:
        status = "stable"
    elif status == "stable":
        status = "provisionally_ranked"
    ranking = sorted(items, key=lambda item: scores.get(item.id, 0.0), reverse=True)
    return {
        "scores": scores,
        "variances": variances,
        "next_pair": next_pair,
        "status": status,
        "ranking": ranking,
        "convergence": convergence,
        "pair_progress": pair_progress,
        "graph": graph_payload(item_ids, edges, scores),
    }


def compute_elo_ranking_state(items: list, comparisons: list) -> dict:
    item_ids = [item.id for item in items]
    comp_dicts = [_comparison_to_dict(comp) for comp in comparisons]
    ratings = {item_id: 1500.0 for item_id in item_ids}
    match_counts = {item_id: 0 for item_id in item_ids}
    for comp in comp_dicts:
        item_a = comp["item_a_id"]
        item_b = comp["item_b_id"]
        weight = int(comp["weight"])
        if weight == 0:
            actual_a = 0.5
            k = 12.0
        elif weight > 0:
            actual_a = 1.0
            k = 24.0 if abs(weight) >= 2 else 12.0
        else:
            actual_a = 0.0
            k = 24.0 if abs(weight) >= 2 else 12.0
        expected_a = 1.0 / (1.0 + (10.0 ** ((ratings[item_b] - ratings[item_a]) / 400.0)))
        delta = k * (actual_a - expected_a)
        ratings[item_a] += delta
        ratings[item_b] -= delta
        match_counts[item_a] += 1
        match_counts[item_b] += 1

    mean_rating = (sum(ratings.values()) / len(ratings)) if ratings else 1500.0
    scores = {item_id: (ratings[item_id] - mean_rating) / 100.0 for item_id in item_ids}
    variances = {item_id: 1.0 / max(match_counts[item_id] + 1, 1) for item_id in item_ids}
    next_pair = choose_next_pair(item_ids, comp_dicts, variances, scores=scores)
    status = derive_status(item_ids, comp_dicts, variances)
    convergence = convergence_metrics(item_ids, comp_dicts, scores, variances)
    pair_progress = pair_progress_metrics(item_ids, comp_dicts)
    if convergence["pass"]:
        status = "stable"
    elif status == "stable":
        status = "provisionally_ranked"
    ranking = sorted(items, key=lambda item: scores.get(item.id, 0.0), reverse=True)
    return {
        "scores": scores,
        "variances": variances,
        "next_pair": next_pair,
        "status": status,
        "ranking": ranking,
        "convergence": convergence,
        "pair_progress": pair_progress,
        "ratings": ratings,
        "graph": {"nodes": [], "edges": []},
    }


def compute_wdl_ranking_state(items: list, comparisons: list) -> dict:
    item_ids = [item.id for item in items]
    comp_dicts = [_comparison_to_dict(comp) for comp in comparisons]
    wins = {item_id: 0 for item_id in item_ids}
    draws = {item_id: 0 for item_id in item_ids}
    losses = {item_id: 0 for item_id in item_ids}
    for comp in comp_dicts:
        item_a = comp["item_a_id"]
        item_b = comp["item_b_id"]
        weight = int(comp["weight"])
        if weight > 0:
            wins[item_a] += 1
            losses[item_b] += 1
        elif weight < 0:
            wins[item_b] += 1
            losses[item_a] += 1
        else:
            draws[item_a] += 1
            draws[item_b] += 1
    points = {item_id: wins[item_id] + 0.5 * draws[item_id] for item_id in item_ids}
    mean_points = (sum(points.values()) / len(points)) if points else 0.0
    scores = {item_id: points[item_id] - mean_points for item_id in item_ids}
    variances = {item_id: 1.0 / max(wins[item_id] + draws[item_id] + losses[item_id] + 1, 1) for item_id in item_ids}
    next_pair = choose_next_pair(item_ids, comp_dicts, variances, scores=scores)
    status = derive_status(item_ids, comp_dicts, variances)
    convergence = convergence_metrics(item_ids, comp_dicts, scores, variances)
    pair_progress = pair_progress_metrics(item_ids, comp_dicts)
    if convergence["pass"]:
        status = "stable"
    elif status == "stable":
        status = "provisionally_ranked"
    ranking = sorted(
        items,
        key=lambda item: (points.get(item.id, 0.0), wins.get(item.id, 0), -losses.get(item.id, 0)),
        reverse=True,
    )
    return {
        "scores": scores,
        "variances": variances,
        "next_pair": next_pair,
        "status": status,
        "ranking": ranking,
        "convergence": convergence,
        "pair_progress": pair_progress,
        "graph": {"nodes": [], "edges": []},
        "wdl": {"wins": wins, "draws": draws, "losses": losses},
    }


def suggest_outcome_from_state(item_a_id: int, item_b_id: int, aspect_state: dict) -> str:
    scores = aspect_state.get("scores", {})
    variances = aspect_state.get("variances", {})
    delta = float(scores.get(item_a_id, 0.0) - scores.get(item_b_id, 0.0))
    uncertainty = math.sqrt(max(float(variances.get(item_a_id, 1.0) + variances.get(item_b_id, 1.0)), 1e-6))
    normalized = delta / max(0.35 + uncertainty, 1e-6)
    if normalized >= 1.1:
        return "better"
    if normalized >= 0.35:
        return "slightly_better"
    if normalized <= -1.1:
        return "worse"
    if normalized <= -0.35:
        return "slightly_worse"
    return "comparable"


def _compute_all_mode_states(items: list, aspects: list, by_aspect: dict[int, list]) -> tuple[dict, dict, dict, dict]:
    bt_aspect_states: dict[int, dict] = {}
    graph_aspect_states: dict[int, dict] = {}
    elo_aspect_states: dict[int, dict] = {}
    wdl_aspect_states: dict[int, dict] = {}
    for aspect in aspects:
        per_aspect_comparisons = by_aspect.get(aspect.id, [])
        bt_aspect_states[aspect.id] = compute_ranking_state(items, per_aspect_comparisons)
        graph_aspect_states[aspect.id] = compute_graph_ranking_state(items, per_aspect_comparisons)
        elo_aspect_states[aspect.id] = compute_elo_ranking_state(items, per_aspect_comparisons)
        wdl_aspect_states[aspect.id] = compute_wdl_ranking_state(items, per_aspect_comparisons)
    return bt_aspect_states, graph_aspect_states, elo_aspect_states, wdl_aspect_states


def _aspect_state_for_mode(scoring_mode: str, bt_states: dict, graph_states: dict, elo_states: dict, wdl_states: dict) -> dict:
    if scoring_mode == "graph":
        return graph_states
    if scoring_mode == "elo":
        return elo_states
    if scoring_mode == "wdl":
        return wdl_states
    return bt_states


def compute_multi_aspect_state(
    items: list,
    aspects: list,
    comparisons: list,
    score_mode: str = "average",
    selected_aspect_id: int | None = None,
    scoring_mode: str = "bt",
    simulation_mode: str = "observed",
) -> dict:
    aspect_map = {aspect.id: aspect for aspect in aspects}
    by_aspect: dict[int, list] = {aspect.id: [] for aspect in aspects}
    for comp in comparisons:
        if comp.aspect_id in by_aspect:
            by_aspect[comp.aspect_id].append(comp)

    bt_aspect_states, graph_aspect_states, elo_aspect_states, wdl_aspect_states = _compute_all_mode_states(
        items, aspects, by_aspect
    )
    source_aspect_states = _aspect_state_for_mode(
        scoring_mode, bt_aspect_states, graph_aspect_states, elo_aspect_states, wdl_aspect_states
    )

    synthetic_by_aspect: dict[int, list[dict]] = {aspect.id: [] for aspect in aspects}
    coverage_before: dict[int, dict] = {}
    coverage_after: dict[int, dict] = {}
    if simulation_mode == "simulated":
        item_ids = [item.id for item in items]
        total_pairs = (len(item_ids) * (len(item_ids) - 1)) // 2
        for aspect in aspects:
            observed = by_aspect.get(aspect.id, [])
            existing_pairs = {
                tuple(sorted((comp.item_a_id, comp.item_b_id)))
                for comp in observed
            }
            coverage_before[aspect.id] = {
                "compared": len(existing_pairs),
                "total": total_pairs,
            }
            aspect_state = source_aspect_states.get(aspect.id, {})
            for item_a, item_b in itertools.combinations(item_ids, 2):
                if (item_a, item_b) in existing_pairs:
                    continue
                outcome = suggest_outcome_from_state(item_a, item_b, aspect_state)
                synthetic_by_aspect[aspect.id].append(
                    {
                        "item_a_id": item_a,
                        "item_b_id": item_b,
                        "outcome": outcome,
                        "weight": OUTCOME_TO_WEIGHT[outcome],
                    }
                )
            coverage_after[aspect.id] = {
                "compared": len(existing_pairs) + len(synthetic_by_aspect[aspect.id]),
                "total": total_pairs,
            }
        simulated_by_aspect = {
            aspect.id: list(by_aspect.get(aspect.id, [])) + list(synthetic_by_aspect.get(aspect.id, []))
            for aspect in aspects
        }
        bt_aspect_states, graph_aspect_states, elo_aspect_states, wdl_aspect_states = _compute_all_mode_states(
            items, aspects, simulated_by_aspect
        )

    aspect_states = _aspect_state_for_mode(
        scoring_mode, bt_aspect_states, graph_aspect_states, elo_aspect_states, wdl_aspect_states
    )

    if selected_aspect_id and selected_aspect_id in aspect_states:
        selected_state = aspect_states[selected_aspect_id]
        aggregate_scores = selected_state["scores"]
        aggregate_variances = selected_state["variances"]
        ranking = selected_state["ranking"]
        status = selected_state["status"]
    else:
        item_ids = [item.id for item in items]
        aggregate_scores = {item_id: 0.0 for item_id in item_ids}
        aggregate_variances = {item_id: 0.0 for item_id in item_ids}
        for aspect in aspects:
            state = aspect_states[aspect.id]
            for item_id in item_ids:
                aggregate_scores[item_id] += state["scores"].get(item_id, 0.0)
                aggregate_variances[item_id] += state["variances"].get(item_id, 1.0)

        divisor = max(len(aspects), 1) if score_mode == "average" else 1
        for item_id in item_ids:
            aggregate_scores[item_id] /= divisor
            aggregate_variances[item_id] /= max(len(aspects), 1)
        ranking = sorted(items, key=lambda item: aggregate_scores.get(item.id, 0.0), reverse=True)
        status = min(
            (state["status"] for state in bt_aspect_states.values()),
            key=_status_rank,
            default="unstarted",
        )

    next_pair = None
    if aspects:
        source_aspect_id = selected_aspect_id if selected_aspect_id in aspect_states else aspects[0].id
        next_pair = bt_aspect_states[source_aspect_id]["next_pair"]

    per_aspect_progress = {
        aspect_id: state.get("pair_progress", {})
        for aspect_id, state in bt_aspect_states.items()
    }
    total_pair_slots = 0
    compared_pair_slots = 0
    for progress in per_aspect_progress.values():
        total_pair_slots += progress.get("total_pairs", 0)
        compared_pair_slots += progress.get("compared_pairs", 0)
    overall_progress = (compared_pair_slots / total_pair_slots) if total_pair_slots else 0.0

    return {
        "aspect_states": aspect_states,
        "bt_aspect_states": bt_aspect_states,
        "graph_aspect_states": graph_aspect_states,
        "elo_aspect_states": elo_aspect_states,
        "wdl_aspect_states": wdl_aspect_states,
        "aggregate_scores": aggregate_scores,
        "aggregate_variances": aggregate_variances,
        "ranking": ranking,
        "status": status,
        "next_pair": next_pair,
        "selected_aspect_id": selected_aspect_id,
        "aspect_map": aspect_map,
        "convergence": {
            "per_aspect": {
                aspect_id: aspect_state.get("convergence", {})
                for aspect_id, aspect_state in bt_aspect_states.items()
            },
            "all_aspects_stable": all(
                aspect_state.get("convergence", {}).get("pass", False)
                for aspect_state in bt_aspect_states.values()
            )
            if bt_aspect_states
            else False,
            "source": "bt_proxy" if scoring_mode in {"graph", "elo", "wdl"} else "bt",
        },
        "pair_progress": {
            "per_aspect": per_aspect_progress,
            "overall": {
                "total_pair_slots": total_pair_slots,
                "compared_pair_slots": compared_pair_slots,
                "remaining_pair_slots": max(total_pair_slots - compared_pair_slots, 0),
                "progress": overall_progress,
            },
        },
        "scoring_mode": scoring_mode,
        "simulation": {
            "mode": simulation_mode,
            "synthetic_by_aspect": {
                aspect_id: len(rows) for aspect_id, rows in synthetic_by_aspect.items()
            },
            "synthetic_total": sum(len(rows) for rows in synthetic_by_aspect.values()),
            "coverage_before": coverage_before,
            "coverage_after": coverage_after,
        },
    }
