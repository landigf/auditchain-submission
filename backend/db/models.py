from sqlalchemy import Column, String, Float, Integer, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class AuditRecord(Base):
    """Immutable append-only audit log. Never update rows — only insert."""
    __tablename__ = "audit_records"

    id = Column(String, primary_key=True)
    created_at = Column(String, nullable=False)
    agent_version = Column(String, nullable=False, default="1.0.0")

    # Raw input
    raw_request = Column(Text, nullable=False)

    # Parsed structure (JSON)
    structured_request = Column(Text, nullable=False)

    # Processing (JSON arrays)
    policy_results = Column(Text, nullable=False)
    supplier_candidates = Column(Text, nullable=False)
    scored_suppliers = Column(Text, nullable=False)

    # Decision
    decision_type = Column(String, nullable=False)   # approved | escalated | rejected
    recommended_supplier_id = Column(String)
    recommended_supplier_name = Column(String)
    estimated_total_eur = Column(Float)
    confidence = Column(Float)
    reasoning_narrative = Column(Text)
    escalation_reason = Column(Text)
    rejection_reason = Column(Text)

    # AIS
    ais_score = Column(Integer)
    ais_grade = Column(String)
    ais_components = Column(Text)    # JSON
    eu_ai_act_compliant = Column(Boolean)

    # Async clarification state machine
    state = Column(String, default="completed")   # submitted|clarification_needed|processing|completed|abandoned
    clarification_questions = Column(Text)         # JSON list of question strings
    clarification_deadline = Column(String)        # ISO timestamp
    clarification_answered_at = Column(String)     # NULL until answered
    clarification_answers = Column(Text)           # JSON: {"budget_eur": 50000, ...}
    parent_record_id = Column(String)              # links to original record if this is a re-run

    # Async approval state machine (for escalated decisions)
    approval_required = Column(Boolean, default=False)       # True if decision was escalated
    approval_questions = Column(Text)                        # JSON list of approval prompts
    approval_deadline = Column(String)                       # ISO timestamp
    approval_answered_at = Column(String)                    # NULL until manager responds
    approval_answers = Column(Text)                          # JSON: {"action": "approve", "reason": "..."}
    approval_responder = Column(String)                      # Who approved (name/role)

    # Basket (multi-item)
    is_basket = Column(Boolean, default=False)
    basket_line_count = Column(Integer, nullable=True)
    basket_line_decisions = Column(Text, nullable=True)   # JSON: [{line_idx, category, decision_type, supplier, cost}]
    basket_total_cost = Column(Float, nullable=True)

    # Traceability
    pipeline_trace = Column(Text)   # JSON: [{step, ms, llm, summary}]
    fuzzy_trace = Column(Text)      # JSON: fuzzy threshold + sensitivity + counterfactuals + confidence
    risk_score = Column(Integer)    # 0-100 composite risk score
    confidence_label = Column(String, nullable=True)  # "low" / "moderate" / "high" from fuzzy gate


class LLMCallLog(Base):
    """Immutable log of every LLM API call — required for EU AI Act Art.13 auditability."""
    __tablename__ = "llm_call_logs"

    id = Column(String, primary_key=True)
    record_id = Column(String, nullable=False)      # links to AuditRecord
    call_type = Column(String, nullable=False)      # "parse" | "narrative"
    model = Column(String, nullable=False)
    temperature = Column(Float, default=0.0)
    system_prompt = Column(Text, nullable=False)
    user_message = Column(Text, nullable=False)
    raw_response = Column(Text, nullable=False)     # full API response as JSON
    extracted_result = Column(Text)                 # tool_call result or narrative text
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    latency_ms = Column(Integer)
    timestamp = Column(String, nullable=False)
    parse_method = Column(String, default="llm")   # "llm" | "regex_fallback"


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)       # mapped scoring category
    category_l1 = Column(String)                    # original ChainIQ category_l1 (e.g. "IT")
    category_l2 = Column(String)                    # original ChainIQ category_l2 (e.g. "Laptops")
    unit_price_eur = Column(Float, nullable=False)
    min_quantity = Column(Integer, default=1)
    delivery_days = Column(Integer, nullable=False)
    compliance_status = Column(String, nullable=False)  # approved | blocked | under_review
    esg_score = Column(Integer, nullable=False)          # 0-100
    preferred_tier = Column(String, nullable=False)      # preferred | approved | spot
    contract_status = Column(String, nullable=False)     # active | expired | none
    country = Column(String, nullable=False)             # HQ country
    service_regions = Column(Text)                       # semicolon-separated ISO codes (e.g. "DE;FR;CH")
    eu_based = Column(Boolean, nullable=False)
    data_residency_supported = Column(Boolean, default=False)
    capacity_per_month = Column(Integer)
    notes = Column(Text)


class Rule(Base):
    __tablename__ = "rules"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    action = Column(String, nullable=False)   # block | warn | escalate
    escalate_to = Column(String)
    warn_reason = Column(Text)
    active = Column(Boolean, default=True)


class PricingTier(Base):
    """Volume pricing tiers from pricing.csv. Used in cost calculation."""
    __tablename__ = "pricing_tiers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    supplier_id = Column(String, nullable=False)
    min_quantity = Column(Integer, nullable=False, default=0)
    max_quantity = Column(Integer)          # null = unlimited
    unit_price_eur = Column(Float, nullable=False)
    discount_pct = Column(Float, default=0.0)


class HistoricalAward(Base):
    """Past sourcing decisions from historical_awards.csv."""
    __tablename__ = "historical_awards"

    award_id = Column(String, primary_key=True)
    supplier_id = Column(String)
    supplier_name = Column(String, nullable=False)
    category = Column(String)
    quantity = Column(Integer)
    total_eur = Column(Float)
    award_date = Column(String)
    outcome = Column(String)               # completed | cancelled | disputed
