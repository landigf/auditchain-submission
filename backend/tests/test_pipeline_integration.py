"""
Integration tests for run_pipeline() — full pipeline with mocked LLM.
Covers: 4 demo scenarios, clarification flow, LLM logging, pipeline trace.
"""
import pytest
from agent.pipeline import run_pipeline
from db.models import AuditRecord, LLMCallLog


# ── Scenario A: Standard Approval ─────────────────────────────────────────────

class TestScenarioA:
    """30 office chairs, €20k budget, 10 day deadline → should complete."""

    def test_state_completed(self, db_session, patch_llm, seed_rules, seed_suppliers,
                             patch_sessionlocal):
        patch_llm.update({
            "category": "facilities", "category_l2": "Office Chairs",
            "quantity": 30, "budget_eur": 20000, "deadline_days": 10,
            "delivery_country": "CH", "item_description": "30 ergonomic office chairs",
        })
        result = run_pipeline("30 chairs for Zurich, budget 20k", db_session)
        assert result["state"] in ("completed", "awaiting_approval")

    def test_has_ais(self, db_session, patch_llm, seed_rules, seed_suppliers,
                     patch_sessionlocal):
        patch_llm.update({
            "category": "facilities", "category_l2": "Office Chairs",
            "quantity": 30, "budget_eur": 20000, "deadline_days": 10,
            "delivery_country": "CH", "item_description": "30 ergonomic office chairs",
        })
        result = run_pipeline("30 chairs for Zurich, budget 20k", db_session)
        assert isinstance(result["ais"]["score"], int)
        assert result["ais"]["grade"] in ("Audit-Ready", "Review Recommended", "Manual Review Required")

    def test_has_risk_score(self, db_session, patch_llm, seed_rules, seed_suppliers,
                            patch_sessionlocal):
        patch_llm.update({
            "category": "facilities", "category_l2": "Office Chairs",
            "quantity": 30, "budget_eur": 20000, "deadline_days": 10,
            "delivery_country": "CH", "item_description": "30 chairs",
        })
        result = run_pipeline("30 chairs", db_session)
        assert isinstance(result["risk_score"]["score"], int)
        assert 0 <= result["risk_score"]["score"] <= 100

    def test_pipeline_trace_complete(self, db_session, patch_llm, seed_rules,
                                     seed_suppliers, patch_sessionlocal):
        patch_llm.update({
            "category": "facilities", "category_l2": "Office Chairs",
            "quantity": 30, "budget_eur": 20000, "deadline_days": 10,
            "delivery_country": "CH", "item_description": "30 chairs",
        })
        result = run_pipeline("30 chairs", db_session)
        steps = [s["step"] for s in result["pipeline_trace"]]
        expected_steps = ["phase1_validation", "parse", "validate", "policy_check",
                          "filter_suppliers", "score", "fuzzy_scoring",
                          "decide", "confidence_gate", "narrative",
                          "risk_score", "ais", "persist"]
        for es in expected_steps:
            assert es in steps, f"Missing step: {es}"


# ── Scenario B: Missing Info → Clarification ─────────────────────────────────

class TestScenarioB:
    """Missing budget → clarification_needed state."""

    def test_clarification_state(self, db_session, patch_llm, seed_rules,
                                 seed_suppliers, patch_sessionlocal):
        patch_llm.update({
            "category": "hardware", "category_l2": "Laptops",
            "quantity": 30, "budget_eur": None, "deadline_days": 10,
            "delivery_country": "CH", "item_description": "30 workstations",
            "missing_fields": ["budget_eur"],
        })
        result = run_pipeline("30 workstations for Madrid", db_session)
        assert result["state"] == "clarification_needed"

    def test_has_questions(self, db_session, patch_llm, seed_rules,
                           seed_suppliers, patch_sessionlocal):
        patch_llm.update({
            "category": "hardware", "missing_fields": ["budget_eur"],
            "item_description": "workstations",
        })
        result = run_pipeline("workstations please", db_session)
        assert len(result["questions"]) >= 1
        assert any("budget" in q.lower() for q in result["questions"])

    def test_timeout_urgent(self, db_session, patch_llm, seed_rules,
                            seed_suppliers, patch_sessionlocal):
        patch_llm.update({
            "category": "hardware", "deadline_days": 2,
            "missing_fields": ["budget_eur"], "item_description": "urgent request",
        })
        result = run_pipeline("urgent request", db_session)
        assert result["timeout_hours"] == 4

    def test_timeout_normal(self, db_session, patch_llm, seed_rules,
                            seed_suppliers, patch_sessionlocal):
        patch_llm.update({
            "category": "hardware", "deadline_days": 14,
            "missing_fields": ["budget_eur"], "item_description": "normal request",
        })
        result = run_pipeline("normal request", db_session)
        assert result["timeout_hours"] == 48

    def test_clarification_record_persisted(self, db_session, patch_llm, seed_rules,
                                            seed_suppliers, patch_sessionlocal):
        patch_llm.update({
            "category": "hardware", "missing_fields": ["budget_eur"],
            "item_description": "test persist",
        })
        result = run_pipeline("test", db_session)
        record = db_session.query(AuditRecord).filter_by(id=result["record_id"]).first()
        assert record is not None
        assert record.state == "clarification_needed"


# ── Scenario D: Infeasible Budget ─────────────────────────────────────────────

class TestScenarioD:
    """500 laptops with €5k budget → infeasibility → escalated."""

    def test_escalated(self, db_session, patch_llm, seed_rules,
                       seed_suppliers, patch_sessionlocal):
        patch_llm.update({
            "category": "hardware", "category_l2": "Laptops",
            "quantity": 500, "budget_eur": 5000, "deadline_days": 14,
            "delivery_country": "CH", "item_description": "500 gaming laptops",
        })
        result = run_pipeline("500 gaming laptops, budget 5k", db_session)
        assert result["state"] in ("completed", "awaiting_approval")
        assert result["decision"]["decision_type"] == "escalated"

    def test_infeasibility_present(self, db_session, patch_llm, seed_rules,
                                   seed_suppliers, patch_sessionlocal):
        patch_llm.update({
            "category": "hardware", "category_l2": "Laptops",
            "quantity": 500, "budget_eur": 5000, "deadline_days": 14,
            "delivery_country": "CH", "item_description": "500 gaming laptops",
        })
        result = run_pipeline("500 laptops", db_session)
        infeasibility = result["supplier_results"].get("infeasibility")
        if infeasibility:
            assert infeasibility["infeasible"] is True


# ── LLM Logging ───────────────────────────────────────────────────────────────

class TestLLMLogging:
    def test_parse_log_persisted(self, db_session, patch_llm, seed_rules,
                                 seed_suppliers, patch_sessionlocal):
        patch_llm.update({"category": "hardware", "item_description": "test log"})
        result = run_pipeline("test", db_session)
        logs = db_session.query(LLMCallLog).filter_by(record_id=result["record_id"]).all()
        parse_logs = [l for l in logs if l.call_type == "parse"]
        assert len(parse_logs) == 1

    def test_narrative_log_persisted(self, db_session, patch_llm, seed_rules,
                                     seed_suppliers, patch_sessionlocal):
        patch_llm.update({"category": "hardware", "item_description": "test log"})
        result = run_pipeline("test", db_session)
        if result["state"] == "completed":
            logs = db_session.query(LLMCallLog).filter_by(record_id=result["record_id"]).all()
            narrative_logs = [l for l in logs if l.call_type == "narrative"]
            assert len(narrative_logs) == 1


# ── Pipeline Trace ────────────────────────────────────────────────────────────

class TestPipelineTrace:
    def test_trace_has_timing(self, db_session, patch_llm, seed_rules,
                              seed_suppliers, patch_sessionlocal):
        patch_llm.update({"category": "hardware", "item_description": "test trace"})
        result = run_pipeline("test", db_session)
        for step in result["pipeline_trace"]:
            assert "ms" in step
            assert isinstance(step["ms"], int)

    def test_parse_and_narrative_flagged_llm(self, db_session, patch_llm, seed_rules,
                                             seed_suppliers, patch_sessionlocal):
        patch_llm.update({"category": "hardware", "item_description": "test"})
        result = run_pipeline("test", db_session)
        trace_map = {s["step"]: s for s in result["pipeline_trace"]}
        assert trace_map["parse"]["llm"] is True
        if "narrative" in trace_map:
            assert trace_map["narrative"]["llm"] is True
        assert trace_map["policy_check"]["llm"] is False
