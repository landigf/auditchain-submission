"""
Tests for FastAPI API endpoints.
Uses TestClient with mocked DB and LLM.
"""
import json
import pytest
from fastapi.testclient import TestClient
from db.database import get_db
from db.models import Base, AuditRecord
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def api_client(db_engine, db_session, patch_llm, seed_rules, seed_suppliers, patch_sessionlocal, monkeypatch):
    """TestClient with overridden DB dependency and no-op lifespan."""
    # Disable lifespan's init_db/seed/load_all to avoid re-initialization & thread errors
    monkeypatch.setattr("main.init_db", lambda: None)
    monkeypatch.setattr("main.seed", lambda: None)
    monkeypatch.setattr("db.loaders.load_all", lambda: None)

    # Override the engine and SessionLocal in db.database to use our test engine
    # This ensures ALL sessions (including those created by run_pipeline) use the test DB
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr("db.database.engine", db_engine)
    monkeypatch.setattr("db.database.SessionLocal", TestSessionLocal)

    from main import app

    def override_get_db():
        s = TestSessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db

    # Pre-populate seed data via the shared engine (seed_rules and seed_suppliers already inserted via db_session)
    db_session.commit()  # ensure seed data is visible to new sessions

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client

    app.dependency_overrides.clear()


def _submit(client, text="Test request for 10 laptops", context=None):
    body = {"request_text": text}
    if context:
        body["requester_context"] = context
    return client.post("/api/submit", json=body)


# ── Submit Endpoint ───────────────────────────────────────────────────────────

class TestSubmitEndpoint:
    def test_submit_valid_200(self, api_client, patch_llm):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        resp = _submit(api_client)
        assert resp.status_code == 200
        assert "record_id" in resp.json()

    def test_submit_empty_text_400(self, api_client):
        resp = _submit(api_client, text="")
        assert resp.status_code == 400

    def test_submit_whitespace_only_400(self, api_client):
        resp = _submit(api_client, text="   ")
        assert resp.status_code == 400

    def test_submit_over_5000_chars_400(self, api_client):
        resp = _submit(api_client, text="x" * 5001)
        assert resp.status_code == 400

    def test_submit_returns_state(self, api_client, patch_llm):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        resp = _submit(api_client)
        assert resp.json()["state"] in ("completed", "clarification_needed", "awaiting_approval")

    def test_submit_with_requester_context(self, api_client, patch_llm):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        resp = _submit(api_client, context={"company": "UBS", "spending_authority_eur": 25000})
        assert resp.status_code == 200


# ── Decision Endpoint ─────────────────────────────────────────────────────────

class TestDecisionEndpoint:
    def test_get_decision_200(self, api_client, patch_llm, db_session):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        submit_resp = _submit(api_client)
        record_id = submit_resp.json()["record_id"]
        resp = api_client.get(f"/api/decision/{record_id}")
        assert resp.status_code == 200

    def test_get_decision_404(self, api_client):
        resp = api_client.get("/api/decision/nonexistent-id")
        assert resp.status_code == 404


# ── Status Endpoint ───────────────────────────────────────────────────────────

class TestStatusEndpoint:
    def test_status_200(self, api_client, patch_llm, db_session):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        submit_resp = _submit(api_client)
        record_id = submit_resp.json()["record_id"]
        resp = api_client.get(f"/api/decision/{record_id}/status")
        assert resp.status_code == 200
        assert "state" in resp.json()

    def test_status_404(self, api_client):
        resp = api_client.get("/api/decision/nonexistent/status")
        assert resp.status_code == 404


# ── History Endpoints ─────────────────────────────────────────────────────────

class TestHistoryEndpoints:
    def test_history_200(self, api_client, patch_llm):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        _submit(api_client)
        resp = api_client.get("/api/history")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_history_stats_200(self, api_client, patch_llm):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        _submit(api_client)
        resp = api_client.get("/api/history/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data


# ── LLM Calls Endpoint ───────────────────────────────────────────────────────

class TestLLMCallsEndpoint:
    def test_llm_calls_200(self, api_client, patch_llm):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        submit_resp = _submit(api_client)
        record_id = submit_resp.json()["record_id"]
        resp = api_client.get(f"/api/decision/{record_id}/llm-calls")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ── Export Endpoints ──────────────────────────────────────────────────────────

class TestExportEndpoints:
    def test_export_json_200(self, api_client, patch_llm):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        submit_resp = _submit(api_client)
        record_id = submit_resp.json()["record_id"]
        resp = api_client.get(f"/api/decision/{record_id}/export/json")
        assert resp.status_code == 200
        # Should be valid JSON
        json.loads(resp.content)

    def test_export_pdf_200(self, api_client, patch_llm):
        patch_llm.update({"category": "hardware", "item_description": "laptops"})
        submit_resp = _submit(api_client)
        record_id = submit_resp.json()["record_id"]
        resp = api_client.get(f"/api/decision/{record_id}/export/pdf")
        assert resp.status_code == 200


# ── Health Endpoint ───────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_200(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── Admin Endpoints ───────────────────────────────────────────────────────────

class TestAdminEndpoints:
    def test_llm_provider_get(self, api_client):
        resp = api_client.get("/api/admin/llm-provider")
        assert resp.status_code == 200
        assert "provider" in resp.json()

    def test_llm_provider_set_openai(self, api_client):
        resp = api_client.post("/api/admin/llm-provider/openai")
        assert resp.status_code == 200
        assert resp.json()["provider"] == "openai"

    def test_llm_provider_set_invalid(self, api_client):
        resp = api_client.post("/api/admin/llm-provider/invalid")
        assert resp.status_code == 400
