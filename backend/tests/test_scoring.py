"""
Tests for score_suppliers(), _get_volume_price(), _historical_bonus().
Covers: category weights, normalization, volume pricing, historical bonus,
        contract penalties, ranking, output structure.
"""
import pytest
from agent.tools import score_suppliers, _get_volume_price, _historical_bonus, CATEGORY_WEIGHTS
from tests.conftest import _make_request


def _candidate(id="S1", name="Test", price=500.0, days=14, esg=75,
               tier="preferred", contract="active", **kw):
    base = {
        "id": id, "supplier_id": id, "name": name,
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


# ── Category Weights ──────────────────────────────────────────────────────────

class TestCategoryWeights:
    @pytest.mark.parametrize("cat", ["hardware", "software", "services", "facilities", "default"])
    def test_weights_sum_to_1(self, cat):
        w = CATEGORY_WEIGHTS[cat]
        assert abs(sum(w.values()) - 1.0) < 0.001

    def test_hardware_price_weight_040(self):
        assert CATEGORY_WEIGHTS["hardware"]["price"] == 0.40

    def test_software_compliance_weight_035(self):
        assert CATEGORY_WEIGHTS["software"]["compliance"] == 0.35


# ── Price/Delivery Normalization ──────────────────────────────────────────────

class TestNormalization:
    def test_cheapest_gets_price_100(self):
        candidates = [
            _candidate(id="S-CHEAP", price=100),
            _candidate(id="S-EXP", price=200),
        ]
        req = _make_request()
        result = score_suppliers(candidates, req)
        cheap = [s for s in result["scored"] if s["id"] == "S-CHEAP"][0]
        assert cheap["score_breakdown"]["price_score"] == 100.0

    def test_most_expensive_gets_price_0(self):
        candidates = [
            _candidate(id="S-CHEAP", price=100),
            _candidate(id="S-EXP", price=200),
        ]
        req = _make_request()
        result = score_suppliers(candidates, req)
        exp = [s for s in result["scored"] if s["id"] == "S-EXP"][0]
        assert exp["score_breakdown"]["price_score"] == 0.0

    def test_single_supplier_price_100(self):
        candidates = [_candidate(id="S-ONLY", price=500)]
        req = _make_request()
        result = score_suppliers(candidates, req)
        assert result["scored"][0]["score_breakdown"]["price_score"] == 100.0

    def test_fastest_gets_delivery_100(self):
        candidates = [
            _candidate(id="S-FAST", days=5),
            _candidate(id="S-SLOW", days=30),
        ]
        req = _make_request()
        result = score_suppliers(candidates, req)
        fast = [s for s in result["scored"] if s["id"] == "S-FAST"][0]
        assert fast["score_breakdown"]["delivery_score"] == 100.0

    def test_slowest_gets_delivery_0(self):
        candidates = [
            _candidate(id="S-FAST", days=5),
            _candidate(id="S-SLOW", days=30),
        ]
        req = _make_request()
        result = score_suppliers(candidates, req)
        slow = [s for s in result["scored"] if s["id"] == "S-SLOW"][0]
        assert slow["score_breakdown"]["delivery_score"] == 0.0


# ── Compliance + Contract ─────────────────────────────────────────────────────

class TestComplianceScoring:
    def test_preferred_tier_100(self):
        candidates = [_candidate(tier="preferred")]
        result = score_suppliers(candidates, _make_request())
        assert result["scored"][0]["score_breakdown"]["compliance_score"] == 100.0

    def test_approved_tier_70(self):
        candidates = [_candidate(tier="approved")]
        result = score_suppliers(candidates, _make_request())
        assert result["scored"][0]["score_breakdown"]["compliance_score"] == 70.0

    def test_spot_tier_30(self):
        candidates = [_candidate(tier="spot", contract="active")]
        result = score_suppliers(candidates, _make_request())
        assert result["scored"][0]["score_breakdown"]["compliance_score"] == 30.0

    def test_expired_contract_penalty(self):
        candidates = [_candidate(tier="preferred", contract="expired")]
        result = score_suppliers(candidates, _make_request())
        # preferred = 1.0 × 0.85 = 0.85 → 85.0
        assert result["scored"][0]["score_breakdown"]["compliance_score"] == 85.0

    def test_no_contract_penalty(self):
        candidates = [_candidate(tier="preferred", contract="none")]
        result = score_suppliers(candidates, _make_request())
        # preferred = 1.0 × 0.75 = 0.75 → 75.0
        assert result["scored"][0]["score_breakdown"]["compliance_score"] == 75.0


# ── Volume Pricing ────────────────────────────────────────────────────────────

class TestVolumePricing:
    def test_quantity_in_tier_range(self, make_pricing_tier, db_session):
        _make_pricing_tier = make_pricing_tier
        _make_pricing_tier("S1", 1, 99, 10.0)
        _make_pricing_tier("S1", 100, 499, 8.0)
        _make_pricing_tier("S1", 500, None, 6.0)
        price = _get_volume_price("S1", 150, db_session)
        assert price == 8.0

    def test_quantity_below_all_tiers(self, make_pricing_tier, db_session):
        make_pricing_tier("S2", 100, 499, 8.0)
        make_pricing_tier("S2", 500, None, 6.0)
        price = _get_volume_price("S2", 5, db_session)
        assert price == 8.0  # fallback to lowest tier

    def test_no_tiers_returns_none(self, db_session):
        price = _get_volume_price("S-NO-TIER", 100, db_session)
        assert price is None

    def test_boundary_at_min_quantity(self, make_pricing_tier, db_session):
        make_pricing_tier("S3", 100, 499, 8.0)
        price = _get_volume_price("S3", 100, db_session)
        assert price == 8.0

    def test_boundary_at_max_quantity(self, make_pricing_tier, db_session):
        make_pricing_tier("S4", 100, 499, 8.0)
        price = _get_volume_price("S4", 499, db_session)
        assert price == 8.0


# ── Historical Bonus ──────────────────────────────────────────────────────────

class TestHistoricalBonus:
    def test_no_awards_zero(self, db_session):
        bonus, note = _historical_bonus("S-NONE", "hardware", db_session)
        assert bonus == 0.0
        assert "No historical" in note

    def test_all_completed_max(self, make_award, db_session):
        for _ in range(10):
            make_award("S-PERFECT", "hardware", "completed")
        bonus, _ = _historical_bonus("S-PERFECT", "hardware", db_session)
        assert bonus == 10.0

    def test_half_completed_scaled(self, make_award, db_session):
        for _ in range(5):
            make_award("S-HALF", "hardware", "completed")
        for _ in range(5):
            make_award("S-HALF", "hardware", "cancelled")
        bonus, _ = _historical_bonus("S-HALF", "hardware", db_session)
        assert bonus == 5.0  # 0.5 × 1.0 × 10

    def test_all_cancelled_zero(self, make_award, db_session):
        for _ in range(5):
            make_award("S-BAD", "hardware", "cancelled")
        bonus, _ = _historical_bonus("S-BAD", "hardware", db_session)
        assert bonus == 0.0


# ── Output Structure ──────────────────────────────────────────────────────────

class TestScoringOutput:
    def test_sorted_descending(self):
        candidates = [
            _candidate(id="S-A", price=900, esg=60),
            _candidate(id="S-B", price=100, esg=90),
        ]
        result = score_suppliers(candidates, _make_request())
        scores = [s["score"] for s in result["scored"]]
        assert scores == sorted(scores, reverse=True)

    def test_ranks_assigned(self):
        candidates = [
            _candidate(id="S-A", price=900),
            _candidate(id="S-B", price=100),
        ]
        result = score_suppliers(candidates, _make_request())
        ranks = [s["rank"] for s in result["scored"]]
        assert ranks == [1, 2]

    def test_total_cost_calculated(self):
        candidates = [_candidate(id="S-TC", price=500)]
        req = _make_request(quantity=10)
        result = score_suppliers(candidates, req)
        assert result["scored"][0]["total_cost_eur"] == 5000.0

    def test_within_budget_true(self):
        candidates = [_candidate(id="S-WB", price=100)]
        req = _make_request(quantity=10, budget_eur=5000)
        req["_budget"] = 5000  # score_suppliers reads _budget (set by check_policy)
        result = score_suppliers(candidates, req)
        assert result["scored"][0]["within_budget"] is True

    def test_within_budget_false(self):
        candidates = [_candidate(id="S-OB", price=1000)]
        req = _make_request(quantity=10, budget_eur=5000)
        req["_budget"] = 5000  # score_suppliers reads _budget (set by check_policy)
        result = score_suppliers(candidates, req)
        assert result["scored"][0]["within_budget"] is False

    def test_empty_candidates(self):
        result = score_suppliers([], _make_request())
        assert result["scored"] == []
        assert result["scoring_warnings"] == []

    def test_deterministic_ordering(self):
        candidates = [
            _candidate(id="S-A", price=500, days=10, esg=80),
            _candidate(id="S-B", price=600, days=20, esg=70),
            _candidate(id="S-C", price=400, days=15, esg=85),
        ]
        req = _make_request()
        r1 = score_suppliers(list(candidates), req)
        r2 = score_suppliers(list(candidates), req)
        ids1 = [s["id"] for s in r1["scored"]]
        ids2 = [s["id"] for s in r2["scored"]]
        assert ids1 == ids2
