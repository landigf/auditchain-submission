"""
AuditChain Procurement Flow — Metaflow FlowSpec v2.0
=====================================================
10-step deterministic procurement pipeline with full artifact provenance.
Every intermediate result is persisted as a Metaflow artifact for 1-year audit replay.

The LLM is ONLY used at:
  - start (parse): text → structured JSON
  - narrative: structured decision → 2-paragraph explanation

All other steps are deterministic Python — fully replayable.

Usage (no metaflow-service needed — uses local filesystem):
    cd solution/backend
    source .venv/bin/activate
    python agent/procurement_flow.py run \\
        --raw_request "I need 50 ergonomic chairs for Zurich, budget €12,000, within 10 days"

With requester context:
    python agent/procurement_flow.py run \\
        --raw_request "I need 50 chairs, budget €12,000" \\
        --requester_context_json '{"company":"UBS","department":"IT","spending_authority_eur":25000}'

With metaflow-service running (for Metaflow UI at http://localhost:3000):
    export METAFLOW_SERVICE_URL=http://localhost:8080
    export METAFLOW_DEFAULT_METADATA=service
    python agent/procurement_flow.py run --raw_request "..."

List past runs:
    python agent/procurement_flow.py list

Inspect a run's artifacts from Python:
    from metaflow import Flow, Run
    run = Flow("ProcurementFlow").latest_run
    print(run["end"].task.data.decision)
    print(run["end"].task.data.ais)
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta

# Add backend root to path so agent.tools / db are importable when run standalone
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_ROOT)

# Load .env from backend root — runs in EVERY subprocess Metaflow spawns per step
from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND_ROOT, ".env"))

from metaflow import FlowSpec, step, Parameter

from agent.pipeline import make_decision, generate_clarification_questions
from agent.tools import check_policy, query_suppliers, score_suppliers, compute_ais
from agent.llm_client import parse_request_logged, generate_narrative_logged
from agent.risk_scorer import compute_risk_score
from db.database import SessionLocal, init_db
from db.models import AuditRecord, LLMCallLog, Rule

AGENT_VERSION = "2.0.0-metaflow"


class ProcurementFlow(FlowSpec):
    """
    AuditChain Autonomous Procurement Pipeline.

    Implements EU AI Act Art.13 compliant procurement decision-making.
    All step outputs are Metaflow artifacts — inspectable from notebooks 1 year later.
    """

    raw_request = Parameter(
        "raw_request",
        help="Natural language procurement request from the requester.",
        required=True,
    )
    requester_context_json = Parameter(
        "requester_context_json",
        default="{}",
        help='JSON string: {"company":"UBS","department":"IT","spending_authority_eur":25000}',
    )
    parent_record_id = Parameter(
        "parent_record_id",
        default="",
        help="For clarification re-runs: the original record_id that triggered clarification.",
    )
    partial_structured_json = Parameter(
        "partial_structured_json",
        default="",
        help="For clarification re-runs: JSON of pre-filled structured_request merged with answers.",
    )

    # ── Step 1: Parse ──────────────────────────────────────────────────────────

    @step
    def start(self):
        """LLM call (temperature=0): natural language → structured JSON. Logged for audit."""
        self.record_id = str(uuid.uuid4())
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.requester_context = json.loads(self.requester_context_json) if self.requester_context_json.strip() not in ("", "{}") else {}
        self.parent_id = self.parent_record_id or None
        self.trace: list = []
        self.llm_logs: list = []

        t0 = time.time()
        if self.partial_structured_json:
            # Clarification re-run: skip LLM parse, use merged structure
            self.structured = json.loads(self.partial_structured_json)
            self.trace.append({
                "step": "parse", "ms": int((time.time() - t0) * 1000),
                "llm": False, "summary": "Skipped (clarification re-run)",
            })
        else:
            self.structured, parse_log = parse_request_logged(self.raw_request)
            if parse_log:
                parse_log["record_id"] = self.record_id
                self.llm_logs.append(parse_log)
            self.trace.append({
                "step": "parse", "ms": int((time.time() - t0) * 1000), "llm": True,
                "summary": (f"{self.structured.get('category', '?')}, "
                            f"qty={self.structured.get('quantity', '?')}, "
                            f"budget=€{self.structured.get('budget_eur', '?')}"),
            })

        print(f"[parse] record_id={self.record_id[:8]}, "
              f"category={self.structured.get('category')}, "
              f"budget=€{self.structured.get('budget_eur')}")
        self.next(self.validate)

    # ── Step 2: Validate ───────────────────────────────────────────────────────

    @step
    def validate(self):
        """Detect missing required fields. If missing → clarification path."""
        t0 = time.time()
        missing_fields = self.structured.get("missing_fields", [])
        self.needs_clarification = bool(missing_fields)
        self.clarification_questions = (
            generate_clarification_questions(missing_fields) if missing_fields else []
        )

        if self.needs_clarification:
            deadline_days = self.structured.get("deadline_days") or 99
            timeout_hours = 4 if deadline_days < 3 else 12 if deadline_days < 7 else 48
            self.clarification_deadline = (
                datetime.now(timezone.utc) + timedelta(hours=timeout_hours)
            ).isoformat()
            self.timeout_hours = timeout_hours
            self.trace.append({
                "step": "validate", "ms": int((time.time() - t0) * 1000),
                "llm": False, "summary": f"Missing: {', '.join(missing_fields)}",
            })
            print(f"[validate] CLARIFICATION NEEDED: {missing_fields}")
        else:
            self.clarification_deadline = ""
            self.timeout_hours = 0
            self.trace.append({
                "step": "validate", "ms": int((time.time() - t0) * 1000),
                "llm": False, "summary": "OK — all required fields present",
            })
            print("[validate] OK")

        self.next(self.policy_check)

    # ── Step 3: Policy check ───────────────────────────────────────────────────

    @step
    def policy_check(self):
        """30 deterministic rules: violations→REJECT, escalations→ESCALATE, warnings→flag."""
        t0 = time.time()
        if self.needs_clarification:
            self.policy_results = {
                "violations": [], "warnings": [], "escalations": [], "all_clear": True,
            }
        else:
            db = SessionLocal()
            try:
                rules = db.query(Rule).filter(Rule.active == True).all()
                self.policy_results = check_policy(self.structured, rules)
            finally:
                db.close()
            viol = len(self.policy_results.get("violations", []))
            esc = len(self.policy_results.get("escalations", []))
            print(f"[policy_check] violations={viol}, escalations={esc}")

        self.trace.append({
            "step": "policy_check", "ms": int((time.time() - t0) * 1000), "llm": False,
            "summary": (
                f"{len(self.policy_results['violations'])} violations, "
                f"{len(self.policy_results['escalations'])} escalations"
                if not self.needs_clarification else "skipped"
            ),
        })
        self.next(self.filter_suppliers)

    # ── Step 4: Filter suppliers ───────────────────────────────────────────────

    @step
    def filter_suppliers(self):
        """Query supplier DB. Disqualify blocked/non-compliant. Detect infeasibility."""
        t0 = time.time()
        if self.needs_clarification:
            self.supplier_results = {
                "candidates": [], "disqualified": [], "total_found": 0, "total_eligible": 0,
            }
        else:
            db = SessionLocal()
            try:
                self.supplier_results = query_suppliers(self.structured, db)
            finally:
                db.close()
            candidates = self.supplier_results.get("candidates", [])
            if candidates:
                self.structured["_preferred_tier"] = candidates[0].get("preferred_tier", "approved")
            eligible = self.supplier_results.get("total_eligible", 0)
            disq = len(self.supplier_results.get("disqualified", []))
            print(f"[filter_suppliers] eligible={eligible}, disqualified={disq}")

        self.trace.append({
            "step": "filter_suppliers", "ms": int((time.time() - t0) * 1000), "llm": False,
            "summary": (
                f"{self.supplier_results.get('total_eligible', 0)} eligible, "
                f"{len(self.supplier_results.get('disqualified', []))} disqualified"
            ),
        })
        self.next(self.score)

    # ── Step 5: Score ──────────────────────────────────────────────────────────

    @step
    def score(self):
        """Weighted composite score: price 35-40%, delivery 25%, compliance 20-25%, ESG 15-25%."""
        t0 = time.time()
        if self.needs_clarification:
            self.scoring_result = {"scored": [], "scoring_warnings": []}
        else:
            candidates = self.supplier_results.get("candidates", [])
            self.scoring_result = score_suppliers(candidates, self.structured)
            scored = self.scoring_result.get("scored", [])
            top = f"{scored[0]['name']} {scored[0]['score']}" if scored else "none"
            print(f"[score] top={top}")

        self.trace.append({
            "step": "score", "ms": int((time.time() - t0) * 1000), "llm": False,
            "summary": (
                f"Top: {self.scoring_result['scored'][0]['name']}"
                if self.scoring_result.get("scored") else "No eligible suppliers"
            ),
        })
        self.next(self.decide)

    # ── Step 6: Decide ─────────────────────────────────────────────────────────

    @step
    def decide(self):
        """Deterministic decision tree → APPROVED / ESCALATED / REJECTED / CLARIFICATION_NEEDED."""
        t0 = time.time()
        if self.needs_clarification:
            self.decision = {
                "decision_type": "clarification_needed",
                "recommended_supplier": None,
                "alternatives": [],
                "confidence": 0.0,
                "rejection_reason": None,
                "escalation_reason": None,
            }
        else:
            self.decision = make_decision(
                self.structured, self.policy_results,
                self.scoring_result, self.supplier_results,
            )
            print(f"[decide] {self.decision['decision_type'].upper()}")

        self.trace.append({
            "step": "decide", "ms": int((time.time() - t0) * 1000), "llm": False,
            "summary": self.decision["decision_type"].upper(),
        })
        self.next(self.narrative)

    # ── Step 7: Narrative (LLM) ────────────────────────────────────────────────

    @step
    def narrative(self):
        """LLM generates audit explanation AFTER decision is made. Cannot change the outcome."""
        t0 = time.time()
        self.narrative_text = ""
        if not self.needs_clarification:
            scored_list = self.scoring_result.get("scored", [])
            context = {
                "request": self.structured,
                "policy_checks": {
                    "escalations": self.policy_results.get("escalations", []),
                    "warnings": self.policy_results.get("warnings", []),
                    "violations": self.policy_results.get("violations", []),
                },
                "top_suppliers": scored_list[:3],
                "decision": self.decision,
            }
            text, narr_log = generate_narrative_logged(context)
            self.narrative_text = text
            if narr_log:
                narr_log["record_id"] = self.record_id
                self.llm_logs.append(narr_log)
            self.decision["reasoning_narrative"] = self.narrative_text
            print(f"[narrative] {len(self.narrative_text)} chars")

        self.trace.append({
            "step": "narrative", "ms": int((time.time() - t0) * 1000),
            "llm": not self.needs_clarification,
            "summary": f"{len(self.narrative_text)} chars" if self.narrative_text else "skipped",
        })
        self.next(self.risk_score_step)

    # ── Step 8: Risk score ─────────────────────────────────────────────────────

    @step
    def risk_score_step(self):
        """
        Linear or fuzzy risk score (USE_FUZZY env var).
        Fuzzy catches near-miss cases: 80% of spending authority → MEDIUM risk.
        Additive to hard rules — never overrides a policy decision.
        """
        t0 = time.time()
        self.risk_result = compute_risk_score(self.structured, self.requester_context)
        self.risk_score_val = self.risk_result["score"]
        print(f"[risk_score] {self.risk_score_val}/100 ({self.risk_result['approach']})")
        self.trace.append({
            "step": "risk_score", "ms": int((time.time() - t0) * 1000), "llm": False,
            "summary": f"{self.risk_score_val}/100 ({self.risk_result['approach']})",
        })
        self.next(self.ais_step)

    # ── Step 9: AIS ────────────────────────────────────────────────────────────

    @step
    def ais_step(self):
        """
        Audit Intelligence Score (0-100).
        Measures EU AI Act Art.13 compliance: completeness, traceability, contestability.
        """
        t0 = time.time()
        self.ais = compute_ais(
            self.structured, self.policy_results,
            self.supplier_results, self.scoring_result, self.decision,
        )
        print(f"[ais] {self.ais['score']}/100 {self.ais['grade']}")
        self.trace.append({
            "step": "ais", "ms": int((time.time() - t0) * 1000), "llm": False,
            "summary": f"{self.ais['score']}/100 {self.ais['grade']}",
        })
        self.next(self.persist)

    # ── Step 10: Persist ───────────────────────────────────────────────────────

    @step
    def persist(self):
        """Write immutable AuditRecord to DB. Records are NEVER deleted — full audit trail."""
        t0 = time.time()
        init_db()  # idempotent — creates tables if not exist
        db = SessionLocal()
        try:
            if self.needs_clarification:
                record = AuditRecord(
                    id=self.record_id,
                    created_at=self.created_at,
                    agent_version=AGENT_VERSION,
                    raw_request=self.raw_request,
                    structured_request=json.dumps(self.structured),
                    policy_results=json.dumps({"violations": [], "warnings": [], "escalations": []}),
                    supplier_candidates=json.dumps({}),
                    scored_suppliers=json.dumps({}),
                    decision_type="clarification_needed",
                    state="clarification_needed",
                    clarification_questions=json.dumps(self.clarification_questions),
                    clarification_deadline=self.clarification_deadline,
                    parent_record_id=self.parent_id,
                    pipeline_trace=json.dumps(self.trace),
                    ais_score=0,
                    ais_grade="Incomplete",
                    ais_components=json.dumps({}),
                    eu_ai_act_compliant=False,
                )
            else:
                rec_supplier = self.decision.get("recommended_supplier")
                record = AuditRecord(
                    id=self.record_id,
                    created_at=self.created_at,
                    agent_version=AGENT_VERSION,
                    raw_request=self.raw_request,
                    structured_request=json.dumps(self.structured),
                    policy_results=json.dumps({
                        "violations": self.policy_results["violations"],
                        "warnings": self.policy_results["warnings"],
                        "escalations": self.policy_results["escalations"],
                    }),
                    supplier_candidates=json.dumps(self.supplier_results),
                    scored_suppliers=json.dumps(self.scoring_result),
                    decision_type=self.decision["decision_type"],
                    recommended_supplier_id=rec_supplier["id"] if rec_supplier else None,
                    recommended_supplier_name=rec_supplier["name"] if rec_supplier else None,
                    estimated_total_eur=(
                        rec_supplier["total_cost_eur"]
                        if rec_supplier and "total_cost_eur" in rec_supplier else None
                    ),
                    confidence=self.decision.get("confidence"),
                    reasoning_narrative=self.narrative_text,
                    escalation_reason=self.decision.get("escalation_reason"),
                    rejection_reason=self.decision.get("rejection_reason"),
                    ais_score=self.ais["score"],
                    ais_grade=self.ais["grade"],
                    ais_components=json.dumps(self.ais["components"]),
                    eu_ai_act_compliant=self.ais["eu_ai_act_article_13_compliant"],
                    state="completed",
                    parent_record_id=self.parent_id,
                    pipeline_trace=json.dumps(self.trace),
                    fuzzy_trace=(
                        json.dumps(self.risk_result)
                        if self.risk_result.get("approach") == "fuzzy" else None
                    ),
                    risk_score=self.risk_score_val,
                )
                # Persist LLM call logs (EU AI Act Art.13 audit trail)
                for log_data in self.llm_logs:
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

            db.add(record)
            db.commit()
            self.final_state = "clarification_needed" if self.needs_clarification else "completed"
            print(f"[persist] record_id={self.record_id[:8]}, state={self.final_state}")
        finally:
            db.close()

        self.trace.append({
            "step": "persist", "ms": int((time.time() - t0) * 1000),
            "llm": False, "summary": f"record_id={self.record_id[:8]}",
        })
        self.next(self.end)

    # ── End ────────────────────────────────────────────────────────────────────

    @step
    def end(self):
        """Pipeline complete. All results available as Metaflow artifacts."""
        total_ms = sum(s["ms"] for s in self.trace)
        print(f"\n{'=' * 60}")
        print(f"  AuditChain Pipeline Complete")
        print(f"  record_id  : {self.record_id}")
        print(f"  state      : {self.final_state}")
        if not self.needs_clarification:
            print(f"  decision   : {self.decision['decision_type'].upper()}")
            if self.decision.get("recommended_supplier"):
                s = self.decision["recommended_supplier"]
                print(f"  supplier   : {s['name']} (score={s['score']})")
            print(f"  ais        : {self.ais['score']}/100 ({self.ais['grade']})")
            print(f"  risk       : {self.risk_score_val}/100 ({self.risk_result['approach']})")
        else:
            print(f"  questions  : {self.clarification_questions}")
            print(f"  deadline   : {self.clarification_deadline}")
        print(f"  total_ms   : {total_ms}")
        print(f"{'=' * 60}\n")
        print(f"Inspect artifacts:")
        print(f"  from metaflow import Flow")
        print(f"  run = Flow('ProcurementFlow').latest_run")
        print(f"  run['end'].task.data.decision")


if __name__ == "__main__":
    ProcurementFlow()
