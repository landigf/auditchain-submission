"""
Risk Scorer — two approaches behind a USE_FUZZY flag.

Approach A (default): linear weighted score, 30 min, zero risk.
Approach B (USE_FUZZY=true): scikit-fuzzy overlay, catches near-miss cases
  like 80% authority usage that are invisible to hard rules.

The risk score is ADDITIVE — it NEVER overrides a hard policy rule.
It powers the UI Risk Meter and enriches the audit trail.
"""
from __future__ import annotations
import os

USE_FUZZY = os.getenv("USE_FUZZY", "false").lower() == "true"


def compute_risk_score(structured_request: dict, requester_context: dict | None = None) -> dict:
    """
    Returns:
        {
            "score": int 0-100,
            "approach": "linear" | "fuzzy",
            "inputs": {...},
            "breakdown": {...},
            "rules_fired": [...]   # only for fuzzy
        }
    """
    if USE_FUZZY:
        return _fuzzy_risk(structured_request, requester_context or {})
    return _linear_risk(structured_request, requester_context or {})


# ── Approach A: Linear ───────────────────────────────────────────────────────

def _linear_risk(structured: dict, ctx: dict) -> dict:
    budget = structured.get("budget_eur") or 0
    deadline_days = structured.get("deadline_days") or 30
    preferred_tier = structured.get("_preferred_tier", "approved")  # injected by query_suppliers
    spending_authority = ctx.get("spending_authority_eur") or 1e9

    budget_ratio = min(budget / 100_000, 1.5) / 1.5       # 0 → budget=0, 1 → budget≥€150k
    authority_ratio = min(budget / spending_authority, 2.0) / 2.0  # 0 → tiny, 1 → 2x authority
    urgency = max(0.0, 1.0 - deadline_days / 30.0)          # 0 → comfortable, 1 → immediate
    vendor_risk_map = {"preferred": 0.1, "approved": 0.4, "spot": 0.8}
    vendor_risk = vendor_risk_map.get(preferred_tier, 0.5)

    # Weighted sum — calibrated to match procurement intuition
    raw = (
        0.35 * min(budget_ratio, 1.0) +
        0.25 * min(authority_ratio, 1.0) +
        0.25 * urgency +
        0.15 * vendor_risk
    )
    score = min(100, round(raw * 100))

    return {
        "score": score,
        "approach": "linear",
        "inputs": {
            "budget_eur": budget,
            "spending_authority_eur": ctx.get("spending_authority_eur"),
            "deadline_days": deadline_days,
            "preferred_tier": preferred_tier,
        },
        "breakdown": {
            "budget_ratio": round(budget_ratio, 3),
            "authority_ratio": round(authority_ratio, 3),
            "urgency": round(urgency, 3),
            "vendor_risk": round(vendor_risk, 3),
        },
        "rules_fired": [],
    }


# ── Approach B: Fuzzy Logic (scikit-fuzzy) ───────────────────────────────────

def _fuzzy_risk(structured: dict, ctx: dict) -> dict:
    try:
        import numpy as np
        import skfuzzy as fuzz
        from skfuzzy import control as ctrl
    except ImportError:
        # Graceful fallback if scikit-fuzzy not installed
        result = _linear_risk(structured, ctx)
        result["fuzzy_error"] = "scikit-fuzzy not installed, fell back to linear"
        return result

    budget = structured.get("budget_eur") or 0
    deadline_days = structured.get("deadline_days") or 30
    preferred_tier = structured.get("_preferred_tier", "approved")
    spending_authority = ctx.get("spending_authority_eur") or 1e9

    # Compute raw input ratios
    br = min(budget / 100_000, 1.5)              # budget ratio: 0 to 1.5
    ar = min(budget / spending_authority, 2.0)   # authority ratio: 0 to 2.0
    urg = max(0.0, min(1.0 - deadline_days / 30.0, 1.0))
    vr_map = {"preferred": 0.1, "approved": 0.4, "spot": 0.8}
    vr = vr_map.get(preferred_tier, 0.5)

    # ── Universe of discourse ─────────────────────────────────────────────────
    budget_ratio    = ctrl.Antecedent(np.arange(0, 1.51, 0.01), 'budget_ratio')
    authority_ratio = ctrl.Antecedent(np.arange(0, 2.01, 0.01), 'authority_ratio')
    urgency         = ctrl.Antecedent(np.arange(0, 1.01, 0.01), 'urgency')
    vendor_risk     = ctrl.Antecedent(np.arange(0, 1.01, 0.01), 'vendor_risk')
    risk            = ctrl.Consequent(np.arange(0, 1.01, 0.01), 'risk')

    # ── Membership functions ──────────────────────────────────────────────────
    budget_ratio['low']    = fuzz.trimf(budget_ratio.universe,    [0,    0,    0.55])
    budget_ratio['medium'] = fuzz.trimf(budget_ratio.universe,    [0.4,  0.7,  0.95])
    budget_ratio['high']   = fuzz.trimf(budget_ratio.universe,    [0.8,  1.5,  1.5])

    authority_ratio['within']     = fuzz.trimf(authority_ratio.universe, [0,    0,    0.75])
    authority_ratio['approaching'] = fuzz.trimf(authority_ratio.universe, [0.6,  0.85, 1.0])
    authority_ratio['exceeded']   = fuzz.trimf(authority_ratio.universe, [0.9,  1.5,  2.0])

    urgency['low']    = fuzz.trimf(urgency.universe, [0,    0,    0.35])
    urgency['medium'] = fuzz.trimf(urgency.universe, [0.2,  0.5,  0.8])
    urgency['high']   = fuzz.trimf(urgency.universe, [0.65, 1.0,  1.0])

    vendor_risk['low']  = fuzz.trimf(vendor_risk.universe, [0,   0,   0.4])
    vendor_risk['high'] = fuzz.trimf(vendor_risk.universe, [0.6, 1.0, 1.0])

    risk['low']    = fuzz.trimf(risk.universe, [0,    0,    0.35])
    risk['medium'] = fuzz.trimf(risk.universe, [0.2,  0.5,  0.8])
    risk['high']   = fuzz.trimf(risk.universe, [0.65, 1.0,  1.0])

    # ── Rules ─────────────────────────────────────────────────────────────────
    rule_defs = [
        ("budget HIGH & urgency HIGH → risk HIGH",      ctrl.Rule(budget_ratio['high'] & urgency['high'], risk['high'])),
        ("budget HIGH & vendor HIGH → risk HIGH",       ctrl.Rule(budget_ratio['high'] & vendor_risk['high'], risk['high'])),
        ("authority EXCEEDED → risk HIGH",              ctrl.Rule(authority_ratio['exceeded'], risk['high'])),
        ("authority APPROACHING → risk MEDIUM",         ctrl.Rule(authority_ratio['approaching'], risk['medium'])),
        ("budget MEDIUM | urgency MEDIUM → risk MEDIUM", ctrl.Rule(budget_ratio['medium'] | urgency['medium'], risk['medium'])),
        ("budget LOW & vendor LOW → risk LOW",          ctrl.Rule(budget_ratio['low'] & vendor_risk['low'], risk['low'])),
    ]
    rules = [r for _, r in rule_defs]
    rule_labels = [label for label, _ in rule_defs]

    risk_ctrl = ctrl.ControlSystem(rules)
    sim = ctrl.ControlSystemSimulation(risk_ctrl)

    sim.input['budget_ratio'] = br
    sim.input['authority_ratio'] = ar
    sim.input['urgency'] = urg
    sim.input['vendor_risk'] = vr

    sim.compute()
    raw_score = float(sim.output['risk'])
    score = min(100, round(raw_score * 100))

    # Which rules fired (strength > 0.05)? We approximate from membership degrees.
    def membership_degree(var_name, term_name, value):
        """Compute degree for a single term at a given crisp value."""
        var_map = {
            'budget_ratio': budget_ratio,
            'authority_ratio': authority_ratio,
            'urgency': urgency,
            'vendor_risk': vendor_risk,
        }
        v = var_map[var_name]
        return float(fuzz.interp_membership(v.universe, v[term_name].mf, value))

    memberships = {
        "budget_ratio":    {t: round(membership_degree('budget_ratio', t, br), 3) for t in ['low', 'medium', 'high']},
        "authority_ratio": {t: round(membership_degree('authority_ratio', t, ar), 3) for t in ['within', 'approaching', 'exceeded']},
        "urgency":         {t: round(membership_degree('urgency', t, urg), 3) for t in ['low', 'medium', 'high']},
        "vendor_risk":     {t: round(membership_degree('vendor_risk', t, vr), 3) for t in ['low', 'high']},
    }

    return {
        "score": score,
        "approach": "fuzzy",
        "inputs": {
            "budget_eur": budget,
            "spending_authority_eur": ctx.get("spending_authority_eur"),
            "deadline_days": deadline_days,
            "preferred_tier": preferred_tier,
        },
        "breakdown": {
            "budget_ratio": round(br, 3),
            "authority_ratio": round(ar, 3),
            "urgency": round(urg, 3),
            "vendor_risk": round(vr, 3),
        },
        "memberships": memberships,
        "rules_fired": rule_labels,
        "raw_output": round(raw_score, 4),
    }
