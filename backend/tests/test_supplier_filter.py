"""
Tests for query_suppliers() — supplier filtering and disqualification.
Covers: R03 blocked, R06 ESG, R07 GDPR, R10 spot, MOQ, ER-005 residency,
        ER-006 capacity, geographic coverage, infeasibility detection.
"""
import pytest
from agent.tools import query_suppliers, check_policy
from tests.conftest import _make_request


# ── Disqualification Rules ────────────────────────────────────────────────────

class TestESGDisqualification:
    """R06: ESG score < 60 → disqualified."""

    def test_esg_below_60_disqualified(self, make_supplier, db_session):
        make_supplier(id="S-LOW", esg_score=55)
        req = _make_request()
        # Run check_policy first to set internal fields
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq_ids = [s["id"] for s in result["disqualified"]]
        assert "S-LOW" in dq_ids
        reasons = [s for s in result["disqualified"] if s["id"] == "S-LOW"][0]["disqualification_reasons"]
        assert any("R06" in r for r in reasons)

    def test_esg_exactly_60_passes(self, make_supplier, db_session):
        make_supplier(id="S-60", esg_score=60)
        req = _make_request()
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-60" in cand_ids

    def test_esg_59_disqualified(self, make_supplier, db_session):
        make_supplier(id="S-59", esg_score=59)
        req = _make_request()
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq_ids = [s["id"] for s in result["disqualified"]]
        assert "S-59" in dq_ids


class TestBlockedSupplier:
    def test_blocked_supplier_disqualified(self, make_supplier, db_session):
        make_supplier(id="S-BLK", compliance_status="blocked")
        req = _make_request()
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq_ids = [s["id"] for s in result["disqualified"]]
        assert "S-BLK" in dq_ids
        reasons = [s for s in result["disqualified"] if s["id"] == "S-BLK"][0]["disqualification_reasons"]
        assert any("R03" in r for r in reasons)


class TestGDPRFilter:
    """R07: Non-EU supplier for GDPR-sensitive categories (software/services)."""

    def test_non_eu_software_disqualified(self, make_supplier, db_session):
        make_supplier(id="S-NON-EU-SW", category="software", category_l2="Cloud Compute",
                      eu_based=False, country="US")
        req = _make_request(category="software", category_l2="Cloud Compute")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq_ids = [s["id"] for s in result["disqualified"]]
        assert "S-NON-EU-SW" in dq_ids

    def test_non_eu_hardware_passes(self, make_supplier, db_session):
        make_supplier(id="S-NON-EU-HW", eu_based=False, country="US",
                      service_regions="US;CH")
        req = _make_request(category="hardware")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-NON-EU-HW" in cand_ids

    def test_eu_software_passes(self, make_supplier, db_session):
        make_supplier(id="S-EU-SW", category="software", category_l2="Cloud Compute",
                      eu_based=True)
        req = _make_request(category="software", category_l2="Cloud Compute")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-EU-SW" in cand_ids


class TestSpotVendorLimit:
    """R10: Spot vendor for purchases > €50k."""

    def test_spot_above_50k_disqualified(self, make_supplier, db_session):
        make_supplier(id="S-SPOT-1", preferred_tier="spot", contract_status="none")
        req = _make_request(budget_eur=50001)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq_ids = [s["id"] for s in result["disqualified"]]
        assert "S-SPOT-1" in dq_ids

    def test_spot_at_50k_passes(self, make_supplier, db_session):
        make_supplier(id="S-SPOT-2", preferred_tier="spot", contract_status="none")
        req = _make_request(budget_eur=50000)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-SPOT-2" in cand_ids

    def test_spot_below_50k_passes(self, make_supplier, db_session):
        make_supplier(id="S-SPOT-3", preferred_tier="spot", contract_status="none")
        req = _make_request(budget_eur=10000)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-SPOT-3" in cand_ids


class TestMOQ:
    """Minimum order quantity exceeded → disqualified."""

    def test_below_moq_disqualified(self, make_supplier, db_session):
        make_supplier(id="S-MOQ", min_quantity=10)
        req = _make_request(quantity=5)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq_ids = [s["id"] for s in result["disqualified"]]
        assert "S-MOQ" in dq_ids

    def test_at_moq_passes(self, make_supplier, db_session):
        make_supplier(id="S-MOQ-OK", min_quantity=10)
        req = _make_request(quantity=10)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-MOQ-OK" in cand_ids


class TestCapacity:
    """ER-006: Quantity > supplier monthly capacity."""

    def test_capacity_exceeded_disqualified(self, make_supplier, db_session):
        make_supplier(id="S-CAP", capacity_per_month=100)
        req = _make_request(quantity=200)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq_ids = [s["id"] for s in result["disqualified"]]
        assert "S-CAP" in dq_ids

    def test_capacity_at_limit_passes(self, make_supplier, db_session):
        make_supplier(id="S-CAP-OK", capacity_per_month=100)
        req = _make_request(quantity=100)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-CAP-OK" in cand_ids


class TestDataResidency:
    """ER-005: Data residency required but not supported."""

    def test_residency_not_supported_disqualified(self, make_supplier, db_session):
        make_supplier(id="S-NO-RES", data_residency_supported=False)
        req = _make_request(data_residency_required=True)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq_ids = [s["id"] for s in result["disqualified"]]
        assert "S-NO-RES" in dq_ids

    def test_residency_supported_passes(self, make_supplier, db_session):
        make_supplier(id="S-YES-RES", data_residency_supported=True)
        req = _make_request(data_residency_required=True)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-YES-RES" in cand_ids


class TestGeographicCoverage:
    def test_geographic_mismatch_disqualified(self, make_supplier, db_session):
        make_supplier(id="S-GEO-BAD", service_regions="DE;FR")
        req = _make_request(delivery_country="CH")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        dq_ids = [s["id"] for s in result["disqualified"]]
        assert "S-GEO-BAD" in dq_ids

    def test_geographic_match_passes(self, make_supplier, db_session):
        make_supplier(id="S-GEO-OK", service_regions="CH;DE;FR")
        req = _make_request(delivery_country="CH")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-GEO-OK" in cand_ids

    def test_no_delivery_country_no_geo_filter(self, make_supplier, db_session):
        make_supplier(id="S-GEO-ANY", service_regions="DE;FR")
        req = _make_request(delivery_country="")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_ids = [s["id"] for s in result["candidates"]]
        assert "S-GEO-ANY" in cand_ids


# ── Category Filtering ────────────────────────────────────────────────────────

class TestCategoryFiltering:
    def test_filters_by_category(self, make_supplier, db_session):
        make_supplier(id="S-HW-1", category="hardware")
        make_supplier(id="S-HW-2", category="hardware")
        make_supplier(id="S-SW-1", category="software", category_l2="Cloud Compute")
        req = _make_request(category="hardware")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_cats = set(s["category"] for s in result["candidates"])
        assert cand_cats == {"hardware"}

    def test_filters_by_category_l2(self, make_supplier, db_session):
        make_supplier(id="S-LAPTOP", category="hardware", category_l2="Laptops")
        make_supplier(id="S-MONITOR", category="hardware", category_l2="Monitors")
        req = _make_request(category="hardware", category_l2="Laptops")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        cand_l2 = set(s["category_l2"] for s in result["candidates"])
        assert cand_l2 == {"Laptops"}

    def test_fallback_to_full_category(self, make_supplier, db_session):
        make_supplier(id="S-HW-ANY", category="hardware", category_l2="Monitors")
        req = _make_request(category="hardware", category_l2="Nonexistent")
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert len(result["candidates"]) >= 1


# ── Infeasibility Detection ───────────────────────────────────────────────────

class TestInfeasibility:
    def test_infeasible_budget_below_min_cost(self, make_supplier, db_session):
        make_supplier(id="S-INF", unit_price_eur=500.0)
        req = _make_request(budget_eur=1000, quantity=10)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert result["infeasibility"] is not None
        assert result["infeasibility"]["infeasible"] is True

    def test_feasible_budget_above_min_cost(self, make_supplier, db_session):
        make_supplier(id="S-FEAS", unit_price_eur=500.0)
        req = _make_request(budget_eur=10000, quantity=10)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert result["infeasibility"] is None

    def test_max_affordable_qty(self, make_supplier, db_session):
        make_supplier(id="S-AFF", unit_price_eur=500.0)
        req = _make_request(budget_eur=999, quantity=10)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert result["infeasibility"]["max_affordable_qty"] == 1

    def test_budget_exactly_at_min_cost_not_infeasible(self, make_supplier, db_session):
        make_supplier(id="S-EXACT", unit_price_eur=500.0)
        req = _make_request(budget_eur=5000, quantity=10)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert result["infeasibility"] is None

    def test_no_candidates_no_infeasibility(self, make_supplier, db_session):
        make_supplier(id="S-ALL-BLK", compliance_status="blocked")
        req = _make_request(budget_eur=100)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert len(result["candidates"]) == 0
        assert result["infeasibility"] is None

    def test_zero_budget_no_infeasibility(self, make_supplier, db_session):
        make_supplier(id="S-ZERO-BDG", unit_price_eur=500.0)
        req = _make_request(budget_eur=0, quantity=10)
        check_policy(req, [])
        result = query_suppliers(req, db_session)
        assert result["infeasibility"] is None
