"""
Tests for check_policy() — the deterministic policy engine.
Covers: budget thresholds (AT-001..AT-005), emergency timeline (R05),
        spending authority (AT-AUTHORITY), missing fields (ER-001),
        preferred supplier checks (W-CAT, W-GEO, R03).
"""
import pytest
from agent.tools import check_policy
from tests.conftest import _make_request


def _rule_ids(result_list):
    return [r["rule_id"] for r in result_list]


# ── Budget Thresholds ─────────────────────────────────────────────────────────

class TestBudgetThresholds:
    """AT-001..AT-005: hardcoded EUR checks in check_policy lines 140-173."""

    def test_budget_below_25k_no_threshold(self, seed_rules, db_session):
        req = _make_request(budget_eur=24999)
        result = check_policy(req, list(seed_rules.values()))
        at_rules = [r for r in result["warnings"] + result["escalations"]
                    if r["rule_id"].startswith("AT-0")]
        assert len(at_rules) == 0

    def test_budget_25k_at002_warning(self, seed_rules, db_session):
        req = _make_request(budget_eur=25000)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-002" in _rule_ids(result["warnings"])
        assert "AT-002" not in _rule_ids(result["escalations"])

    def test_budget_99999_at002_only(self, seed_rules, db_session):
        req = _make_request(budget_eur=99999.99)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-002" in _rule_ids(result["warnings"])
        assert "AT-003" not in _rule_ids(result["escalations"])

    def test_budget_100k_at003_escalation(self, seed_rules, db_session):
        req = _make_request(budget_eur=100000)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-003" in _rule_ids(result["escalations"])

    def test_budget_499999_at003(self, seed_rules, db_session):
        req = _make_request(budget_eur=499999.99)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-003" in _rule_ids(result["escalations"])
        assert "AT-004" not in _rule_ids(result["escalations"])

    def test_budget_500k_at004(self, seed_rules, db_session):
        req = _make_request(budget_eur=500000)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-004" in _rule_ids(result["escalations"])

    def test_budget_4999999_at004(self, seed_rules, db_session):
        req = _make_request(budget_eur=4999999.99)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-004" in _rule_ids(result["escalations"])
        assert "AT-005" not in _rule_ids(result["escalations"])

    def test_budget_5m_at005(self, seed_rules, db_session):
        req = _make_request(budget_eur=5000000)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-005" in _rule_ids(result["escalations"])

    def test_budget_zero_no_threshold(self, seed_rules, db_session):
        req = _make_request(budget_eur=0)
        result = check_policy(req, list(seed_rules.values()))
        at_rules = [r for r in result["warnings"] + result["escalations"]
                    if r["rule_id"].startswith("AT-0")]
        assert len(at_rules) == 0

    def test_budget_negative_no_threshold(self, seed_rules, db_session):
        req = _make_request(budget_eur=-1)
        result = check_policy(req, list(seed_rules.values()))
        at_rules = [r for r in result["warnings"] + result["escalations"]
                    if r["rule_id"].startswith("AT-0")]
        assert len(at_rules) == 0

    @pytest.mark.parametrize("budget,expected_rule,expected_list", [
        (24999, None, None),
        (25000, "AT-002", "warnings"),
        (100000, "AT-003", "escalations"),
        (500000, "AT-004", "escalations"),
        (5000000, "AT-005", "escalations"),
    ])
    def test_budget_threshold_parametrized(self, budget, expected_rule, expected_list,
                                           seed_rules, db_session):
        req = _make_request(budget_eur=budget)
        result = check_policy(req, list(seed_rules.values()))
        if expected_rule is None:
            at_rules = [r for r in result["warnings"] + result["escalations"]
                        if r["rule_id"].startswith("AT-0")]
            assert len(at_rules) == 0
        else:
            assert expected_rule in _rule_ids(result[expected_list])


# ── Emergency Timeline ────────────────────────────────────────────────────────

class TestEmergencyTimeline:
    """R05: deadline_days condition is `0 < deadline_days < 3`."""

    def test_deadline_1_day_r05(self, seed_rules, db_session):
        req = _make_request(deadline_days=1)
        result = check_policy(req, list(seed_rules.values()))
        assert "R05" in _rule_ids(result["escalations"])

    def test_deadline_2_days_r05(self, seed_rules, db_session):
        req = _make_request(deadline_days=2)
        result = check_policy(req, list(seed_rules.values()))
        assert "R05" in _rule_ids(result["escalations"])

    def test_deadline_3_days_no_r05(self, seed_rules, db_session):
        req = _make_request(deadline_days=3)
        result = check_policy(req, list(seed_rules.values()))
        assert "R05" not in _rule_ids(result["escalations"])

    def test_deadline_0_no_r05(self, seed_rules, db_session):
        req = _make_request(deadline_days=0)
        result = check_policy(req, list(seed_rules.values()))
        assert "R05" not in _rule_ids(result["escalations"])

    def test_deadline_none_defaults_no_r05(self, seed_rules, db_session):
        req = _make_request(deadline_days=None)
        result = check_policy(req, list(seed_rules.values()))
        assert "R05" not in _rule_ids(result["escalations"])

    def test_deadline_30_no_r05(self, seed_rules, db_session):
        req = _make_request(deadline_days=30)
        result = check_policy(req, list(seed_rules.values()))
        assert "R05" not in _rule_ids(result["escalations"])


# ── Spending Authority ────────────────────────────────────────────────────────

class TestSpendingAuthority:
    """AT-AUTHORITY: budget > _spending_authority_eur, only if not covered by AT-003+."""

    def test_authority_exceeded(self, seed_rules, db_session):
        req = _make_request(budget_eur=30000, _spending_authority_eur=25000)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-AUTHORITY" in _rule_ids(result["escalations"])

    def test_authority_exact_no_trigger(self, seed_rules, db_session):
        req = _make_request(budget_eur=25000, _spending_authority_eur=25000)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-AUTHORITY" not in _rule_ids(result["escalations"])

    def test_authority_below_no_trigger(self, seed_rules, db_session):
        req = _make_request(budget_eur=24000, _spending_authority_eur=25000)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-AUTHORITY" not in _rule_ids(result["escalations"])

    def test_authority_not_set_no_trigger(self, seed_rules, db_session):
        req = _make_request(budget_eur=50000)
        # No _spending_authority_eur key → defaults to inf
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-AUTHORITY" not in _rule_ids(result["escalations"])

    def test_authority_exceeded_by_1_cent(self, seed_rules, db_session):
        req = _make_request(budget_eur=25000.01, _spending_authority_eur=25000)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-AUTHORITY" in _rule_ids(result["escalations"])

    def test_authority_suppressed_by_at003(self, seed_rules, db_session):
        """AT-AUTHORITY not added if AT-003 already present (budget >= 100k)."""
        req = _make_request(budget_eur=150000, _spending_authority_eur=25000)
        result = check_policy(req, list(seed_rules.values()))
        assert "AT-003" in _rule_ids(result["escalations"])
        assert "AT-AUTHORITY" not in _rule_ids(result["escalations"])


# ── Missing Fields ────────────────────────────────────────────────────────────

class TestMissingFields:
    """ER-001: missing_fields + rule_map has ER-001."""

    def test_missing_fields_er001(self, seed_rules, db_session):
        req = _make_request(missing_fields=["budget_eur"])
        result = check_policy(req, list(seed_rules.values()))
        assert "ER-001" in _rule_ids(result["escalations"])

    def test_no_missing_fields_no_er001(self, seed_rules, db_session):
        req = _make_request(missing_fields=[])
        result = check_policy(req, list(seed_rules.values()))
        assert "ER-001" not in _rule_ids(result["escalations"])

    def test_er001_rule_absent_no_escalation(self, make_rule, db_session):
        """If ER-001 rule is not in the rules list, no escalation even with missing fields."""
        r05 = make_rule("R05", name="Emergency", action="escalate")
        req = _make_request(missing_fields=["budget_eur", "quantity"])
        result = check_policy(req, [r05])
        assert "ER-001" not in _rule_ids(result["escalations"])

    def test_multiple_missing_fields(self, seed_rules, db_session):
        req = _make_request(missing_fields=["budget_eur", "quantity", "deadline_days"])
        result = check_policy(req, list(seed_rules.values()))
        er001 = [e for e in result["escalations"] if e["rule_id"] == "ER-001"]
        assert len(er001) == 1
        assert "budget_eur" in er001[0]["detail"]
        assert "quantity" in er001[0]["detail"]


# ── Preferred Supplier Checks ────────────────────────────────────────────────

class TestPreferredSupplierChecks:
    """Category mismatch, geo mismatch, blocked status. Requires patch_sessionlocal."""

    def test_category_mismatch_warning(self, seed_rules, make_supplier,
                                       patch_sessionlocal, db_session):
        make_supplier(id="S-PREF-HW", name="Hardware Only Ltd", category="hardware",
                      service_regions="CH;DE;FR")
        req = _make_request(category="services", preferred_supplier_name="Hardware Only Ltd")
        result = check_policy(req, list(seed_rules.values()))
        assert "W-CAT-MISMATCH" in _rule_ids(result["warnings"])

    def test_geo_mismatch_warning(self, seed_rules, make_supplier,
                                  patch_sessionlocal, db_session):
        make_supplier(id="S-PREF-GEO", name="Germany Only GmbH", category="hardware",
                      service_regions="DE;FR")
        req = _make_request(delivery_country="CH", preferred_supplier_name="Germany Only GmbH")
        result = check_policy(req, list(seed_rules.values()))
        assert "W-GEO-MISMATCH" in _rule_ids(result["warnings"])

    def test_blocked_supplier_violation(self, seed_rules, make_supplier,
                                        patch_sessionlocal, db_session):
        make_supplier(id="S-PREF-BLOCKED", name="Blocked Corp", category="hardware",
                      compliance_status="blocked", service_regions="CH;DE;FR")
        req = _make_request(preferred_supplier_name="Blocked Corp")
        result = check_policy(req, list(seed_rules.values()))
        assert "R03" in _rule_ids(result["violations"])

    def test_no_preferred_supplier_no_checks(self, seed_rules, db_session):
        req = _make_request(preferred_supplier_name="")
        result = check_policy(req, list(seed_rules.values()))
        assert "W-CAT-MISMATCH" not in _rule_ids(result["warnings"])
        assert "W-GEO-MISMATCH" not in _rule_ids(result["warnings"])


# ── All Clear Flag ────────────────────────────────────────────────────────────

class TestAllClear:
    """all_clear is True only if no violations AND no escalations."""

    def test_all_clear_clean_request(self, seed_rules, db_session):
        req = _make_request(budget_eur=10000, deadline_days=14)
        result = check_policy(req, list(seed_rules.values()))
        assert result["all_clear"] is True

    def test_all_clear_false_with_escalation(self, seed_rules, db_session):
        req = _make_request(budget_eur=100000)
        result = check_policy(req, list(seed_rules.values()))
        assert result["all_clear"] is False

    def test_warnings_dont_affect_all_clear(self, seed_rules, db_session):
        req = _make_request(budget_eur=25000)
        result = check_policy(req, list(seed_rules.values()))
        # AT-002 is a warning, not an escalation
        assert "AT-002" in _rule_ids(result["warnings"])
        assert result["all_clear"] is True


# ── GDPR Sensitivity ──────────────────────────────────────────────────────────

class TestGDPRSensitivity:
    """gdpr_sensitive flag based on category in {"software", "services"}."""

    def test_software_is_gdpr_sensitive(self, seed_rules, db_session):
        req = _make_request(category="software")
        result = check_policy(req, list(seed_rules.values()))
        assert result["gdpr_sensitive"] is True

    def test_services_is_gdpr_sensitive(self, seed_rules, db_session):
        req = _make_request(category="services")
        result = check_policy(req, list(seed_rules.values()))
        assert result["gdpr_sensitive"] is True

    def test_hardware_not_gdpr_sensitive(self, seed_rules, db_session):
        req = _make_request(category="hardware")
        result = check_policy(req, list(seed_rules.values()))
        assert result["gdpr_sensitive"] is False

    def test_facilities_not_gdpr_sensitive(self, seed_rules, db_session):
        req = _make_request(category="facilities")
        result = check_policy(req, list(seed_rules.values()))
        assert result["gdpr_sensitive"] is False
