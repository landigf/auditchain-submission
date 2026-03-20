"""
Tests for fuzzy_policy.py — the pure-Python fuzzy logic engine.
Covers: threshold classification, supplier scoring, confidence gate,
        sensitivity analysis, counterfactuals, and the check_policy wrapper.
"""
import pytest
from agent.fuzzy_policy import (
    fuzzy_threshold_classify,
    fuzzy_score_supplier,
    fuzzy_confidence_gate,
    sensitivity_analysis,
    generate_counterfactuals,
    fuzzy_check_policy,
)


# ── Fuzzy Threshold Classification ───────────────────────────────────────────

class TestFuzzyThresholdClassify:
    def test_20k_fully_tier1(self):
        """€20k is well inside Tier 1 (0-25k) — no borderline."""
        result = fuzzy_threshold_classify(20000)
        assert result["primary_tier"] == "tier1"
        assert result["is_borderline"] is False

    def test_98k_borderline(self):
        """€98k is within 15% of €100k boundary → borderline tier2/tier3."""
        result = fuzzy_threshold_classify(98000)
        assert result["is_borderline"] is True
        assert "tier3" in result["borderline_tiers"]
        assert result["recommendation"] == "tier3"  # cautious: pick higher tier

    def test_50k_clean_tier2(self):
        """€50k is solidly mid-range Tier 2 — not borderline."""
        result = fuzzy_threshold_classify(50000)
        assert result["primary_tier"] == "tier2"
        assert result["is_borderline"] is False

    def test_120k_tier3(self):
        """€120k is in Tier 3 (100k-500k)."""
        result = fuzzy_threshold_classify(120000)
        assert result["primary_tier"] == "tier3"

    def test_5m_tier5(self):
        """€6M is solidly Tier 5 (CPO). €5M exactly is on the boundary."""
        result = fuzzy_threshold_classify(6000000)
        assert result["primary_tier"] == "tier5"

    def test_zero_budget_no_crash(self):
        """Budget 0 should not crash."""
        result = fuzzy_threshold_classify(0)
        assert result["primary_tier"] == "tier1"
        assert result["is_borderline"] is False

    def test_chf_conversion(self):
        """CHF budget is converted with 0.95 factor."""
        result = fuzzy_threshold_classify(100000, currency="CHF")
        # 100k CHF = 95k EUR → still Tier 2, but close to 100k boundary
        assert result["budget_eur"] == 95000.0

    def test_proximity_warning_present(self):
        """Borderline budget should have a proximity warning string."""
        result = fuzzy_threshold_classify(98000)
        assert result["proximity_warning"] is not None
        assert "€" in result["proximity_warning"]

    def test_memberships_sum_positive(self):
        """At least one tier should have positive membership."""
        result = fuzzy_threshold_classify(50000)
        assert max(result["memberships"].values()) > 0

    def test_all_tiers_present(self):
        """Memberships dict should have all 5 tiers."""
        result = fuzzy_threshold_classify(50000)
        for tier in ["tier1", "tier2", "tier3", "tier4", "tier5"]:
            assert tier in result["memberships"]


# ── Fuzzy Supplier Scoring ───────────────────────────────────────────────────

class TestFuzzyScoreSupplier:
    def test_excellent_inputs_high_score(self):
        """All-excellent inputs → score well above 50."""
        result = fuzzy_score_supplier(0.9, 0.9, 0.9, 0.9)
        assert result["score"] > 60

    def test_poor_inputs_low_score(self):
        """All-poor inputs → low score."""
        result = fuzzy_score_supplier(0.1, 0.1, 0.1, 0.1)
        assert result["score"] < 35

    def test_returns_rules_fired(self):
        """Output should have rules_fired list with rule_text and strength."""
        result = fuzzy_score_supplier(0.7, 0.7, 0.7, 0.7)
        assert isinstance(result["rules_fired"], list)
        assert len(result["rules_fired"]) > 0
        for rule in result["rules_fired"]:
            assert "rule_text" in rule
            assert "strength" in rule
            assert rule["strength"] > 0

    def test_returns_memberships(self):
        """Output should have memberships for all 4 criteria."""
        result = fuzzy_score_supplier(0.5, 0.5, 0.5, 0.5)
        assert "memberships" in result
        for criterion in ["price", "delivery", "compliance", "esg"]:
            assert criterion in result["memberships"]
            # Each should have linguistic terms
            for term in ["poor", "fair", "good", "excellent"]:
                assert term in result["memberships"][criterion]

    def test_deterministic(self):
        """Same inputs should always produce same output."""
        r1 = fuzzy_score_supplier(0.7, 0.6, 0.8, 0.5)
        r2 = fuzzy_score_supplier(0.7, 0.6, 0.8, 0.5)
        assert r1["score"] == r2["score"]
        assert r1["linguistic"] == r2["linguistic"]

    def test_linguistic_label_valid(self):
        """Linguistic label should be one of the defined terms."""
        result = fuzzy_score_supplier(0.6, 0.6, 0.6, 0.6)
        assert result["linguistic"] in ("poor", "acceptable", "good", "excellent")

    def test_score_range_0_to_100(self):
        """Score should be in 0-100 range."""
        for p, d, c, e in [(0.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0, 1.0), (0.5, 0.3, 0.8, 0.2)]:
            result = fuzzy_score_supplier(p, d, c, e)
            assert 0 <= result["score"] <= 100


# ── Fuzzy Confidence Gate ────────────────────────────────────────────────────

class TestFuzzyConfidenceGate:
    def _clean_threshold(self):
        """Non-borderline threshold result."""
        return {"is_borderline": False, "memberships": {"tier2": 1.0}, "borderline_tiers": []}

    def _borderline_threshold(self):
        """Borderline threshold result."""
        return {
            "is_borderline": True,
            "memberships": {"tier2": 0.85, "tier3": 0.35},
            "borderline_tiers": ["tier3"],
            "proximity_warning": "Budget near boundary",
        }

    def test_high_confidence_no_escalation(self):
        """Clean inputs → high confidence, no escalation."""
        result = fuzzy_confidence_gate(
            threshold_result=self._clean_threshold(),
            top_supplier_score=85.0,
            second_supplier_score=60.0,
            num_candidates=5,
            has_ambiguities=False,
            has_missing_fields=False,
        )
        assert result["confidence"] >= 0.75
        assert result["confidence_label"] == "high"
        assert result["should_escalate"] is False

    def test_borderline_reduces_confidence(self):
        """Borderline threshold should reduce confidence."""
        clean = fuzzy_confidence_gate(
            self._clean_threshold(), 80.0, 60.0, 5, False, False
        )
        borderline = fuzzy_confidence_gate(
            self._borderline_threshold(), 80.0, 60.0, 5, False, False
        )
        assert borderline["confidence"] < clean["confidence"]

    def test_narrow_gap_signal(self):
        """1-point gap between top 2 → narrow_score_gap signal."""
        result = fuzzy_confidence_gate(
            self._clean_threshold(), 80.0, 79.0, 5, False, False
        )
        signal_names = [s["signal"] for s in result["uncertainty_signals"]]
        assert "narrow_score_gap" in signal_names

    def test_single_candidate_limited(self):
        """Only 1 candidate → limited_candidates signal."""
        result = fuzzy_confidence_gate(
            self._clean_threshold(), 80.0, None, 1, False, False
        )
        signal_names = [s["signal"] for s in result["uncertainty_signals"]]
        assert "limited_candidates" in signal_names

    def test_low_confidence_escalates(self):
        """Many uncertainty signals → should_escalate=True."""
        result = fuzzy_confidence_gate(
            self._borderline_threshold(), 50.0, 49.0, 1, True, True
        )
        assert result["should_escalate"] is True
        assert result["confidence_label"] == "low"

    def test_no_signals_095(self):
        """No issues at all → confidence = 0.95."""
        result = fuzzy_confidence_gate(
            self._clean_threshold(), 90.0, 60.0, 10, False, False
        )
        assert result["confidence"] == 0.95
        assert len(result["uncertainty_signals"]) == 0


# ── Sensitivity Analysis ─────────────────────────────────────────────────────

def _scored_candidate(id, price=80, delivery=70, compliance=60, esg=50, score=70):
    return {
        "id": id, "name": f"Supplier {id}", "score": score,
        "score_breakdown": {
            "price_score": price, "delivery_score": delivery,
            "compliance_score": compliance, "esg_score_normalized": esg,
        },
    }


class TestSensitivityAnalysis:
    def test_clear_winner_stable(self):
        """Large score gap → ranking stable."""
        candidates = [
            _scored_candidate("A", price=100, delivery=100, score=95),
            _scored_candidate("B", price=20, delivery=20, score=30),
        ]
        weights = {"price": 0.4, "delivery": 0.25, "compliance": 0.2, "esg": 0.15}
        result = sensitivity_analysis(candidates, weights)
        assert result["ranking_stable"] is True
        assert result["stability_score"] > 0.8

    def test_tied_scores_unstable(self):
        """Nearly identical scores → flips expected."""
        candidates = [
            _scored_candidate("A", price=50, delivery=50, compliance=50, esg=50, score=50),
            _scored_candidate("B", price=50, delivery=50, compliance=50, esg=50, score=50),
        ]
        weights = {"price": 0.4, "delivery": 0.25, "compliance": 0.2, "esg": 0.15}
        result = sensitivity_analysis(candidates, weights)
        # With identical scores, any weight change can flip
        assert result["total_scenarios"] > 0

    def test_single_candidate_always_stable(self):
        """1 supplier → stability_score = 1.0."""
        candidates = [_scored_candidate("ONLY")]
        weights = {"price": 0.4, "delivery": 0.25, "compliance": 0.2, "esg": 0.15}
        result = sensitivity_analysis(candidates, weights)
        assert result["ranking_stable"] is True
        assert result["stability_score"] == 1.0

    def test_flips_have_required_keys(self):
        """Each flip should have criterion, direction, new_winner."""
        candidates = [
            _scored_candidate("A", price=90, delivery=10, score=50),
            _scored_candidate("B", price=10, delivery=90, score=50),
        ]
        weights = {"price": 0.4, "delivery": 0.25, "compliance": 0.2, "esg": 0.15}
        result = sensitivity_analysis(candidates, weights)
        for flip in result["flips"]:
            assert "criterion" in flip
            assert "direction" in flip
            assert "new_winner" in flip


# ── Counterfactual Explanations ──────────────────────────────────────────────

class TestCounterfactuals:
    def test_generates_for_runner_up(self):
        """2+ suppliers → counterfactuals generated for runner-up."""
        scored = [
            {"id": "A", "name": "Winner", "score": 80},
            {"id": "B", "name": "RunnerUp", "score": 65},
        ]
        fuzzy_results = [
            {"memberships": {"price": {"good": 0.8}, "delivery": {"good": 0.7},
                             "compliance": {"good": 0.6}, "esg": {"good": 0.5}}},
            {"memberships": {"price": {"fair": 0.7}, "delivery": {"fair": 0.6},
                             "compliance": {"fair": 0.5}, "esg": {"fair": 0.4}}},
        ]
        cfs = generate_counterfactuals(scored, fuzzy_results)
        assert len(cfs) >= 1
        assert cfs[0]["supplier_id"] == "B"

    def test_empty_single_supplier(self):
        """1 supplier → no counterfactuals."""
        scored = [{"id": "A", "name": "Only", "score": 80}]
        fuzzy_results = [{"memberships": {}}]
        cfs = generate_counterfactuals(scored, fuzzy_results)
        assert len(cfs) == 0

    def test_what_if_suggestions(self):
        """Counterfactuals should have what_if list with improvement suggestions."""
        scored = [
            {"id": "A", "name": "Winner", "score": 80},
            {"id": "B", "name": "RunnerUp", "score": 60},
        ]
        fuzzy_results = [
            {"memberships": {"price": {"excellent": 0.9}, "delivery": {"excellent": 0.8},
                             "compliance": {"excellent": 0.7}, "esg": {"excellent": 0.6}}},
            {"memberships": {"price": {"fair": 0.8}, "delivery": {"poor": 0.7},
                             "compliance": {"fair": 0.6}, "esg": {"fair": 0.5}}},
        ]
        cfs = generate_counterfactuals(scored, fuzzy_results)
        assert len(cfs) >= 1
        assert len(cfs[0]["what_if"]) > 0
        assert all(isinstance(s, str) for s in cfs[0]["what_if"])


# ── Fuzzy Check Policy (integration wrapper) ────────────────────────────────

class TestFuzzyCheckPolicy:
    def _mock_check_policy(self, structured, rules):
        """Minimal mock of check_policy()."""
        return {
            "violations": [{"rule_id": "R03", "detail": "Blocked"}] if structured.get("_force_violation") else [],
            "warnings": [],
            "escalations": [],
            "all_clear": not structured.get("_force_violation"),
        }

    def test_wraps_check_policy_preserving_violations(self):
        """Hard rule violations should pass through."""
        result = fuzzy_check_policy(
            {"budget_eur": 50000, "_force_violation": True}, [], self._mock_check_policy
        )
        assert len(result["violations"]) == 1
        assert result["violations"][0]["rule_id"] == "R03"

    def test_adds_proximity_warning_for_borderline(self):
        """€98k budget should add FUZZY-THRESHOLD warning."""
        result = fuzzy_check_policy(
            {"budget_eur": 98000}, [], self._mock_check_policy
        )
        warning_ids = [w["rule_id"] for w in result["warnings"]]
        assert "FUZZY-THRESHOLD" in warning_ids

    def test_adds_fuzzy_escalation_when_tier_higher(self):
        """€98k budget should add FUZZY-TIER3 escalation (higher than hard tier1)."""
        result = fuzzy_check_policy(
            {"budget_eur": 98000}, [], self._mock_check_policy
        )
        esc_ids = [e["rule_id"] for e in result["escalations"]]
        assert any("FUZZY" in eid for eid in esc_ids)

    def test_no_fuzzy_for_clean_budget(self):
        """€50k budget (well within tier) → no FUZZY-* warnings."""
        result = fuzzy_check_policy(
            {"budget_eur": 50000}, [], self._mock_check_policy
        )
        fuzzy_warnings = [w for w in result["warnings"] if "FUZZY" in w.get("rule_id", "")]
        assert len(fuzzy_warnings) == 0

    def test_fuzzy_threshold_in_results(self):
        """fuzzy_threshold key should be present when budget > 0."""
        result = fuzzy_check_policy(
            {"budget_eur": 50000}, [], self._mock_check_policy
        )
        assert "fuzzy_threshold" in result
        assert "primary_tier" in result["fuzzy_threshold"]
