"""
Edge case and boundary tests for maximum robustness & escalation logic points.
Covers: budget boundaries, scoring edge cases, degenerate inputs, conditional restrictions.
"""
import pytest
from agent.tools import check_policy, query_suppliers, score_suppliers, compute_ais
from agent.risk_scorer import _linear_risk
from agent.pipeline import make_decision
from tests.conftest import _make_request


def _rule_ids(result_list):
    return [r["rule_id"] for r in result_list]


def _candidate(id="S1", price=500.0, days=14, esg=75, tier="preferred",
               contract="active", **kw):
    base = {
        "id": id, "supplier_id": id, "name": f"Supplier {id}",
        "category": "hardware", "category_l2": "Laptops",
        "unit_price_eur": price, "min_quantity": 1,
        "delivery_days": days, "compliance_status": "approved",
        "esg_score": esg, "preferred_tier": tier,
        "contract_status": contract, "country": "CH",
        "service_regions": "CH;DE;FR", "eu_based": True,
        "data_residency_supported": True, "notes": "",
        "disqualified": False, "disqualification_reasons": [],
    }
    base.update(kw)
    return base


# ── Budget Boundary Values ────────────────────────────────────────────────────

class TestBudgetBoundaries:
    """Parametrized test at exact budget thresholds."""

    @pytest.mark.parametrize("budget,expected_rule,expected_list", [
        (24999.99, None, None),
        (25000.00, "AT-002", "warnings"),
        (99999.99, "AT-002", "warnings"),
        (100000.00, "AT-003", "escalations"),
        (499999.99, "AT-003", "escalations"),
        (500000.00, "AT-004", "escalations"),
        (4999999.99, "AT-004", "escalations"),
        (5000000.00, "AT-005", "escalations"),
    ])
    def test_budget_boundary(self, budget, expected_rule, expected_list,
                             seed_rules, db_session):
        req = _make_request(budget_eur=budget)
        result = check_policy(req, list(seed_rules.values()))
        if expected_rule is None:
            at_rules = [r for r in result["warnings"] + result["escalations"]
                        if r["rule_id"].startswith("AT-0")]
            assert len(at_rules) == 0
        else:
            assert expected_rule in _rule_ids(result[expected_list])


# ── Scoring Edge Cases ────────────────────────────────────────────────────────

class TestScoringEdgeCases:
    def test_all_suppliers_tied(self):
        """3 identical suppliers → deterministic rank assignment."""
        candidates = [
            _candidate(id="S-A", price=500, days=14, esg=75),
            _candidate(id="S-B", price=500, days=14, esg=75),
            _candidate(id="S-C", price=500, days=14, esg=75),
        ]
        result = score_suppliers(candidates, _make_request())
        ranks = [s["rank"] for s in result["scored"]]
        assert sorted(ranks) == [1, 2, 3]

    def test_single_supplier_max_scores(self):
        """Single candidate gets price=100 and delivery=100 (range=0 → or 1)."""
        candidates = [_candidate(id="S-ONLY", price=500, days=14)]
        result = score_suppliers(candidates, _make_request())
        bd = result["scored"][0]["score_breakdown"]
        assert bd["price_score"] == 100.0
        assert bd["delivery_score"] == 100.0

    def test_esg_0_no_crash(self):
        candidates = [_candidate(id="S-ESG0", esg=0)]
        result = score_suppliers(candidates, _make_request())
        assert result["scored"][0]["score_breakdown"]["esg_score_normalized"] == 0.0

    def test_esg_100_normalized(self):
        candidates = [_candidate(id="S-ESG100", esg=100)]
        result = score_suppliers(candidates, _make_request())
        assert result["scored"][0]["score_breakdown"]["esg_score_normalized"] == 100.0


# ── Degenerate Inputs ─────────────────────────────────────────────────────────

class TestDegenerateInputs:
    def test_no_suppliers_in_category(self, make_supplier, db_session):
        """Category with 0 suppliers → empty candidates, no infeasibility."""
        req = _make_request(category="nonexistent")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert len(result["candidates"]) == 0
        assert result["infeasibility"] is None

    def test_all_suppliers_disqualified(self, make_supplier, db_session):
        """All blocked → empty candidates."""
        make_supplier(id="S-BLK1", compliance_status="blocked")
        make_supplier(id="S-BLK2", compliance_status="blocked")
        req = _make_request()
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert len(result["candidates"]) == 0
        assert len(result["disqualified"]) == 2

    def test_zero_budget_no_threshold(self, seed_rules, db_session):
        req = _make_request(budget_eur=0)
        result = check_policy(req, list(seed_rules.values()))
        at_rules = [r for r in result["warnings"] + result["escalations"]
                    if r["rule_id"].startswith("AT-")]
        assert len(at_rules) == 0

    def test_zero_quantity(self):
        """quantity=0 is falsy → defaults to 1 in score_suppliers."""
        candidates = [_candidate(id="S-ZQ", price=500)]
        req = _make_request(quantity=0)
        result = score_suppliers(candidates, req)
        # quantity=0 triggers `or 1` fallback → total_cost = 500 * 1
        assert result["scored"][0]["total_cost_eur"] == 500.0

    def test_very_large_budget(self, seed_rules, db_session):
        req = _make_request(budget_eur=1e12)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-005" in _rule_ids(result["escalations"])

    def test_very_large_quantity_infeasible(self, make_supplier, db_session):
        """qty 1M with huge capacity → infeasible budget."""
        make_supplier(id="S-NORMAL", unit_price_eur=500.0, capacity_per_month=10000000)
        req = _make_request(quantity=1000000, budget_eur=1000)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert result["infeasibility"] is not None


# ── Decision Edge Cases ───────────────────────────────────────────────────────

class TestDecisionEdgeCases:
    def test_infeasibility_with_violations(self):
        """Infeasibility takes precedence over violations → escalated."""
        result = make_decision(
            {}, {"violations": [{"detail": "blocked"}], "escalations": [], "warnings": []},
            {"scored": [{"id": "S1", "score": 80, "name": "Test"}]},
            {"infeasibility": {"infeasible": True, "reason": "Budget"}, "candidates": [], "disqualified": []},
        )
        assert result["decision_type"] == "escalated"

    def test_empty_scoring_result_none(self):
        """scoring_result=None → scored defaults to []."""
        result = make_decision(
            {}, {"violations": [], "escalations": [], "warnings": []},
            None,  # scoring_result is None
            {"infeasibility": None, "candidates": [], "disqualified": []},
        )
        assert result["decision_type"] == "rejected"

    def test_escalation_with_no_scored_suppliers(self):
        result = make_decision(
            {}, {"violations": [], "escalations": [{"detail": "AT-003"}], "warnings": []},
            {"scored": []},
            {"infeasibility": None, "candidates": [], "disqualified": []},
        )
        assert result["decision_type"] == "escalated"
        assert result["recommended_supplier"] is None


# ── Risk Scorer Edge Cases ────────────────────────────────────────────────────

class TestRiskEdgeCases:
    def test_negative_deadline(self):
        """Negative deadline → urgency = max(0, 1 - (-5)/30) = max(0, 1.167) → clamped by min(1.0)."""
        result = _linear_risk(
            {"budget_eur": 50000, "deadline_days": -5, "_preferred_tier": "approved"},
            {"spending_authority_eur": 100000},
        )
        # urgency = max(0.0, 1.0 - (-5)/30) = max(0.0, 1.167) = 1.167
        # BUT: in the weighted sum it's just a float > 1.0, score is capped at 100
        assert 0 <= result["score"] <= 100

    def test_spending_authority_zero(self):
        """Authority=0 → fallback to 1e9 via `or 1e9`."""
        result = _linear_risk(
            {"budget_eur": 50000, "deadline_days": 14, "_preferred_tier": "approved"},
            {"spending_authority_eur": 0},
        )
        # spending_authority_eur = 0, triggers `or 1e9`
        assert result["breakdown"]["authority_ratio"] == 0.0


# ── AIS Edge Cases ────────────────────────────────────────────────────────────

class TestAISEdgeCases:
    def test_empty_policy_results(self):
        ais = compute_ais(
            {"item_description": "X", "category": "hw", "quantity": 1, "budget_eur": 100, "deadline_days": 10},
            {"violations": [], "warnings": [], "escalations": []},
            {"candidates": [], "disqualified": []},
            {"scored": [{"id": "S1", "score": 80}]},
            {"decision_type": "approved", "reasoning_narrative": "OK"},
        )
        assert ais["score"] > 0

    def test_all_none_decision_fields(self):
        ais = compute_ais(
            {},
            {"violations": [], "warnings": [], "escalations": []},
            {"candidates": [], "disqualified": []},
            {"scored": []},
            {"decision_type": None, "reasoning_narrative": None},
        )
        # Should not crash, just give low score
        assert isinstance(ais["score"], int)
        assert ais["score"] >= 0


# ── Multiple Disqualification Reasons ─────────────────────────────────────────

class TestMultipleDisqualifications:
    def test_supplier_with_multiple_reasons(self, make_supplier, db_session):
        """A supplier that fails ESG + geographic + capacity."""
        make_supplier(id="S-MULTI", esg_score=50, service_regions="DE;FR",
                      capacity_per_month=5)
        req = _make_request(delivery_country="CH", quantity=100)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq = [s for s in result["disqualified"] if s["id"] == "S-MULTI"]
        assert len(dq) == 1
        reasons = dq[0]["disqualification_reasons"]
        assert len(reasons) >= 3  # R06 + Geographic + ER-006
