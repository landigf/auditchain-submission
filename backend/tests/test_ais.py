"""
Tests for compute_ais() — Audit Intelligence Score.
Covers: 5 components (completeness, rule_coverage, traceability, contestability,
        escalation_appropriateness), grading thresholds, flags.
"""
import pytest
from agent.tools import compute_ais


def _base_inputs(req_overrides=None, policy_overrides=None, supplier_overrides=None,
                 scoring_overrides=None, decision_overrides=None):
    """Build a minimal set of inputs for compute_ais with overridable parts."""
    req = {
        "item_description": "Test", "category": "hardware", "quantity": 10,
        "budget_eur": 50000, "deadline_days": 14, "ambiguities": [],
    }
    if req_overrides:
        req.update(req_overrides)

    policy = {"violations": [], "warnings": [], "escalations": []}
    if policy_overrides:
        policy.update(policy_overrides)

    supplier = {"candidates": [], "disqualified": []}
    if supplier_overrides:
        supplier.update(supplier_overrides)

    scoring = {"scored": [{"id": "S1", "name": "Test", "score": 80}]}
    if scoring_overrides:
        scoring.update(scoring_overrides)

    decision = {
        "decision_type": "approved",
        "reasoning_narrative": "This is a test narrative for auditing purposes.",
    }
    if decision_overrides:
        decision.update(decision_overrides)

    return req, policy, supplier, scoring, decision


# ── Completeness (max 25) ────────────────────────────────────────────────────

class TestCompleteness:
    def test_all_fields_max_25(self):
        req, pol, sup, sco, dec = _base_inputs()
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["completeness"] == 25

    def test_missing_one_field_20(self):
        req, pol, sup, sco, dec = _base_inputs(req_overrides={"budget_eur": None})
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["completeness"] == 20

    def test_ambiguities_reduce_score(self):
        req, pol, sup, sco, dec = _base_inputs(
            req_overrides={"ambiguities": ["a1", "a2"]}
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        # 25 - 2*3 = 19
        assert ais["components"]["completeness"] == 19

    def test_no_fields_0(self):
        req = {}
        _, pol, sup, sco, dec = _base_inputs()
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["completeness"] == 0

    def test_three_ambiguities_reduce_more(self):
        req, pol, sup, sco, dec = _base_inputs(
            req_overrides={"ambiguities": ["a1", "a2", "a3"]}
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        # 25 - 9 = 16
        assert ais["components"]["completeness"] == 16


# ── Rule Coverage (max 20) ───────────────────────────────────────────────────

class TestRuleCoverage:
    def test_always_20(self):
        """Line 661: rules_checked >= 0 is ALWAYS True → always 20."""
        req, pol, sup, sco, dec = _base_inputs()
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["rule_coverage"] == 20

    def test_with_rules_still_20(self):
        req, pol, sup, sco, dec = _base_inputs(
            policy_overrides={"warnings": [{"rule_id": "W1"}], "escalations": [{"rule_id": "E1"}]}
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["rule_coverage"] == 20


# ── Decision Traceability (max 25) ───────────────────────────────────────────

class TestDecisionTraceability:
    def test_full_traceability_25(self):
        req, pol, sup, sco, dec = _base_inputs()
        ais = compute_ais(req, pol, sup, sco, dec)
        # decision_type(8) + narrative(10) + scored(7) = 25
        assert ais["components"]["decision_traceability"] == 25

    def test_no_narrative_15(self):
        req, pol, sup, sco, dec = _base_inputs(
            decision_overrides={"decision_type": "approved", "reasoning_narrative": None}
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        # 8 + 0 + 7 = 15
        assert ais["components"]["decision_traceability"] == 15

    def test_no_scored_18(self):
        req, pol, sup, sco, dec = _base_inputs(scoring_overrides={"scored": []})
        ais = compute_ais(req, pol, sup, sco, dec)
        # 8 + 10 + 0 = 18
        assert ais["components"]["decision_traceability"] == 18

    def test_nothing_0(self):
        req, pol, sup, sco, dec = _base_inputs(
            scoring_overrides={"scored": []},
            decision_overrides={"decision_type": None, "reasoning_narrative": None},
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["decision_traceability"] == 0


# ── Contestability (max 20) ───────────────────────────────────────────────────

class TestContestability:
    def test_no_disqualified_20(self):
        req, pol, sup, sco, dec = _base_inputs()
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["contestability"] == 20

    def test_all_have_reasons_20(self):
        req, pol, sup, sco, dec = _base_inputs(
            supplier_overrides={"disqualified": [
                {"id": "S1", "disqualification_reasons": ["R06: ESG too low"]},
            ]}
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["contestability"] == 20

    def test_missing_reasons_8(self):
        req, pol, sup, sco, dec = _base_inputs(
            supplier_overrides={"disqualified": [
                {"id": "S1", "disqualification_reasons": []},
            ]}
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["contestability"] == 8


# ── Escalation Appropriateness (max 10) ──────────────────────────────────────

class TestEscalationAppropriateness:
    def test_escalated_with_escalations_10(self):
        req, pol, sup, sco, dec = _base_inputs(
            policy_overrides={"escalations": [{"rule_id": "AT-003"}]},
            decision_overrides={"decision_type": "escalated"},
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["escalation_appropriateness"] == 10

    def test_approved_no_escalations_10(self):
        req, pol, sup, sco, dec = _base_inputs()
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["escalation_appropriateness"] == 10

    def test_approved_with_escalations_0(self):
        """BUG-like: approved despite escalation rules → 0 points."""
        req, pol, sup, sco, dec = _base_inputs(
            policy_overrides={"escalations": [{"rule_id": "AT-003"}]},
            decision_overrides={"decision_type": "approved"},
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["escalation_appropriateness"] == 0

    def test_rejected_7(self):
        req, pol, sup, sco, dec = _base_inputs(
            decision_overrides={"decision_type": "rejected"},
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        assert ais["components"]["escalation_appropriateness"] == 7


# ── Grading ───────────────────────────────────────────────────────────────────

class TestGrading:
    def test_grade_85_plus_audit_ready(self):
        req, pol, sup, sco, dec = _base_inputs()
        ais = compute_ais(req, pol, sup, sco, dec)
        # 25 + 20 + 25 + 20 + 10 = 100
        assert ais["grade"] == "Audit-Ready"
        assert ais["eu_ai_act_article_13_compliant"] is True

    def test_grade_below_85_review(self):
        req, pol, sup, sco, dec = _base_inputs(
            req_overrides={"ambiguities": ["a1", "a2", "a3", "a4", "a5", "a6"]},
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        # completeness: 25 - 18 = 7; total = 7+20+25+20+10 = 82
        assert ais["grade"] == "Review Recommended"
        assert ais["eu_ai_act_article_13_compliant"] is False

    def test_grade_below_65_manual_review(self):
        req = {}
        _, pol, sup, sco, dec = _base_inputs(
            scoring_overrides={"scored": []},
            decision_overrides={"decision_type": None, "reasoning_narrative": None},
            supplier_overrides={"disqualified": [{"id": "X", "disqualification_reasons": []}]},
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        # 0 + 20 + 0 + 8 + 7 = 35
        assert ais["grade"] == "Manual Review Required"


# ── Flags ─────────────────────────────────────────────────────────────────────

class TestFlags:
    def test_escalation_mismatch_critical_flag(self):
        req, pol, sup, sco, dec = _base_inputs(
            policy_overrides={"escalations": [{"rule_id": "AT-003"}]},
            decision_overrides={"decision_type": "approved"},
        )
        ais = compute_ais(req, pol, sup, sco, dec)
        assert any("CRITICAL" in f for f in ais["flags"])

    def test_no_flags_when_perfect(self):
        req, pol, sup, sco, dec = _base_inputs()
        ais = compute_ais(req, pol, sup, sco, dec)
        assert len(ais["flags"]) == 0

    def test_low_completeness_flag(self):
        req = {"item_description": "X"}
        _, pol, sup, sco, dec = _base_inputs()
        ais = compute_ais(req, pol, sup, sco, dec)
        assert any("ambiguous or missing" in f for f in ais["flags"])
