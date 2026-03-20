"""
Fuzzy Policy Engine — Plugs into AuditChain pipeline
======================================================
Replaces hard if/else threshold checks with fuzzy membership functions.
The key insight: €98k and €50k are BOTH in Tier 2, but €98k is "borderline Tier 3".
Fuzzy logic captures this — hard rules don't.

Three modules:
  1. Fuzzy Threshold Classifier  — approval tier with proximity awareness
  2. Fuzzy Supplier Scorer       — replaces weighted sum with fuzzy inference
  3. Fuzzy Confidence Gate        — triggers escalation on genuine uncertainty

Integration:
  - In pipeline.py, replace check_policy() call with fuzzy_check_policy()
  - In tools.py, replace score_suppliers() with fuzzy_score_suppliers()
  - Confidence gate feeds into make_decision()

All outputs include full membership traces for the audit trail.
No scikit-fuzzy dependency — pure numpy for hackathon speed.

References:
  - FAHP-TOPSIS: Nature Scientific Reports 2025 (doi:10.1038/s41598-025-25042-z)
  - Ordered Fuzzy Decision Systems: Entropy 2024 (doi:10.3390/e26100860)
  - Fuzzy Supplier Evaluation: Sayyadi et al. 2018, Lima et al. 2013
"""
from __future__ import annotations
import math
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# MEMBERSHIP FUNCTIONS — the building blocks
# ═══════════════════════════════════════════════════════════════════════════════

def trimf(x: float, a: float, b: float, c: float) -> float:
    """Triangular membership function. Peak at b, zero at a and c."""
    if x <= a or x >= c:
        return 0.0
    if x <= b:
        return (x - a) / (b - a) if b != a else 1.0
    return (c - x) / (c - b) if c != b else 1.0


def trapmf(x: float, a: float, b: float, c: float, d: float) -> float:
    """Trapezoidal membership function. Flat top between b and c."""
    if x <= a or x >= d:
        return 0.0
    if x <= b:
        return (x - a) / (b - a) if b != a else 1.0
    if x <= c:
        return 1.0
    return (d - x) / (d - c) if d != c else 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 1: FUZZY THRESHOLD CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════════════
#
# Instead of: if budget >= 100_000: escalate
# We get:     budget has 0.85 membership in Tier 2 and 0.35 in Tier 3
#             → recommend treating as Tier 3 (cautious) with proximity warning

# EUR thresholds from policies.json (overlap zones = fuzzy boundaries)
TIER_BOUNDS_EUR = {
    "tier1": {"label": "Business Only",              "lo": 0,       "hi": 25_000,    "quotes": 1, "approver": "Business"},
    "tier2": {"label": "Business + Procurement",     "lo": 25_000,  "hi": 100_000,   "quotes": 2, "approver": "Procurement Manager"},
    "tier3": {"label": "Head of Category",           "lo": 100_000, "hi": 500_000,   "quotes": 3, "approver": "Head of Category"},
    "tier4": {"label": "Head of Strategic Sourcing",  "lo": 500_000, "hi": 5_000_000, "quotes": 3, "approver": "Head of Strategic Sourcing"},
    "tier5": {"label": "CPO",                         "lo": 5_000_000, "hi": float("inf"), "quotes": 3, "approver": "CPO"},
}

# Overlap width: 15% of boundary value (how "fuzzy" the boundary is)
OVERLAP_PCT = 0.15


def fuzzy_threshold_classify(budget: float, currency: str = "EUR") -> dict:
    """
    Classify a budget amount into approval tiers with fuzzy membership.

    Returns:
    {
        "primary_tier": "tier2",
        "memberships": {"tier1": 0.0, "tier2": 0.85, "tier3": 0.35, ...},
        "is_borderline": True,
        "borderline_tiers": ["tier2", "tier3"],
        "recommendation": "tier3",  # cautious: pick the higher tier if borderline
        "proximity_warning": "Budget €98,000 is within 15% of Tier 3 boundary (€100,000)",
        "approver": "Head of Category",
        "min_quotes": 3,
    }
    """
    # CHF and USD conversion factors (from policies.json)
    fx = {"EUR": 1.0, "CHF": 0.95, "USD": 0.92}
    budget_eur = budget * fx.get(currency, 1.0)

    memberships = {}
    tiers = list(TIER_BOUNDS_EUR.items())

    for tier_name, bounds in tiers:
        lo, hi = bounds["lo"], bounds["hi"]
        overlap_lo = lo * OVERLAP_PCT if lo > 0 else 0
        overlap_hi = hi * OVERLAP_PCT if hi < float("inf") else 0

        # Trapezoidal: ramp up from (lo - overlap) to lo, flat, ramp down from hi to (hi + overlap)
        a = max(0, lo - overlap_lo)
        b = lo
        c = hi if hi < float("inf") else budget_eur * 2
        d = (hi + overlap_hi) if hi < float("inf") else budget_eur * 2

        memberships[tier_name] = round(trapmf(budget_eur, a, b, c, d), 3)

    # Normalize so at least one tier has membership > 0
    max_mem = max(memberships.values()) or 1.0
    if max_mem == 0:
        memberships["tier1"] = 1.0

    # Primary tier = highest membership
    primary = max(memberships, key=memberships.get)

    # Borderline = any other tier with membership > 0.15
    borderline_tiers = [t for t, m in memberships.items() if m > 0.15 and t != primary]
    is_borderline = len(borderline_tiers) > 0

    # Cautious recommendation: if borderline, pick the HIGHER tier
    if is_borderline:
        all_active = [primary] + borderline_tiers
        tier_order = list(TIER_BOUNDS_EUR.keys())
        recommendation = max(all_active, key=lambda t: tier_order.index(t))
    else:
        recommendation = primary

    rec_bounds = TIER_BOUNDS_EUR[recommendation]

    # Proximity warning
    proximity_warning = None
    if is_borderline:
        # Find the boundary we're close to
        for bt in borderline_tiers:
            bt_bounds = TIER_BOUNDS_EUR[bt]
            boundary = bt_bounds["lo"]
            if boundary > 0:
                pct_away = abs(budget_eur - boundary) / boundary * 100
                proximity_warning = (
                    f"Budget €{budget_eur:,.0f} is within {pct_away:.0f}% of "
                    f"{bt_bounds['label']} boundary (€{boundary:,.0f})"
                )
                break

    return {
        "primary_tier": primary,
        "memberships": memberships,
        "is_borderline": is_borderline,
        "borderline_tiers": borderline_tiers,
        "recommendation": recommendation,
        "proximity_warning": proximity_warning,
        "approver": rec_bounds["approver"],
        "min_quotes": rec_bounds["quotes"],
        "budget_eur": round(budget_eur, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 2: FUZZY SUPPLIER SCORER
# ═══════════════════════════════════════════════════════════════════════════════
#
# Instead of: score = 0.4*price + 0.25*delivery + 0.2*compliance + 0.15*esg
# We get:     fuzzy rules like "IF price IS competitive AND delivery IS fast
#             THEN suitability IS excellent" — readable by auditors

# Linguistic terms for each criterion (triangular MFs on 0-1 scale)
TERMS = {
    "poor":        (0.0, 0.0, 0.35),
    "fair":        (0.2, 0.4, 0.6),
    "good":        (0.45, 0.65, 0.85),
    "excellent":   (0.7, 1.0, 1.0),
}

# Suitability output terms
SUITABILITY = {
    "poor":        (0.0, 0.0, 0.3),
    "acceptable":  (0.15, 0.35, 0.55),
    "good":        (0.4, 0.6, 0.8),
    "excellent":   (0.7, 1.0, 1.0),
}

# Fuzzy rules — each is (conditions_dict, consequent)
# Read as: IF price IS ... AND delivery IS ... THEN suitability IS ...
FUZZY_RULES = [
    # Strong positive
    ({"price": "excellent", "delivery": "excellent"},                    "excellent"),
    ({"price": "excellent", "compliance": "excellent"},                  "excellent"),
    ({"price": "good",      "delivery": "good",    "esg": "excellent"}, "excellent"),

    # Good
    ({"price": "good",      "delivery": "good"},                        "good"),
    ({"price": "excellent", "delivery": "fair"},                         "good"),
    ({"price": "fair",      "delivery": "excellent", "compliance": "excellent"}, "good"),
    ({"price": "good",      "compliance": "good"},                      "good"),

    # Acceptable
    ({"price": "fair",      "delivery": "fair"},                         "acceptable"),
    ({"price": "good",      "delivery": "poor"},                         "acceptable"),
    ({"price": "poor",      "delivery": "excellent"},                    "acceptable"),

    # Poor
    ({"price": "poor",      "delivery": "poor"},                         "poor"),
    ({"price": "poor",      "compliance": "poor"},                       "poor"),
    ({"price": "fair",      "esg": "poor"},                              "acceptable"),  # ESG alone doesn't kill
]


def _evaluate_term(value: float, term_name: str, term_set: dict = TERMS) -> float:
    """Get membership degree for a value in a named fuzzy term."""
    params = term_set.get(term_name)
    if not params:
        return 0.0
    return trimf(value, *params)


def _classify_linguistic(value: float, term_set: dict = TERMS) -> dict:
    """Get all membership degrees for a crisp value."""
    return {name: round(trimf(value, *params), 3) for name, params in term_set.items()}


def _fire_rules(criterion_values: dict[str, float]) -> list[dict]:
    """
    Fire all fuzzy rules and return activation strengths.
    Uses Mamdani min-max inference: rule strength = min of all antecedent memberships.
    """
    fired = []
    for conditions, consequent in FUZZY_RULES:
        # Rule strength = min of all condition memberships (AND = min in fuzzy logic)
        strengths = []
        for criterion, term in conditions.items():
            if criterion in criterion_values:
                strength = _evaluate_term(criterion_values[criterion], term)
                strengths.append(strength)
            else:
                strengths.append(0.0)  # missing criterion = no support

        rule_strength = min(strengths) if strengths else 0.0

        if rule_strength > 0.01:  # only record meaningfully fired rules
            fired.append({
                "conditions": conditions,
                "consequent": consequent,
                "strength": round(rule_strength, 3),
                "rule_text": " AND ".join(f"{k} IS {v}" for k, v in conditions.items())
                             + f" → suitability IS {consequent}",
            })

    return fired


def _defuzzify(fired_rules: list[dict]) -> float:
    """
    Centroid defuzzification of fired rules.
    Aggregates: for each output point, take max of (rule_strength clipped by output MF).
    """
    if not fired_rules:
        return 0.0

    # Sample 100 points on [0, 1]
    n = 100
    points = [i / n for i in range(n + 1)]
    aggregated = [0.0] * (n + 1)

    for rule in fired_rules:
        consequent_params = SUITABILITY[rule["consequent"]]
        for i, x in enumerate(points):
            mf_value = trimf(x, *consequent_params)
            clipped = min(rule["strength"], mf_value)  # Mamdani: clip at rule strength
            aggregated[i] = max(aggregated[i], clipped)  # aggregate: max (union)

    # Centroid
    numerator = sum(x * a for x, a in zip(points, aggregated))
    denominator = sum(aggregated)
    if denominator < 1e-10:
        return 0.0
    return numerator / denominator


def fuzzy_score_supplier(
    price_normalized: float,      # 0-1, higher = cheaper (better)
    delivery_normalized: float,   # 0-1, higher = faster (better)
    compliance_normalized: float, # 0-1, higher = more compliant
    esg_normalized: float,        # 0-1, higher = better ESG
) -> dict:
    """
    Score a single supplier using fuzzy inference.

    Returns:
    {
        "score": 72.5,             # 0-100 defuzzified score
        "linguistic": "good",      # dominant output term
        "memberships": {           # per-criterion memberships (audit trail)
            "price": {"poor": 0.0, "fair": 0.2, "good": 0.8, "excellent": 0.1},
            ...
        },
        "rules_fired": [           # which rules activated and how strongly
            {"rule_text": "price IS good AND delivery IS good → suitability IS good",
             "strength": 0.72},
            ...
        ],
    }
    """
    values = {
        "price": price_normalized,
        "delivery": delivery_normalized,
        "compliance": compliance_normalized,
        "esg": esg_normalized,
    }

    # Get linguistic memberships for each criterion
    memberships = {k: _classify_linguistic(v) for k, v in values.items()}

    # Fire rules
    fired = _fire_rules(values)

    # Defuzzify
    crisp_score = _defuzzify(fired)
    score_100 = round(crisp_score * 100, 1)

    # Dominant linguistic label for output
    output_memberships = _classify_linguistic(crisp_score, SUITABILITY)
    linguistic = max(output_memberships, key=output_memberships.get)

    return {
        "score": score_100,
        "linguistic": linguistic,
        "memberships": memberships,
        "output_memberships": output_memberships,
        "rules_fired": fired,
        "defuzzified_raw": round(crisp_score, 4),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 3: FUZZY CONFIDENCE GATE
# ═══════════════════════════════════════════════════════════════════════════════
#
# Combines multiple uncertainty signals into a single confidence measure.
# When confidence is low, the system escalates — with an explanation of WHY.

def fuzzy_confidence_gate(
    threshold_result: dict,        # from fuzzy_threshold_classify()
    top_supplier_score: float,     # 0-100 from fuzzy scorer
    second_supplier_score: float | None,  # 0-100, None if only 1 candidate
    num_candidates: int,
    has_ambiguities: bool,
    has_missing_fields: bool,
) -> dict:
    """
    Compute fuzzy confidence for the overall decision.

    Inputs are fuzzified into uncertainty signals, then combined.

    Returns:
    {
        "confidence": 0.73,              # 0-1
        "confidence_label": "moderate",  # low / moderate / high
        "should_escalate": False,
        "uncertainty_signals": [
            {"signal": "threshold_proximity", "severity": 0.35, "detail": "..."},
            {"signal": "score_gap", "severity": 0.2, "detail": "..."},
            ...
        ],
    }
    """
    signals = []

    # Signal 1: Threshold proximity — borderline budget = uncertainty
    if threshold_result.get("is_borderline"):
        severity = max(
            threshold_result["memberships"].get(t, 0)
            for t in threshold_result.get("borderline_tiers", [])
        )
        signals.append({
            "signal": "threshold_proximity",
            "severity": round(severity, 3),
            "detail": threshold_result.get("proximity_warning", "Budget near approval boundary"),
        })

    # Signal 2: Score gap — if top 2 suppliers are close, ranking is fragile
    if second_supplier_score is not None:
        gap = top_supplier_score - second_supplier_score
        # Calibrated: 0-2pt gap is high severity, 5pt is moderate, 10+ is negligible
        # Uses sqrt curve so severity drops faster for meaningful gaps
        gap_severity = max(0, 1.0 - (gap / 10.0) ** 0.7)  # 0 at ~10 gap, ~0.65 at 3 gap
        if gap_severity > 0.15:
            signals.append({
                "signal": "narrow_score_gap",
                "severity": round(gap_severity, 3),
                "detail": f"Top two suppliers scored {top_supplier_score:.1f} vs {second_supplier_score:.1f} "
                          f"(gap: {gap:.1f} points) — ranking may be sensitive to weight changes",
            })

    # Signal 3: Few candidates — less choice = less confidence
    if num_candidates <= 1:
        signals.append({
            "signal": "limited_candidates",
            "severity": 0.6,
            "detail": f"Only {num_candidates} eligible supplier(s) — insufficient market coverage",
        })
    elif num_candidates == 2:
        signals.append({
            "signal": "limited_candidates",
            "severity": 0.3,
            "detail": f"Only {num_candidates} eligible suppliers — limited comparison basis",
        })

    # Signal 4: Ambiguities in request
    if has_ambiguities:
        signals.append({
            "signal": "request_ambiguity",
            "severity": 0.4,
            "detail": "Request contains ambiguous or conflicting information",
        })

    # Signal 5: Missing fields
    if has_missing_fields:
        signals.append({
            "signal": "incomplete_request",
            "severity": 0.7,
            "detail": "Required fields are missing from the request",
        })

    # Combine: confidence = 1 - weighted_severity, accounting for signal count
    if not signals:
        confidence = 0.95
    else:
        max_severity = max(s["severity"] for s in signals)
        avg_severity = sum(s["severity"] for s in signals) / len(signals)
        # Single signal is damped (one concern alone shouldn't tank confidence);
        # multiple compounding signals are more serious
        signal_count_factor = min(1.0, len(signals) / 3)  # 1→0.33, 2→0.67, 3+→1.0
        raw_severity = 0.6 * max_severity + 0.4 * avg_severity
        combined_severity = raw_severity * (0.5 + 0.5 * signal_count_factor)
        confidence = max(0.05, 1.0 - combined_severity)

    confidence = round(confidence, 2)

    # Linguistic label
    if confidence >= 0.75:
        label = "high"
    elif confidence >= 0.45:
        label = "moderate"
    else:
        label = "low"

    return {
        "confidence": confidence,
        "confidence_label": label,
        "should_escalate": confidence < 0.30,
        "escalation_reason": (
            "Fuzzy confidence gate: " +
            "; ".join(s["detail"] for s in signals if s["severity"] > 0.3)
        ) if confidence < 0.30 else None,
        "uncertainty_signals": signals,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION HELPERS — drop-in replacements for pipeline.py
# ═══════════════════════════════════════════════════════════════════════════════

def fuzzy_check_policy(structured_request: dict, rules: list, existing_check_fn=None) -> dict:
    """
    Wrapper: runs the existing check_policy() PLUS fuzzy threshold overlay.
    The hard rules still fire (they must, for compliance). Fuzzy adds:
      - proximity warnings for borderline budgets
      - cautious tier recommendation
      - full membership trace in the audit trail

    Usage in pipeline.py:
        # Replace:  policy_results = check_policy(structured, rules)
        # With:     policy_results = fuzzy_check_policy(structured, rules, check_policy)
    """
    # Run existing deterministic checks (keep all hard rules!)
    if existing_check_fn:
        results = existing_check_fn(structured_request, rules)
    else:
        results = {"violations": [], "warnings": [], "escalations": [], "all_clear": True}

    # Overlay fuzzy threshold classification
    budget = structured_request.get("budget_eur") or structured_request.get("_budget") or 0
    currency = structured_request.get("currency") or "EUR"

    if budget > 0:
        threshold = fuzzy_threshold_classify(budget, currency)
        results["fuzzy_threshold"] = threshold

        # Add proximity warning if borderline
        if threshold["is_borderline"] and threshold.get("proximity_warning"):
            results["warnings"].append({
                "rule_id": "FUZZY-THRESHOLD",
                "rule_name": "Fuzzy Threshold Proximity",
                "description": "Budget is near an approval tier boundary",
                "triggered": True,
                "action": "warn",
                "detail": threshold["proximity_warning"],
                "escalate_to": threshold["approver"],
                "fuzzy_memberships": threshold["memberships"],
            })

        # Fuzzy tier classification is INFORMATIONAL — it enriches the audit trail
        # but does NOT add escalations. Hard rules (AT-002/003/004/005) already
        # handle genuine spending authority violations. The fuzzy tier, approver,
        # and proximity warnings are visible in the decision detail for auditors.

    return results


def _tier_rank(tier: str) -> int:
    order = {"none": 0, "tier1": 1, "tier2": 2, "tier3": 3, "tier4": 4, "tier5": 5}
    return order.get(tier, 0)


def _detect_hard_tier(escalations: list) -> str:
    """Detect which tier the hard rules already escalated to."""
    for e in escalations:
        rid = e.get("rule_id", "")
        if rid.startswith("AT-"):
            num = rid.replace("AT-", "").replace("0", "")
            try:
                return f"tier{int(num)}"
            except ValueError:
                pass
    return "tier1"  # no escalation = tier 1 (business only)


# ═══════════════════════════════════════════════════════════════════════════════
# COUNTERFACTUAL EXPLANATIONS
# ═══════════════════════════════════════════════════════════════════════════════
#
# "Supplier B would have been recommended if their lead time were 3 days shorter"
# This is what auditors actually need — contrastive explanations.

def generate_counterfactuals(
    scored_suppliers: list[dict],
    fuzzy_results: list[dict],
    top_n: int = 2,
) -> list[dict]:
    """
    For each non-winning supplier in the top N, explain what would need to change
    for them to overtake the winner.

    Returns list of counterfactual explanations for the audit trail.
    """
    if len(scored_suppliers) < 2 or len(fuzzy_results) < 2:
        return []

    winner = scored_suppliers[0]
    winner_score = winner["score"]
    counterfactuals = []

    for i, supplier in enumerate(scored_suppliers[1:top_n + 1], 1):
        gap = winner_score - supplier["score"]
        if gap <= 0:
            continue

        cf = {
            "supplier_id": supplier["id"],
            "supplier_name": supplier["name"],
            "current_score": supplier["score"],
            "gap_to_winner": round(gap, 1),
            "what_if": [],
        }

        # Check each criterion: how much would it need to improve?
        fuzzy = fuzzy_results[i] if i < len(fuzzy_results) else None
        if not fuzzy:
            continue

        for criterion in ["price", "delivery", "compliance", "esg"]:
            current_mems = fuzzy["memberships"].get(criterion, {})
            dominant = max(current_mems, key=current_mems.get) if current_mems else "fair"

            # If they're not "excellent" in this criterion, suggest improvement
            if dominant in ("poor", "fair", "good"):
                next_level = {"poor": "fair", "fair": "good", "good": "excellent"}[dominant]
                cf["what_if"].append(
                    f"If {criterion} improved from '{dominant}' to '{next_level}', "
                    f"the gap would narrow"
                )

        if cf["what_if"]:
            counterfactuals.append(cf)

    return counterfactuals


# ═══════════════════════════════════════════════════════════════════════════════
# SENSITIVITY ANALYSIS (mini-TOPSIS style)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Varies criterion weights ±20% and checks if ranking is stable.
# Directly addresses: "How confident should we be in this ranking?"

def sensitivity_analysis(
    candidates: list[dict],
    base_weights: dict[str, float],
    perturbation: float = 0.2,
    steps: int = 5,
) -> dict:
    """
    Systematically vary each weight by ±perturbation and re-score.

    Returns:
    {
        "ranking_stable": True/False,
        "flips": [{"criterion": "price", "direction": "+15%", "new_winner": "SupplierB"}],
        "stability_score": 0.85,  # 0-1, higher = more stable
        "weight_scenarios": [...]
    }
    """
    if len(candidates) < 2:
        return {"ranking_stable": True, "flips": [], "stability_score": 1.0, "weight_scenarios": []}

    criteria = list(base_weights.keys())
    flips = []
    scenarios = []
    total_scenarios = 0
    stable_scenarios = 0

    # Original ranking
    original_scores = _quick_weighted_score(candidates, base_weights)
    original_winner = max(original_scores, key=original_scores.get)

    for criterion in criteria:
        for direction in range(-steps, steps + 1):
            if direction == 0:
                continue
            delta = direction / steps * perturbation  # e.g. -0.2 to +0.2
            modified_weights = base_weights.copy()
            modified_weights[criterion] = max(0.01, base_weights[criterion] + delta)

            # Renormalize weights to sum to 1
            total_w = sum(modified_weights.values())
            modified_weights = {k: v / total_w for k, v in modified_weights.items()}

            new_scores = _quick_weighted_score(candidates, modified_weights)
            new_winner = max(new_scores, key=new_scores.get)

            total_scenarios += 1
            if new_winner == original_winner:
                stable_scenarios += 1
            else:
                flips.append({
                    "criterion": criterion,
                    "direction": f"{'+' if delta > 0 else ''}{delta:.0%}",
                    "new_winner": new_winner,
                    "new_winner_name": next(
                        (c["name"] for c in candidates if c["id"] == new_winner), "?"
                    ),
                })

            scenarios.append({
                "criterion": criterion,
                "delta": round(delta, 3),
                "winner": new_winner,
                "stable": new_winner == original_winner,
            })

    stability = stable_scenarios / total_scenarios if total_scenarios > 0 else 1.0

    return {
        "ranking_stable": len(flips) == 0,
        "flips": flips,
        "stability_score": round(stability, 3),
        "total_scenarios": total_scenarios,
        "stable_scenarios": stable_scenarios,
        "weight_scenarios": scenarios,
    }


def _quick_weighted_score(candidates: list[dict], weights: dict) -> dict[str, float]:
    """Quick weighted scoring for sensitivity analysis."""
    scores = {}
    for c in candidates:
        breakdown = c.get("score_breakdown", {})
        score = (
            weights.get("price", 0) * (breakdown.get("price_score", 50) / 100) +
            weights.get("delivery", 0) * (breakdown.get("delivery_score", 50) / 100) +
            weights.get("compliance", 0) * (breakdown.get("compliance_score", 50) / 100) +
            weights.get("esg", 0) * (breakdown.get("esg_score_normalized", 50) / 100)
        )
        scores[c["id"]] = round(score * 100, 2)
    return scores
