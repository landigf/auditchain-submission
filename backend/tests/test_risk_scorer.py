"""
Tests for compute_risk_score() — both linear and fuzzy approaches.
Covers: input ratio calculations, boundary values, output structure,
        fuzzy fallback, edge cases.
"""
import pytest
from agent.risk_scorer import _linear_risk, _fuzzy_risk, compute_risk_score


def _req(**kw):
    base = {"budget_eur": 50000, "deadline_days": 14, "_preferred_tier": "approved"}
    base.update(kw)
    return base


def _ctx(**kw):
    base = {"spending_authority_eur": 100000}
    base.update(kw)
    return base


# ── Linear Risk ───────────────────────────────────────────────────────────────

class TestLinearRisk:
    def test_zero_risk_scenario(self):
        result = _linear_risk(
            _req(budget_eur=0, deadline_days=30, _preferred_tier="preferred"),
            _ctx(spending_authority_eur=1000000000),
        )
        assert result["score"] <= 5

    def test_max_risk_scenario(self):
        result = _linear_risk(
            _req(budget_eur=200000, deadline_days=0, _preferred_tier="spot"),
            _ctx(spending_authority_eur=50000),
        )
        assert result["score"] >= 70

    def test_budget_ratio_capped(self):
        result = _linear_risk(_req(budget_eur=200000), _ctx())
        assert result["breakdown"]["budget_ratio"] <= 1.0

    def test_urgency_zero_deadline(self):
        """deadline_days=0 is falsy → defaults to 30 → urgency=0.0."""
        result = _linear_risk(_req(deadline_days=0), _ctx())
        assert result["breakdown"]["urgency"] == 0.0

    def test_urgency_30_days(self):
        result = _linear_risk(_req(deadline_days=30), _ctx())
        assert result["breakdown"]["urgency"] == 0.0

    def test_urgency_60_days_clamped(self):
        result = _linear_risk(_req(deadline_days=60), _ctx())
        assert result["breakdown"]["urgency"] == 0.0

    def test_vendor_preferred_01(self):
        result = _linear_risk(_req(_preferred_tier="preferred"), _ctx())
        assert result["breakdown"]["vendor_risk"] == 0.1

    def test_vendor_approved_04(self):
        result = _linear_risk(_req(_preferred_tier="approved"), _ctx())
        assert result["breakdown"]["vendor_risk"] == 0.4

    def test_vendor_spot_08(self):
        result = _linear_risk(_req(_preferred_tier="spot"), _ctx())
        assert result["breakdown"]["vendor_risk"] == 0.8

    def test_vendor_unknown_05(self):
        result = _linear_risk(_req(_preferred_tier="custom"), _ctx())
        assert result["breakdown"]["vendor_risk"] == 0.5

    def test_approach_label(self):
        result = _linear_risk(_req(), _ctx())
        assert result["approach"] == "linear"

    def test_has_breakdown_keys(self):
        result = _linear_risk(_req(), _ctx())
        keys = set(result["breakdown"].keys())
        assert keys == {"budget_ratio", "authority_ratio", "urgency", "vendor_risk"}

    def test_score_always_0_to_100(self):
        result = _linear_risk(_req(), _ctx())
        assert 0 <= result["score"] <= 100

    def test_rules_fired_empty_for_linear(self):
        result = _linear_risk(_req(), _ctx())
        assert result["rules_fired"] == []

    @pytest.mark.parametrize("budget,deadline,tier,auth,min_score,max_score", [
        (0, 30, "preferred", 1e9, 0, 5),
        (200000, 0, "spot", 50000, 70, 100),
        (50000, 15, "approved", 100000, 15, 50),
        (25000, 7, "preferred", 50000, 10, 40),
    ])
    def test_linear_risk_ranges(self, budget, deadline, tier, auth, min_score, max_score):
        result = _linear_risk(
            _req(budget_eur=budget, deadline_days=deadline, _preferred_tier=tier),
            _ctx(spending_authority_eur=auth),
        )
        assert min_score <= result["score"] <= max_score


# ── Fuzzy Risk ────────────────────────────────────────────────────────────────

class TestFuzzyRisk:
    def test_fuzzy_has_memberships(self):
        result = _fuzzy_risk(_req(), _ctx())
        assert "memberships" in result

    def test_fuzzy_has_rules_fired(self):
        result = _fuzzy_risk(_req(), _ctx())
        assert "rules_fired" in result
        assert isinstance(result["rules_fired"], list)

    def test_fuzzy_approach_label(self):
        result = _fuzzy_risk(_req(), _ctx())
        assert result["approach"] == "fuzzy"

    def test_fuzzy_score_0_to_100(self):
        result = _fuzzy_risk(_req(), _ctx())
        assert 0 <= result["score"] <= 100

    def test_fuzzy_high_risk(self):
        result = _fuzzy_risk(
            _req(budget_eur=200000, deadline_days=1, _preferred_tier="spot"),
            _ctx(spending_authority_eur=50000),
        )
        assert result["score"] > 50

    def test_fuzzy_low_risk(self):
        result = _fuzzy_risk(
            _req(budget_eur=1000, deadline_days=30, _preferred_tier="preferred"),
            _ctx(spending_authority_eur=1000000),
        )
        assert result["score"] < 30

    def test_fuzzy_fallback_on_import_error(self, monkeypatch):
        """If scikit-fuzzy not available, falls back to linear."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "skfuzzy" in name:
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = _fuzzy_risk(_req(), _ctx())
        assert "fuzzy_error" in result
        assert result["approach"] == "linear"


# ── Edge Cases ────────────────────────────────────────────────────────────────

class TestRiskEdgeCases:
    def test_budget_none_treated_as_0(self):
        result = _linear_risk(_req(budget_eur=None), _ctx())
        assert result["score"] >= 0
        assert result["breakdown"]["budget_ratio"] == 0.0

    def test_deadline_none_defaults_30(self):
        result = _linear_risk(_req(deadline_days=None), _ctx())
        assert result["breakdown"]["urgency"] == 0.0

    def test_authority_none_defaults_1e9(self):
        result = _linear_risk(_req(), {"spending_authority_eur": None})
        assert result["breakdown"]["authority_ratio"] == 0.0

    def test_compute_risk_score_uses_linear_by_default(self, monkeypatch):
        monkeypatch.setattr("agent.risk_scorer.USE_FUZZY", False)
        result = compute_risk_score(_req(), _ctx())
        assert result["approach"] == "linear"

    def test_compute_risk_score_uses_fuzzy_when_flag_set(self, monkeypatch):
        monkeypatch.setattr("agent.risk_scorer.USE_FUZZY", True)
        result = compute_risk_score(_req(), _ctx())
        assert result["approach"] == "fuzzy"
