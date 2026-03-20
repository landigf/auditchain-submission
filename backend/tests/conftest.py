"""
Shared test fixtures for AuditChain test suite.
All tests run against an in-memory SQLite DB with mocked LLM calls.
"""
import os
import sys
import uuid

# Must set env BEFORE any app imports
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["LLM_PROVIDER"] = "openai"
os.environ["USE_FUZZY"] = "false"
os.environ.pop("OPENAI_API_KEY", None)

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db.models import Base, Supplier, Rule, PricingTier, HistoricalAward


# ── Database Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def db_engine():
    # StaticPool + check_same_thread=False ensures ALL sessions share
    # the same in-memory DB even across threads (required for TestClient).
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Fresh DB session per test. Tables recreated each time for isolation."""
    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


class _NoCloseSession:
    """Wraps a session so that .close() is a no-op (prevents check_policy from killing the test session)."""
    def __init__(self, real_session):
        self._real = real_session

    def close(self):
        pass  # no-op — the test fixture manages the session lifecycle

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def patch_sessionlocal(db_session, monkeypatch):
    """Monkeypatch SessionLocal so check_policy's internal DB access uses the test session."""
    monkeypatch.setattr("db.database.SessionLocal", lambda: _NoCloseSession(db_session))


# ── Factory Helpers ───────────────────────────────────────────────────────────

def _make_request(**overrides) -> dict:
    """Return a structured_request dict with sensible defaults."""
    base = {
        "item_description": "Test procurement item",
        "category": "hardware",
        "category_l2": "Laptops",
        "quantity": 10,
        "budget_eur": 50000,
        "deadline_days": 14,
        "delivery_country": "CH",
        "preferred_supplier_name": "",
        "ambiguities": [],
        "missing_fields": [],
    }
    base.update(overrides)
    return base


def _make_supplier(db_session, **overrides) -> Supplier:
    """Create and insert a Supplier with sensible defaults. Returns the ORM instance."""
    sid = overrides.pop("id", f"S-TEST-{uuid.uuid4().hex[:6]}")
    defaults = dict(
        id=sid,
        name=overrides.pop("name", "Test Supplier"),
        category=overrides.pop("category", "hardware"),
        category_l1=overrides.pop("category_l1", "IT"),
        category_l2=overrides.pop("category_l2", "Laptops"),
        unit_price_eur=overrides.pop("unit_price_eur", 500.0),
        min_quantity=overrides.pop("min_quantity", 1),
        delivery_days=overrides.pop("delivery_days", 14),
        compliance_status=overrides.pop("compliance_status", "approved"),
        esg_score=overrides.pop("esg_score", 75),
        preferred_tier=overrides.pop("preferred_tier", "preferred"),
        contract_status=overrides.pop("contract_status", "active"),
        country=overrides.pop("country", "CH"),
        service_regions=overrides.pop("service_regions", "CH;DE;FR"),
        eu_based=overrides.pop("eu_based", True),
        data_residency_supported=overrides.pop("data_residency_supported", True),
        capacity_per_month=overrides.pop("capacity_per_month", 10000),
        notes=overrides.pop("notes", None),
    )
    defaults.update(overrides)
    s = Supplier(**defaults)
    db_session.add(s)
    db_session.flush()
    return s


def _make_rule(db_session, rule_id, name="Test Rule", action="escalate",
               escalate_to=None, active=True) -> Rule:
    """Create and insert a Rule. Returns the ORM instance."""
    r = Rule(id=rule_id, name=name, description=f"Test rule {rule_id}",
             action=action, escalate_to=escalate_to, active=active)
    db_session.add(r)
    db_session.flush()
    return r


def _make_pricing_tier(db_session, supplier_id, min_qty, max_qty, price) -> PricingTier:
    t = PricingTier(supplier_id=supplier_id, min_quantity=min_qty,
                    max_quantity=max_qty, unit_price_eur=price)
    db_session.add(t)
    db_session.flush()
    return t


def _make_award(db_session, supplier_id, category, outcome="completed") -> HistoricalAward:
    a = HistoricalAward(
        award_id=f"AWD-{uuid.uuid4().hex[:8]}",
        supplier_id=supplier_id,
        supplier_name="Test",
        category=category,
        outcome=outcome,
    )
    db_session.add(a)
    db_session.flush()
    return a


@pytest.fixture
def make_request():
    return _make_request


@pytest.fixture
def make_supplier(db_session):
    def _factory(**kw):
        return _make_supplier(db_session, **kw)
    return _factory


@pytest.fixture
def make_rule(db_session):
    def _factory(rule_id, **kw):
        return _make_rule(db_session, rule_id, **kw)
    return _factory


@pytest.fixture
def make_pricing_tier(db_session):
    def _factory(supplier_id, min_qty, max_qty, price):
        return _make_pricing_tier(db_session, supplier_id, min_qty, max_qty, price)
    return _factory


@pytest.fixture
def make_award(db_session):
    def _factory(supplier_id, category, outcome="completed"):
        return _make_award(db_session, supplier_id, category, outcome)
    return _factory


# ── Seed Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def seed_rules(make_rule):
    """Insert the key rules needed for policy engine tests."""
    return {
        "R03": make_rule("R03", name="Blocked Supplier", action="block", escalate_to="Procurement Manager"),
        "R05": make_rule("R05", name="Emergency Timeline", action="escalate", escalate_to="Head of Category"),
        "R06": make_rule("R06", name="ESG Minimum", action="block"),
        "R07": make_rule("R07", name="GDPR Compliance", action="block"),
        "R10": make_rule("R10", name="Spot Vendor Limit", action="block"),
        "ER-001": make_rule("ER-001", name="Missing Information", action="escalate", escalate_to="Requester Clarification"),
    }


@pytest.fixture
def seed_suppliers(make_supplier):
    """Insert a diverse set of test suppliers."""
    return {
        "cheap_hw": make_supplier(id="S-HW-CHEAP", name="Cheap Hardware Co", unit_price_eur=400.0,
                                  esg_score=70, preferred_tier="preferred", delivery_days=10),
        "mid_hw": make_supplier(id="S-HW-MID", name="Mid Hardware Co", unit_price_eur=600.0,
                                esg_score=80, preferred_tier="approved", delivery_days=20),
        "exp_hw": make_supplier(id="S-HW-EXP", name="Expensive Hardware Co", unit_price_eur=900.0,
                                esg_score=90, preferred_tier="preferred", delivery_days=7),
        "blocked": make_supplier(id="S-BLOCKED", name="Blocked Supplier", compliance_status="blocked",
                                 esg_score=80),
        "low_esg": make_supplier(id="S-LOW-ESG", name="Low ESG Supplier", esg_score=55),
        "non_eu": make_supplier(id="S-NON-EU", name="Non-EU Supplier", eu_based=False,
                                country="US", service_regions="US;CA"),
        "spot": make_supplier(id="S-SPOT", name="Spot Vendor", preferred_tier="spot",
                              contract_status="none"),
        "sw": make_supplier(id="S-SW-1", name="Software Co", category="software",
                            category_l2="Cloud Compute", unit_price_eur=200.0),
        "facilities": make_supplier(id="S-FAC-1", name="Facilities Co", category="facilities",
                                    category_l2="Office Chairs", unit_price_eur=300.0),
    }


# ── LLM Mock Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def patch_llm(monkeypatch):
    """Mock both LLM functions to avoid real API calls."""
    _parse_overrides = {}

    def mock_parse(raw_text):
        base = {
            "item_description": "Mocked item from LLM",
            "category": "hardware",
            "category_l2": "Laptops",
            "quantity": 10,
            "budget_eur": 50000,
            "deadline_days": 14,
            "delivery_country": "CH",
            "preferred_supplier_name": "",
            "ambiguities": [],
            "missing_fields": [],
        }
        base.update(_parse_overrides)
        log = {
            "id": str(uuid.uuid4()),
            "call_type": "parse",
            "model": "mock-model",
            "temperature": 0.0,
            "system_prompt": "mock system prompt",
            "user_message": raw_text,
            "raw_response": "{}",
            "extracted_result": "{}",
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 10,
            "timestamp": "2026-03-19T12:00:00Z",
            "parse_method": "mock",
        }
        return base, log

    def mock_narrative(context):
        narrative = "This is a mock audit narrative for testing purposes."
        log = {
            "id": str(uuid.uuid4()),
            "call_type": "narrative",
            "model": "mock-model",
            "temperature": 0.0,
            "system_prompt": "mock system prompt",
            "user_message": str(context),
            "raw_response": narrative,
            "extracted_result": narrative,
            "input_tokens": 200,
            "output_tokens": 100,
            "latency_ms": 20,
            "timestamp": "2026-03-19T12:00:01Z",
            "parse_method": "mock",
        }
        return narrative, log

    # Patch at the source module AND at the import site in pipeline.py
    monkeypatch.setattr("agent.llm_client.parse_request_logged", mock_parse)
    monkeypatch.setattr("agent.llm_client.generate_narrative_logged", mock_narrative)
    monkeypatch.setattr("agent.pipeline.parse_request_logged", mock_parse)
    monkeypatch.setattr("agent.pipeline.generate_narrative_logged", mock_narrative)

    return _parse_overrides  # tests can modify this dict to change parse output


# ── LRU Cache Cleanup ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_policy_cache():
    """Clear the _load_policies LRU cache between tests."""
    yield
    try:
        from agent.tools import _load_policies
        _load_policies.cache_clear()
    except Exception:
        pass
