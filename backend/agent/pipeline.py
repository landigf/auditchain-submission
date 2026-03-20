"""
Sourcing Agent Pipeline — v2.0
-------------------------------
Hybrid approach:
  - LLM (Claude/OpenAI) handles ambiguous natural-language tasks: parse + narrative
  - Python handles deterministic tasks: policy, scoring, decision, AIS, risk

All LLM calls are logged in LLMCallLog (EU AI Act Art.13 compliance).
Every step is timed and stored in pipeline_trace for the admin dashboard.
The pipeline supports async clarification flow when required fields are missing.
"""
from __future__ import annotations
import json
import os
import time
import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from db.models import AuditRecord, LLMCallLog, Rule
from agent.tools import check_policy, query_suppliers, score_suppliers, compute_ais
from agent.llm_client import parse_request_logged, generate_narrative_logged
from agent.risk_scorer import compute_risk_score
from agent.fuzzy_policy import (
    fuzzy_check_policy,
    fuzzy_score_supplier,
    fuzzy_confidence_gate,
    sensitivity_analysis,
    generate_counterfactuals,
)
from agent.tools import CATEGORY_WEIGHTS

AGENT_VERSION = "2.1.0"

QUESTION_TEMPLATES = {
    # Canonical field names from LLM parse schema
    "budget_eur":        "What is your total budget for this purchase? (please specify currency)",
    "quantity":          "How many units do you need?",
    "deadline_days":     "When do you need delivery? (give a date or number of business days from today)",
    "item_description":  "Please describe exactly what you need to purchase.",
    # LLM sometimes uses shorthand names → same questions
    "budget":            "What is your total budget for this purchase? (please specify currency)",
    "deadline":          "When do you need delivery? (give a date or number of business days from today)",
    "currency":          "What currency is your budget in?",
}


def _step(trace: list, name: str, t0: float, llm: bool, summary: str):
    trace.append({"step": name, "ms": int((time.time() - t0) * 1000), "llm": llm, "summary": summary})


def generate_clarification_questions(missing_fields: list[str]) -> list[str]:
    return [QUESTION_TEMPLATES.get(f, f"Please clarify: {f}") for f in missing_fields]


# ── Approval question generation ──────────────────────────────────────────────

def _resolve_approver(budget: float, policy_results: dict) -> str:
    """Determine who the escalation goes to, based on budget tier rules."""
    # First check if policy escalations have an explicit escalate_to
    for esc in policy_results.get("escalations", []):
        if esc.get("escalate_to"):
            return esc["escalate_to"]
    # Fall back to fuzzy threshold approver
    ft = policy_results.get("fuzzy_threshold", {})
    if ft.get("approver"):
        return ft["approver"]
    # Last resort: derive from budget
    if budget > 5_000_000:
        return "CPO"
    elif budget > 500_000:
        return "Head of Strategic Sourcing"
    elif budget > 100_000:
        return "Head of Category"
    elif budget > 25_000:
        return "Procurement Manager"
    return "Procurement Manager"


def _generate_approval_questions(decision: dict, policy_results: dict, structured: dict) -> list[str]:
    """Generate context-aware approval prompts from escalation details."""
    questions = []
    esc_reason = decision.get("escalation_reason", "")
    budget = structured.get("budget_eur", 0)
    approver = _resolve_approver(budget if isinstance(budget, (int, float)) else 0, policy_results)

    if "authority" in esc_reason.lower():
        authority = structured.get("_spending_authority_eur", "?")
        auth_str = f"€{authority:,.0f}" if isinstance(authority, (int, float)) else f"€{authority}"
        budget_str = f"€{budget:,.0f}" if isinstance(budget, (int, float)) else f"€{budget}"
        questions.append(
            f"Budget {budget_str} exceeds requester spending authority ({auth_str}). "
            f"Escalated to {approver} for approval."
        )
    elif "budget" in esc_reason.lower() or "threshold" in esc_reason.lower():
        budget_str = f"€{budget:,.0f}" if isinstance(budget, (int, float)) else f"€{budget}"
        questions.append(f"Budget {budget_str} requires {approver} approval per threshold policy. Do you authorize proceeding?")

    if "confidence" in esc_reason.lower() or "fuzzy" in esc_reason.lower() or "uncertain" in esc_reason.lower():
        conf = decision.get("confidence", 0)
        questions.append(f"System confidence is {conf:.0%}. The ranking may be sensitive to weight assumptions. Approve the recommended supplier?")

    if not questions:
        questions.append(f"This decision requires approval from {approver}. Reason: {esc_reason[:200]}")

    questions.append("Any additional notes or conditions for this approval?")
    return questions


# ── Decision logic ────────────────────────────────────────────────────────────

def make_decision(
    structured_request: dict,
    policy_results: dict,
    scoring_result: dict,
    supplier_results: dict,
) -> dict:
    violations = policy_results.get("violations", [])
    escalations = policy_results.get("escalations", [])
    scored = scoring_result.get("scored", []) if scoring_result else []
    infeasibility = supplier_results.get("infeasibility")

    # Infeasibility → return to client as clarification (budget is the client's problem,
    # not an internal ChainIQ escalation). Client can reduce qty, increase budget, or cancel.
    if infeasibility and infeasibility.get("infeasible"):
        budget = structured_request.get("budget_eur", 0)
        min_cost = infeasibility.get("min_cost_eur", 0)
        max_qty = infeasibility.get("max_affordable_qty", 0)
        quantity = structured_request.get("quantity", "?")
        return {
            "decision_type": "infeasible",
            "recommended_supplier": scored[0] if scored else None,
            "alternatives": scored[1:3] if len(scored) > 1 else [],
            "confidence": 0.0,
            "rejection_reason": None,
            "escalation_reason": None,
            "infeasibility_detail": {
                "reason": infeasibility["reason"],
                "budget_eur": budget,
                "min_cost_eur": min_cost,
                "cheapest_unit_eur": infeasibility.get("cheapest_unit_eur", 0),
                "max_affordable_qty": max_qty,
                "requested_quantity": quantity,
            },
        }

    if violations:
        return {
            "decision_type": "rejected",
            "recommended_supplier": None,
            "alternatives": [],
            "confidence": 1.0,
            "rejection_reason": "; ".join(str(v.get("detail") or v.get("rule_id", "unknown")) for v in violations),
            "escalation_reason": None,
        }

    if escalations:
        # Determine who it escalates to from the escalation rules
        esc_to = next((e.get("escalate_to") for e in escalations if e.get("escalate_to")), None)
        return {
            "decision_type": "escalated",
            "recommended_supplier": scored[0] if scored else None,
            "alternatives": scored[1:3] if len(scored) > 1 else [],
            "confidence": 0.0,
            "rejection_reason": None,
            "escalation_reason": "; ".join(str(e.get("detail") or e.get("rule_id", "unknown")) for e in escalations),
            "escalated_to": esc_to,
        }

    if not scored:
        return {
            "decision_type": "rejected",
            "recommended_supplier": None,
            "alternatives": [],
            "confidence": 1.0,
            "rejection_reason": "No compliant suppliers found for this category and requirements",
            "escalation_reason": None,
        }

    top = scored[0]
    confidence = 1.0
    if len(scored) > 1:
        gap = top["score"] - scored[1]["score"]
        confidence = min(0.99, 0.6 + (gap / 100) * 2)

    return {
        "decision_type": "approved",
        "recommended_supplier": top,
        "alternatives": scored[1:3],
        "confidence": round(confidence, 2),
        "rejection_reason": None,
        "escalation_reason": None,
    }


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(
    raw_request: str,
    db: Session,
    requester_context: dict | None = None,
    partial_structured: dict | None = None,  # pre-populated fields from clarification answers
    parent_record_id: str | None = None,
) -> dict:
    """
    Full sourcing agent pipeline with traceability.
    Returns complete audit record dict + record_id for polling.

    If missing required fields: returns immediately with state=clarification_needed.
    If partial_structured is given: skip LLM parse, merge with answers and run pipeline.
    """
    record_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    ctx = requester_context or {}
    trace: list = []
    llm_logs: list = []

    # Inject requester context into structured_request for downstream checks
    # (spending_authority, company, department)
    _ctx_overrides = {}
    if ctx.get("spending_authority_eur"):
        _ctx_overrides["_spending_authority_eur"] = float(ctx["spending_authority_eur"])

    # ── Step 0: Phase 1 Pre-validation (optional, requires OPENAI_API_KEY) ───
    t0 = time.time()
    phase1_result = None
    if os.getenv("OPENAI_API_KEY") and not partial_structured:
        try:
            from agent.phase1_validation import run_phase1_validation
            phase1_result = run_phase1_validation(raw_request, requester_context)
            status = "PASS" if phase1_result.get("passed") else "ISSUES"
            issues = phase1_result.get("blocking_issues", [])
            warns = phase1_result.get("warnings", [])
            summary = f"{status}"
            if issues:
                summary += f" — {len(issues)} blocking"
            if warns:
                summary += f", {len(warns)} warnings"
            _step(trace, "phase1_validation", t0, True, summary)
        except Exception as e:
            _step(trace, "phase1_validation", t0, False, f"Error (non-blocking): {e}")
    else:
        reason = "Skipped (clarification re-run)" if partial_structured else "Skipped (no OPENAI_API_KEY)"
        _step(trace, "phase1_validation", t0, False, reason)

    # ── Step 1: Parse ─────────────────────────────────────────────────────────
    t0 = time.time()
    if partial_structured:
        # Re-run after clarification: skip LLM parse, use merged structure
        structured = partial_structured
        parse_log_data = None
        _step(trace, "parse", t0, False, f"Skipped (clarification re-run). Fields: {list(structured.keys())}")
    else:
        structured, parse_log_data = parse_request_logged(raw_request)
        if parse_log_data:
            parse_log_data["record_id"] = record_id
            llm_logs.append(parse_log_data)
        _step(trace, "parse", t0, True,
              f"{structured.get('category','?')}, qty={structured.get('quantity','?')}, "
              f"budget=€{structured.get('budget_eur','?')}")

    # ── Step 2: Validate — check for missing required fields ──────────────────
    t0 = time.time()
    missing_fields = structured.get("missing_fields", [])

    # Max 2 clarification rounds — after that, force through with defaults
    MAX_CLARIFICATION_ROUNDS = 2
    clarification_depth = 0
    if parent_record_id:
        _pid = parent_record_id
        for _ in range(10):  # safety cap
            parent = db.query(AuditRecord).filter(AuditRecord.id == _pid).first()
            if not parent or parent.decision_type != "clarification_needed":
                break
            clarification_depth += 1
            _pid = parent.parent_record_id
            if not _pid:
                break

    if missing_fields and clarification_depth >= MAX_CLARIFICATION_ROUNDS:
        # Force through — escalate instead of asking again
        _step(trace, "validate", t0, False,
              f"Max clarification rounds ({MAX_CLARIFICATION_ROUNDS}) reached. "
              f"Still missing: {', '.join(missing_fields)}. Forcing escalation.")
        structured["_forced_past_clarification"] = True
        missing_fields = []  # proceed with what we have

    # If no hard missing fields but critical ambiguities detected,
    # treat as clarification needed (first round only).
    # "Critical" = related to missing deadline/urgency, missing budget, or multiple ambiguities
    ambiguities = structured.get("ambiguities", [])
    CRITICAL_KEYWORDS = ["deadline", "urgency", "date", "budget", "cost", "price", "quantity"]
    critical_ambiguities = [a for a in ambiguities
                            if any(kw in a.lower() for kw in CRITICAL_KEYWORDS)]
    should_clarify = (
        not missing_fields
        and ambiguities
        and clarification_depth == 0
        and (len(critical_ambiguities) >= 1 or len(ambiguities) >= 2)
    )
    if should_clarify:
        # Convert ambiguities to clarification questions
        ask = critical_ambiguities or ambiguities
        ambiguity_questions = [f"Please clarify: {a}" for a in ask[:3]]
        missing_fields = ["_ambiguity_clarification"]
        structured["missing_fields"] = missing_fields
        structured["_ambiguity_questions"] = ambiguity_questions

    if missing_fields:
        # Use ambiguity questions if that's what triggered clarification
        if structured.get("_ambiguity_questions"):
            questions = structured["_ambiguity_questions"]
        else:
            questions = generate_clarification_questions(missing_fields)
        _step(trace, "validate", t0, False, f"Missing: {', '.join(missing_fields)}")

        # Compute clarification deadline based on urgency
        deadline_days = structured.get("deadline_days") or 99
        if deadline_days < 3:
            timeout_hours = 4
        elif deadline_days < 7:
            timeout_hours = 12
        else:
            timeout_hours = 48

        from datetime import timedelta
        deadline_iso = (datetime.now(timezone.utc) + timedelta(hours=timeout_hours)).isoformat()

        # Persist partial record with state=clarification_needed
        record = AuditRecord(
            id=record_id,
            created_at=created_at,
            agent_version=AGENT_VERSION,
            raw_request=raw_request,
            structured_request=json.dumps(structured),
            policy_results=json.dumps({"violations": [], "warnings": [], "escalations": []}),
            supplier_candidates=json.dumps({}),
            scored_suppliers=json.dumps({}),
            decision_type="clarification_needed",
            state="clarification_needed",
            clarification_questions=json.dumps(questions),
            clarification_deadline=deadline_iso,
            parent_record_id=parent_record_id,
            pipeline_trace=json.dumps(trace),
            ais_score=0,
            ais_grade="Incomplete",
            ais_components=json.dumps({}),
            eu_ai_act_compliant=False,
        )
        db.add(record)
        db.commit()

        return {
            "record_id": record_id,
            "state": "clarification_needed",
            "questions": questions,
            "clarification_deadline": deadline_iso,
            "timeout_hours": timeout_hours,
        }

    _step(trace, "validate", t0, False, f"OK — all required fields present")

    # Inject requester context overrides (spending authority, etc.)
    structured.update(_ctx_overrides)

    # ── Step 3: Load rules + policy check ─────────────────────────────────────
    t0 = time.time()
    rules = db.query(Rule).filter(Rule.active == True).all()
    policy_results = fuzzy_check_policy(structured, rules, check_policy)
    viol = len(policy_results.get("violations", []))
    esc = len(policy_results.get("escalations", []))
    _step(trace, "policy_check", t0, False,
          f"{viol} violations, {esc} escalations" if viol or esc else "All clear")

    # ── Step 4: Filter suppliers ───────────────────────────────────────────────
    t0 = time.time()
    supplier_results = query_suppliers(structured, db)
    eligible = supplier_results.get("total_eligible", 0)
    disq = len(supplier_results.get("disqualified", []))
    _step(trace, "filter_suppliers", t0, False, f"{eligible} eligible, {disq} disqualified")

    # Inject preferred_tier for risk scoring (from top candidate)
    candidates = supplier_results.get("candidates", [])
    if candidates:
        structured["_preferred_tier"] = candidates[0].get("preferred_tier", "approved")

    # ── Step 5: Score suppliers ────────────────────────────────────────────────
    t0 = time.time()
    scoring_result = score_suppliers(candidates, structured, db=db)
    scored_list = scoring_result.get("scored", [])
    top_summary = f"{scored_list[0]['name']} {scored_list[0]['score']}" if scored_list else "none"
    _step(trace, "score", t0, False,
          f"Top: {top_summary}" if scored_list else "No eligible suppliers to score")

    # ── Step 5b: Fuzzy supplier scoring overlay ────────────────────────────
    t0 = time.time()
    fuzzy_scores = []
    if scored_list:
        for s in scored_list:
            fs = fuzzy_score_supplier(
                price_normalized=s["score_breakdown"]["price_score"] / 100,
                delivery_normalized=s["score_breakdown"]["delivery_score"] / 100,
                compliance_normalized=s["score_breakdown"]["compliance_score"] / 100,
                esg_normalized=s["score_breakdown"]["esg_score_normalized"] / 100,
            )
            s["fuzzy_score"] = fs["score"]
            s["fuzzy_linguistic"] = fs["linguistic"]
            s["fuzzy_rules_fired"] = fs["rules_fired"]
            s["fuzzy_memberships"] = fs["memberships"]
            fuzzy_scores.append(fs)

        # Sensitivity analysis on scoring weights
        category = structured.get("category", "default")
        weights = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["default"])
        sens = sensitivity_analysis(scored_list, weights)
        scoring_result["sensitivity"] = sens
        scoring_result["fuzzy_scores"] = fuzzy_scores

        # Counterfactual explanations
        cfs = generate_counterfactuals(scored_list, fuzzy_scores)
        scoring_result["counterfactuals"] = cfs

    _step(trace, "fuzzy_scoring", t0, False,
          f"Sensitivity: {'stable' if scoring_result.get('sensitivity', {}).get('ranking_stable') else 'UNSTABLE'}, "
          f"{len(scoring_result.get('counterfactuals', []))} counterfactuals")

    # ── Step 6: Decision ──────────────────────────────────────────────────────
    t0 = time.time()
    decision = make_decision(structured, policy_results, scoring_result, supplier_results)
    _step(trace, "decide", t0, False, decision["decision_type"].upper())

    # ── Step 6a: Infeasible budget → clarification back to client ─────────
    if decision["decision_type"] == "infeasible":
        detail = decision.get("infeasibility_detail", {})
        budget = detail.get("budget_eur", "?")
        min_cost = detail.get("min_cost_eur", "?")
        max_qty = detail.get("max_affordable_qty", 0)
        qty = detail.get("requested_quantity", "?")

        budget_str = f"€{budget:,.0f}" if isinstance(budget, (int, float)) else f"€{budget}"
        min_cost_str = f"€{min_cost:,.0f}" if isinstance(min_cost, (int, float)) else f"€{min_cost}"

        if max_qty <= 0:
            cheapest = detail.get("cheapest_unit_eur", min_cost)
            cheapest_str = f"€{cheapest:,.0f}" if isinstance(cheapest, (int, float)) else f"€{cheapest}"
            questions = [
                f"Your budget of {budget_str} is too low — the cheapest unit available costs {cheapest_str}. "
                f"Please increase your budget or cancel this request.",
            ]
        else:
            questions = [
                f"Your budget of {budget_str} cannot cover {qty} units (minimum cost is {min_cost_str}). "
                f"You can afford up to {max_qty} unit{'s' if max_qty != 1 else ''}. "
                f"Would you like to reduce the quantity to {max_qty}, increase your budget, or cancel?",
            ]

        from datetime import timedelta
        deadline_iso = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()

        record = AuditRecord(
            id=record_id,
            created_at=created_at,
            agent_version=AGENT_VERSION,
            raw_request=raw_request,
            structured_request=json.dumps(structured),
            policy_results=json.dumps({"violations": [], "warnings": [], "escalations": []}),
            supplier_candidates=json.dumps(supplier_results),
            scored_suppliers=json.dumps(scoring_result),
            decision_type="clarification_needed",
            state="clarification_needed",
            clarification_questions=json.dumps(questions),
            clarification_deadline=deadline_iso,
            parent_record_id=parent_record_id,
            pipeline_trace=json.dumps(trace),
            ais_score=0,
            ais_grade="Incomplete",
            ais_components=json.dumps({}),
            eu_ai_act_compliant=False,
        )
        db.add(record)
        db.commit()

        return {
            "record_id": record_id,
            "state": "clarification_needed",
            "questions": questions,
            "clarification_deadline": deadline_iso,
            "timeout_hours": 48,
        }

    # ── Step 6b: Fuzzy confidence gate ─────────────────────────────────────
    t0 = time.time()
    threshold_result = policy_results.get("fuzzy_threshold", {})
    conf_gate = fuzzy_confidence_gate(
        threshold_result=threshold_result,
        top_supplier_score=scored_list[0]["score"] if scored_list else 0,
        second_supplier_score=scored_list[1]["score"] if len(scored_list) > 1 else None,
        num_candidates=len(candidates),
        has_ambiguities=bool(structured.get("ambiguities")),
        has_missing_fields=bool(structured.get("missing_fields")),
    )

    # Override confidence with fuzzy gate
    decision["confidence"] = conf_gate["confidence"]
    decision["confidence_label"] = conf_gate["confidence_label"]
    decision["uncertainty_signals"] = conf_gate["uncertainty_signals"]

    # If fuzzy gate says escalate but hard rules didn't → add escalation
    if conf_gate["should_escalate"] and decision["decision_type"] == "approved":
        decision["decision_type"] = "escalated"
        decision["escalation_reason"] = conf_gate["escalation_reason"]

    _step(trace, "confidence_gate", t0, False,
          f"Confidence: {conf_gate['confidence']:.0%} ({conf_gate['confidence_label']}), "
          f"{len(conf_gate['uncertainty_signals'])} signals")

    # ── Step 7: Narrative (LLM) ────────────────────────────────────────────────
    t0 = time.time()
    narrative_context = {
        "request": structured,
        "policy_checks": {
            "escalations": policy_results.get("escalations", []),
            "warnings": policy_results.get("warnings", []),
            "violations": policy_results.get("violations", []),
        },
        "top_suppliers": scored_list[:3],
        "decision": decision,
    }
    narrative, narrative_log = generate_narrative_logged(narrative_context)
    narrative_log["record_id"] = record_id
    llm_logs.append(narrative_log)
    decision["reasoning_narrative"] = narrative
    _step(trace, "narrative", t0, True, f"{len(narrative)} chars")

    # ── Step 8: Risk score ────────────────────────────────────────────────────
    t0 = time.time()
    risk_result = compute_risk_score(structured, ctx)
    risk_score = risk_result["score"]
    _step(trace, "risk_score", t0, False,
          f"{risk_score}/100 ({risk_result['approach']})")

    # ── Step 9: AIS ────────────────────────────────────────────────────────────
    t0 = time.time()
    ais = compute_ais(structured, policy_results, supplier_results, scoring_result, decision)
    _step(trace, "ais", t0, False, f"{ais['score']}/100 {ais['grade']}")

    # ── Step 10: Persist ───────────────────────────────────────────────────────
    t0 = time.time()
    rec_supplier = decision.get("recommended_supplier")
    record = AuditRecord(
        id=record_id,
        created_at=created_at,
        agent_version=AGENT_VERSION,
        raw_request=raw_request,
        structured_request=json.dumps(structured),
        policy_results=json.dumps({
            "violations": policy_results["violations"],
            "warnings": policy_results["warnings"],
            "escalations": policy_results["escalations"],
        }),
        supplier_candidates=json.dumps(supplier_results),
        scored_suppliers=json.dumps(scoring_result),
        decision_type=decision["decision_type"],
        recommended_supplier_id=rec_supplier["id"] if rec_supplier else None,
        recommended_supplier_name=rec_supplier["name"] if rec_supplier else None,
        estimated_total_eur=(
            rec_supplier["total_cost_eur"] if rec_supplier and "total_cost_eur" in rec_supplier else None
        ),
        confidence=decision.get("confidence"),
        reasoning_narrative=narrative,
        escalation_reason=decision.get("escalation_reason"),
        rejection_reason=decision.get("rejection_reason"),
        ais_score=ais["score"],
        ais_grade=ais["grade"],
        ais_components=json.dumps(ais["components"]),
        eu_ai_act_compliant=ais["eu_ai_act_article_13_compliant"],
        state="completed",
        parent_record_id=parent_record_id,
        pipeline_trace=json.dumps(trace),
        fuzzy_trace=json.dumps({
            "risk": risk_result if risk_result.get("approach") == "fuzzy" else None,
            "threshold": policy_results.get("fuzzy_threshold"),
            "sensitivity": scoring_result.get("sensitivity"),
            "counterfactuals": scoring_result.get("counterfactuals"),
            "confidence_gate": {
                "confidence": decision.get("confidence"),
                "confidence_label": decision.get("confidence_label"),
                "uncertainty_signals": decision.get("uncertainty_signals", []),
            },
        }),
        risk_score=risk_score,
        confidence_label=decision.get("confidence_label"),
    )
    db.add(record)

    # Persist LLM call logs
    for log_data in llm_logs:
        db.add(LLMCallLog(
            id=log_data["id"],
            record_id=log_data["record_id"],
            call_type=log_data["call_type"],
            model=log_data["model"],
            temperature=log_data.get("temperature", 0.0),
            system_prompt=log_data["system_prompt"],
            user_message=log_data["user_message"],
            raw_response=log_data["raw_response"],
            extracted_result=log_data.get("extracted_result"),
            input_tokens=log_data.get("input_tokens"),
            output_tokens=log_data.get("output_tokens"),
            latency_ms=log_data.get("latency_ms"),
            timestamp=log_data["timestamp"],
            parse_method=log_data.get("parse_method", "llm"),
        ))

    db.commit()

    # ── Approval gate: escalated decisions wait for human approval ───────────
    approval_prompts = None
    if decision["decision_type"] == "escalated":
        budget_val = structured.get("budget_eur", 0)
        approver = _resolve_approver(budget_val if isinstance(budget_val, (int, float)) else 0, policy_results)
        decision["escalated_to"] = approver
        record.state = "awaiting_approval"
        record.approval_required = True
        approval_prompts = _generate_approval_questions(decision, policy_results, structured)
        record.approval_questions = json.dumps(approval_prompts)
        from datetime import timedelta
        record.approval_deadline = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        db.commit()

    db.refresh(record)
    _step(trace, "persist", t0, False, f"record_id={record_id[:8]}")

    base_result = {
        "record_id": record_id,
        "state": record.state or "completed",
        "created_at": created_at,
        "structured_request": structured,
        "policy_results": {
            "violations": policy_results["violations"],
            "warnings": policy_results["warnings"],
            "escalations": policy_results["escalations"],
            "all_clear": policy_results["all_clear"],
        },
        "supplier_results": supplier_results,
        "scoring_result": scoring_result,
        "decision": decision,
        "ais": ais,
        "risk_score": risk_result,
        "pipeline_trace": trace,
    }

    # Add approval fields for escalated decisions
    if approval_prompts:
        base_result["approval_questions"] = approval_prompts
        base_result["approval_deadline"] = record.approval_deadline
        base_result["timeout_hours"] = 24

    return base_result
