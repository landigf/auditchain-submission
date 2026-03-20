"""
Validate our pipeline against the ChainIQ example output (REQ-000004).
240 docking stations, €25,199.55, 6-day deadline, preferred Dell.
"""
import pytest
from agent.tools import check_policy, query_suppliers, score_suppliers, compute_ais
from agent.pipeline import make_decision
from tests.conftest import _make_request


@pytest.fixture
def req_000004():
    """Structured request matching REQ-000004 from example_request.json."""
    return _make_request(
        item_description="240 docking stations matching existing laptop fleet",
        category="hardware",
        category_l2="Docking Stations",
        quantity=240,
        budget_eur=25199.55,
        deadline_days=6,
        delivery_country="DE",
        preferred_supplier_name="Dell Enterprise Europe",
        ambiguities=[],
        missing_fields=[],
    )


class TestExampleREQ000004:
    """Validate against the example_output.json structure."""

    def test_budget_above_25k_triggers_at002(self, req_000004, seed_rules, db_session, patch_sessionlocal):
        """Budget €25,199.55 ≥ €25,000 → AT-002 warning."""
        result = check_policy(req_000004, list(seed_rules.values()))
        at002 = [r for r in result["warnings"] if r["rule_id"] == "AT-002"]
        assert len(at002) == 1

    def test_deadline_6_days_no_r05(self, req_000004, seed_rules, db_session, patch_sessionlocal):
        """Deadline = 6 days, R05 requires < 3 days → no R05."""
        result = check_policy(req_000004, list(seed_rules.values()))
        assert "R05" not in [r["rule_id"] for r in result["escalations"]]

    def test_infeasibility_detection(self, req_000004, make_supplier, db_session, seed_rules, patch_sessionlocal):
        """240 units × cheapest price should exceed €25,199.55 → infeasible."""
        make_supplier(id="S-BECHTLE", name="Bechtle Workplace Solutions",
                      category="hardware", category_l2="Docking Stations",
                      unit_price_eur=148.80, delivery_days=26, esg_score=72,
                      preferred_tier="preferred", service_regions="DE;CH;FR")
        make_supplier(id="S-DELL", name="Dell Enterprise Europe",
                      category="hardware", category_l2="Docking Stations",
                      unit_price_eur=155.00, delivery_days=22, esg_score=73,
                      preferred_tier="preferred", service_regions="DE;CH;FR")
        make_supplier(id="S-HP", name="HP Enterprise Devices",
                      category="hardware", category_l2="Docking Stations",
                      unit_price_eur=153.45, delivery_days=23, esg_score=66,
                      preferred_tier="preferred", service_regions="DE;CH;FR")

        check_policy(req_000004, list(seed_rules.values()))
        result = query_suppliers(req_000004, db_session)

        # Min cost: 240 × €148.80 = €35,712 > €25,199.55
        assert result["infeasibility"] is not None
        assert result["infeasibility"]["infeasible"] is True
        assert result["infeasibility"]["min_cost_eur"] > 25199.55

    def test_three_suppliers_ranked(self, req_000004, make_supplier, db_session, seed_rules, patch_sessionlocal):
        """All 3 docking station suppliers should be eligible and ranked."""
        make_supplier(id="S-BECHTLE", name="Bechtle", category="hardware",
                      category_l2="Docking Stations", unit_price_eur=148.80,
                      delivery_days=26, esg_score=72, preferred_tier="preferred",
                      service_regions="DE;CH;FR")
        make_supplier(id="S-DELL", name="Dell", category="hardware",
                      category_l2="Docking Stations", unit_price_eur=155.00,
                      delivery_days=22, esg_score=73, preferred_tier="preferred",
                      service_regions="DE;CH;FR")
        make_supplier(id="S-HP", name="HP", category="hardware",
                      category_l2="Docking Stations", unit_price_eur=153.45,
                      delivery_days=23, esg_score=66, preferred_tier="preferred",
                      service_regions="DE;CH;FR")

        check_policy(req_000004, list(seed_rules.values()))
        supplier_results = query_suppliers(req_000004, db_session)
        candidates = supplier_results["candidates"]

        assert len(candidates) == 3
        scoring = score_suppliers(candidates, req_000004)
        assert len(scoring["scored"]) == 3
        assert scoring["scored"][0]["rank"] == 1

    def test_decision_is_escalated(self, req_000004, make_supplier, db_session, seed_rules, patch_sessionlocal):
        """Infeasible budget → decision should be escalated."""
        make_supplier(id="S-B", name="Bechtle", category="hardware",
                      category_l2="Docking Stations", unit_price_eur=148.80,
                      delivery_days=26, esg_score=72, preferred_tier="preferred",
                      service_regions="DE;CH;FR")

        check_policy(req_000004, list(seed_rules.values()))
        supplier_results = query_suppliers(req_000004, db_session)
        scoring = score_suppliers(supplier_results["candidates"], req_000004)
        policy_results = check_policy(req_000004, list(seed_rules.values()))

        decision = make_decision(req_000004, policy_results, scoring, supplier_results)
        assert decision["decision_type"] == "escalated"

    def test_max_affordable_quantity(self, req_000004, make_supplier, db_session, seed_rules, patch_sessionlocal):
        """max_affordable_qty should be < 240."""
        make_supplier(id="S-B2", name="Bechtle", category="hardware",
                      category_l2="Docking Stations", unit_price_eur=148.80,
                      delivery_days=26, esg_score=72, preferred_tier="preferred",
                      service_regions="DE;CH;FR")

        check_policy(req_000004, list(seed_rules.values()))
        result = query_suppliers(req_000004, db_session)
        assert result["infeasibility"]["max_affordable_qty"] < 240
        # €25,199.55 / €148.80 ≈ 169
        assert result["infeasibility"]["max_affordable_qty"] == 169

    def test_ais_computed_for_escalated(self, req_000004, make_supplier, db_session, seed_rules, patch_sessionlocal):
        """AIS should still compute even for escalated decisions."""
        make_supplier(id="S-B3", name="Bechtle", category="hardware",
                      category_l2="Docking Stations", unit_price_eur=148.80,
                      delivery_days=26, esg_score=72, preferred_tier="preferred",
                      service_regions="DE;CH;FR")

        check_policy(req_000004, list(seed_rules.values()))
        supplier_results = query_suppliers(req_000004, db_session)
        scoring = score_suppliers(supplier_results["candidates"], req_000004)
        policy_results = check_policy(req_000004, list(seed_rules.values()))
        decision = make_decision(req_000004, policy_results, scoring, supplier_results)
        decision["reasoning_narrative"] = "Test narrative"

        ais = compute_ais(req_000004, policy_results, supplier_results, scoring, decision)
        assert isinstance(ais["score"], int)
        assert ais["score"] > 0
