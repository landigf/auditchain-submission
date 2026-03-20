"""
Tests for make_decision() — the decision logic.
Covers: priority order, confidence calculation, output shape.
"""
import pytest
from agent.pipeline import make_decision


def _scored(id="S1", score=80, **kw):
    base = {"id": id, "name": f"Supplier {id}", "score": score,
            "total_cost_eur": 5000, "detail": "test"}
    base.update(kw)
    return base


def _policy(violations=None, escalations=None, warnings=None):
    return {
        "violations": violations or [],
        "escalations": escalations or [],
        "warnings": warnings or [],
    }


def _scoring(scored=None):
    return {"scored": scored or []}


def _suppliers(infeasibility=None):
    return {"infeasibility": infeasibility, "candidates": [], "disqualified": []}


# ── Decision Priority ─────────────────────────────────────────────────────────

class TestDecisionPriority:
    """Infeasibility > Violations > Escalations > No-candidates > Approved."""

    def test_infeasible_escalated(self):
        result = make_decision(
            {}, _policy(),
            _scoring([_scored()]),
            _suppliers(infeasibility={"infeasible": True, "reason": "Budget too low"}),
        )
        assert result["decision_type"] == "escalated"
        assert result["recommended_supplier"] is None

    def test_violations_rejected(self):
        result = make_decision(
            {}, _policy(violations=[{"detail": "R03 blocked"}]),
            _scoring([_scored()]),
            _suppliers(),
        )
        assert result["decision_type"] == "rejected"

    def test_escalations_escalated(self):
        result = make_decision(
            {}, _policy(escalations=[{"detail": "AT-003 budget"}]),
            _scoring([_scored()]),
            _suppliers(),
        )
        assert result["decision_type"] == "escalated"

    def test_no_scored_rejected(self):
        result = make_decision({}, _policy(), _scoring([]), _suppliers())
        assert result["decision_type"] == "rejected"
        assert "No compliant" in result["rejection_reason"]

    def test_clean_approved(self):
        result = make_decision(
            {}, _policy(),
            _scoring([_scored(score=85)]),
            _suppliers(),
        )
        assert result["decision_type"] == "approved"

    def test_infeasibility_over_violations(self):
        result = make_decision(
            {}, _policy(violations=[{"detail": "blocked"}]),
            _scoring([_scored()]),
            _suppliers(infeasibility={"infeasible": True, "reason": "Budget"}),
        )
        assert result["decision_type"] == "escalated"

    def test_violations_over_escalations(self):
        result = make_decision(
            {}, _policy(violations=[{"detail": "blocked"}],
                        escalations=[{"detail": "AT-003"}]),
            _scoring([_scored()]),
            _suppliers(),
        )
        assert result["decision_type"] == "rejected"


# ── Confidence ────────────────────────────────────────────────────────────────

class TestConfidence:
    """Formula: min(0.99, 0.6 + (gap / 100) * 2)."""

    def test_single_supplier_confidence_1(self):
        result = make_decision(
            {}, _policy(),
            _scoring([_scored(score=90)]),
            _suppliers(),
        )
        assert result["confidence"] == 1.0

    def test_large_gap_high_confidence(self):
        result = make_decision(
            {}, _policy(),
            _scoring([_scored(id="S1", score=90), _scored(id="S2", score=70)]),
            _suppliers(),
        )
        # gap=20, 0.6 + (20/100)*2 = 0.6 + 0.4 = 1.0 → min(0.99) = 0.99
        assert result["confidence"] == 0.99

    def test_small_gap_low_confidence(self):
        result = make_decision(
            {}, _policy(),
            _scoring([_scored(id="S1", score=80), _scored(id="S2", score=79)]),
            _suppliers(),
        )
        # gap=1, 0.6 + (1/100)*2 = 0.6 + 0.02 = 0.62
        assert result["confidence"] == 0.62

    def test_zero_gap_confidence_06(self):
        result = make_decision(
            {}, _policy(),
            _scoring([_scored(id="S1", score=80), _scored(id="S2", score=80)]),
            _suppliers(),
        )
        assert result["confidence"] == 0.6


# ── Output Shape ──────────────────────────────────────────────────────────────

class TestOutputShape:
    def test_approved_has_recommended_supplier(self):
        result = make_decision(
            {}, _policy(),
            _scoring([_scored(id="S1", score=90)]),
            _suppliers(),
        )
        assert result["recommended_supplier"] is not None
        assert result["recommended_supplier"]["id"] == "S1"

    def test_approved_has_alternatives(self):
        result = make_decision(
            {}, _policy(),
            _scoring([_scored(id="S1", score=90), _scored(id="S2", score=80),
                       _scored(id="S3", score=70)]),
            _suppliers(),
        )
        alt_ids = [s["id"] for s in result["alternatives"]]
        assert alt_ids == ["S2", "S3"]

    def test_rejected_has_rejection_reason(self):
        result = make_decision(
            {}, _policy(violations=[{"detail": "R03: blocked"}]),
            _scoring([_scored()]),
            _suppliers(),
        )
        assert result["rejection_reason"] is not None
        assert "R03" in result["rejection_reason"]

    def test_escalated_has_escalation_reason(self):
        result = make_decision(
            {}, _policy(escalations=[{"detail": "AT-003 budget escalation"}]),
            _scoring([_scored()]),
            _suppliers(),
        )
        assert result["escalation_reason"] is not None
        assert "AT-003" in result["escalation_reason"]

    def test_escalated_still_recommends_if_scored(self):
        result = make_decision(
            {}, _policy(escalations=[{"detail": "escalation"}]),
            _scoring([_scored(id="S1", score=90)]),
            _suppliers(),
        )
        assert result["recommended_supplier"]["id"] == "S1"
