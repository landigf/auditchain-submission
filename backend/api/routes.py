import json
import os
import threading
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any
from sqlalchemy.orm import Session
from db.database import get_db
from db.models import AuditRecord, LLMCallLog, Supplier
from agent.pipeline import run_pipeline
from io import BytesIO


def _trigger_metaflow_background(
    raw_request: str,
    structured_request: dict,
    requester_context: dict | None,
    parent_record_id: str,
):
    """Fire-and-forget Metaflow flow for DAG visualization."""
    try:
        import subprocess, os
        env = os.environ.copy()
        env["USERNAME"] = env.get("USERNAME", "auditchain")
        structured_json = json.dumps(structured_request)
        ctx_json = json.dumps(requester_context or {})
        proc = subprocess.Popen(
            ["python", "agent/phase2_flow.py", "run",
             "--structured_json", structured_json,
             "--raw_request", raw_request,
             "--requester_context_json", ctx_json,
             "--parent_record_id", parent_record_id],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # Log output in background thread
        def _log_output():
            try:
                for line in proc.stdout:
                    print(f"[Metaflow] {line.decode(errors='replace').rstrip()}")
                proc.wait()
                print(f"[Metaflow] Process exited with code {proc.returncode}")
            except Exception:
                pass
        import threading
        threading.Thread(target=_log_output, daemon=True).start()
    except Exception as e:
        print(f"[Metaflow] Background trigger failed (non-critical): {e}")

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────

class SubmitRequest(BaseModel):
    request_text: str
    requester_context: dict | None = None   # {"company": "UBS", "spending_authority_eur": 25000, ...}


class ClarifyRequest(BaseModel):
    answers: dict   # {"budget_eur": 50000, "deadline_days": 14, ...}


class ApprovalRequest(BaseModel):
    action: str                        # "approve" or "reject"
    reason: str | None = None          # optional rejection reason or approval notes
    responder_name: str | None = None  # who approved (procurement manager name)


# ── Submit a new sourcing request ─────────────────────────────────────────────

@router.post("/submit")
def submit_request(body: SubmitRequest, db: Session = Depends(get_db)):
    if not body.request_text.strip():
        raise HTTPException(status_code=400, detail="request_text cannot be empty")
    if len(body.request_text) > 5000:
        raise HTTPException(status_code=400, detail="request_text exceeds 5000 character limit")
    try:
        result = run_pipeline(
            body.request_text,
            db,
            requester_context=body.requester_context,
        )
        # Fire-and-forget Metaflow DAG for visualization (non-blocking)
        # Skip during batch testing to conserve DB connections
        skip_metaflow = os.getenv("SKIP_METAFLOW", "").lower() in ("1", "true", "yes")
        if not skip_metaflow and result.get("state") in ("completed", "awaiting_approval"):
            threading.Thread(
                target=_trigger_metaflow_background,
                args=(
                    body.request_text,
                    result.get("structured_request", {}),
                    body.requester_context,
                    result["record_id"],
                ),
                daemon=True,
            ).start()
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── Answer clarification questions → resume pipeline ─────────────────────────

@router.post("/decision/{record_id}/clarify")
def clarify_request(record_id: str, body: ClarifyRequest, db: Session = Depends(get_db)):
    """
    Customer answers the clarification questions.
    Merges answers into the partial structured_request and re-runs the pipeline.
    Returns a new completed record linked to the original via parent_record_id.
    """
    record = db.query(AuditRecord).filter(AuditRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    if record.state != "clarification_needed":
        raise HTTPException(status_code=409, detail=f"Record state is '{record.state}', not clarification_needed")

    # Check deadline
    from datetime import datetime, timezone
    if record.clarification_deadline:
        deadline = datetime.fromisoformat(record.clarification_deadline)
        if datetime.now(timezone.utc) > deadline:
            # Mark as abandoned
            record.state = "abandoned"
            db.commit()
            raise HTTPException(status_code=410, detail="Clarification deadline has passed — request abandoned")

    # Mark original as answered
    record.state = "processing"
    record.clarification_answered_at = datetime.now(timezone.utc).isoformat()
    record.clarification_answers = json.dumps(body.answers)
    db.commit()

    # Merge answers into the partial structured_request
    partial = json.loads(record.structured_request)
    merged = {**partial, **body.answers}
    # Remove the field from missing_fields if the answer was provided
    merged["missing_fields"] = [
        f for f in merged.get("missing_fields", [])
        if f not in body.answers
    ]

    try:
        result = run_pipeline(
            record.raw_request,
            db,
            partial_structured=merged,
            parent_record_id=record_id,
        )
        # Mark the original as completed (linked to child)
        record.state = "completed"
        db.commit()
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        record.state = "clarification_needed"  # revert on error
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))


# ── Approve or reject an escalated decision ──────────────────────────────────

@router.post("/decision/{record_id}/approve")
def approve_decision(record_id: str, body: ApprovalRequest, db: Session = Depends(get_db)):
    """
    Procurement manager approves or rejects an escalated decision.
    Approve → state becomes completed (decision_type stays escalated, marked human-approved).
    Reject → state becomes completed, decision_type becomes rejected.
    """
    record = db.query(AuditRecord).filter(AuditRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    if record.state != "awaiting_approval":
        raise HTTPException(status_code=409, detail=f"Record state is '{record.state}', not awaiting_approval")

    # Check deadline
    from datetime import datetime, timezone
    if record.approval_deadline:
        deadline = datetime.fromisoformat(record.approval_deadline)
        if datetime.now(timezone.utc) > deadline:
            record.state = "abandoned"
            db.commit()
            raise HTTPException(status_code=410, detail="Approval deadline has passed — request abandoned")

    if body.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    # Record the approval/rejection
    record.approval_answered_at = datetime.now(timezone.utc).isoformat()
    record.approval_answers = json.dumps({"action": body.action, "reason": body.reason})
    record.approval_responder = body.responder_name

    if body.action == "approve":
        record.state = "completed"
        # decision_type stays "escalated" — it's human-approved escalation
    else:
        record.state = "completed"
        record.decision_type = "rejected"
        record.rejection_reason = body.reason or "Rejected by approver"

    db.commit()
    return _record_to_dict(record)


# ── Poll status ───────────────────────────────────────────────────────────────

@router.get("/decision/{record_id}/status")
def get_decision_status(record_id: str, db: Session = Depends(get_db)):
    """
    Lightweight poll endpoint. UI polls every 5 seconds.
    Returns state + questions (if clarification_needed) + decision summary (if completed).
    """
    record = db.query(AuditRecord).filter(AuditRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    base = {
        "record_id": record.id,
        "state": record.state or "completed",
        "decision_type": record.decision_type,
    }

    if record.state == "clarification_needed":
        base["questions"] = json.loads(record.clarification_questions) if record.clarification_questions else []
        base["clarification_deadline"] = record.clarification_deadline

    if record.state == "awaiting_approval":
        base["approval_questions"] = json.loads(record.approval_questions) if record.approval_questions else []
        base["approval_deadline"] = record.approval_deadline
        base["escalation_reason"] = record.escalation_reason

    if record.state in ("completed", None):
        base["ais_score"] = record.ais_score
        base["ais_grade"] = record.ais_grade
        base["recommended_supplier_name"] = record.recommended_supplier_name
        base["estimated_total_eur"] = record.estimated_total_eur
        base["risk_score"] = record.risk_score

    return base


# ── Get full decision + audit trail ──────────────────────────────────────────

@router.get("/decision/{record_id}")
def get_decision(record_id: str, db: Session = Depends(get_db)):
    record = db.query(AuditRecord).filter(AuditRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Decision not found")
    return _record_to_dict(record)


# ── Export audit document as JSON ─────────────────────────────────────────────

@router.get("/decision/{record_id}/export/json")
def export_audit_json(record_id: str, db: Session = Depends(get_db)):
    record = db.query(AuditRecord).filter(AuditRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Decision not found")
    data = _record_to_dict(record)
    content = json.dumps(data, indent=2).encode("utf-8")
    return StreamingResponse(
        BytesIO(content),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=audit_{record_id[:8]}.json"},
    )


# ── Export audit document as PDF ──────────────────────────────────────────────

@router.get("/decision/{record_id}/export/pdf")
def export_audit_pdf(record_id: str, db: Session = Depends(get_db)):
    record = db.query(AuditRecord).filter(AuditRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Decision not found")
    pdf_bytes = _generate_pdf(record)
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=audit_{record_id[:8]}.pdf"},
    )


# ── LLM call log for a decision (admin / audit inspector) ─────────────────────

@router.get("/decision/{record_id}/llm-calls")
def get_llm_calls(record_id: str, db: Session = Depends(get_db)):
    record = db.query(AuditRecord).filter(AuditRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Decision not found")
    logs = db.query(LLMCallLog).filter(LLMCallLog.record_id == record_id).all()
    return [
        {
            "id": l.id,
            "call_type": l.call_type,
            "model": l.model,
            "temperature": l.temperature,
            "system_prompt": l.system_prompt,
            "user_message": l.user_message,
            "extracted_result": l.extracted_result,
            "input_tokens": l.input_tokens,
            "output_tokens": l.output_tokens,
            "latency_ms": l.latency_ms,
            "timestamp": l.timestamp,
            "parse_method": l.parse_method,
        }
        for l in logs
    ]


# ── Recent decisions history ───────────────────────────────────────────────────

@router.get("/history")
def get_history(limit: int = 20, db: Session = Depends(get_db)):
    records = (
        db.query(AuditRecord)
        .order_by(AuditRecord.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_record_to_summary(r) for r in records]


# ── History aggregate stats (for pitch dashboard) ─────────────────────────────

@router.get("/history/stats")
def get_history_stats(db: Session = Depends(get_db)):
    records = db.query(AuditRecord).all()
    if not records:
        return {"total": 0, "escalation_rate": 0, "avg_ais": 0, "approved_rate": 0}

    total = len(records)
    escalated = sum(1 for r in records if r.decision_type == "escalated")
    approved = sum(1 for r in records if r.decision_type == "approved")
    avg_ais = sum(r.ais_score or 0 for r in records) / total

    return {
        "total": total,
        "approved": approved,
        "escalated": escalated,
        "rejected": total - approved - escalated,
        "escalation_rate": round(escalated / total * 100, 1),
        "approved_rate": round(approved / total * 100, 1),
        "avg_ais": round(avg_ais, 1),
    }


# ── Demo request scenarios from requests.json ──────────────────────────────────

@router.get("/demo-requests")
def get_demo_requests():
    from db.loaders import load_demo_requests
    requests = load_demo_requests()
    return [
        {
            "request_id": r.get("request_id", f"REQ-{i+1:04d}"),
            "title": r.get("title", "Untitled Request"),
            "request_text": r.get("request_text", ""),
            "scenario_tags": r.get("scenario_tags", []),
            "category_l1": r.get("category_l1", ""),
            "category_l2": r.get("category_l2", ""),
            "budget_amount": r.get("budget_amount"),
            "currency": r.get("currency", "EUR"),
            "country": r.get("country", ""),
            "business_unit": r.get("business_unit", ""),
            "quantity": r.get("quantity"),
            "unit_of_measure": r.get("unit_of_measure", ""),
            "required_by_date": r.get("required_by_date", ""),
            "preferred_supplier_mentioned": r.get("preferred_supplier_mentioned", ""),
            "delivery_countries": r.get("delivery_countries", []),
        }
        for i, r in enumerate(requests)
    ]


# ── Supplier list (for comparison view) ───────────────────────────────────────

@router.get("/suppliers")
def get_suppliers(category: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Supplier)
    if category:
        query = query.filter(Supplier.category == category)
    suppliers = query.limit(100).all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "category": s.category,
            "unit_price_eur": s.unit_price_eur,
            "delivery_days": s.delivery_days,
            "compliance_status": s.compliance_status,
            "esg_score": s.esg_score,
            "preferred_tier": s.preferred_tier,
            "contract_status": s.contract_status,
            "country": s.country,
            "eu_based": s.eu_based,
        }
        for s in suppliers
    ]


# ── Batch results (from scripts/run_batch.py) ────────────────────────────────

@router.get("/history/batch-stats")
def get_batch_stats():
    import pathlib
    path = pathlib.Path(__file__).parent.parent / "data" / "batch_results.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No batch results. Run scripts/run_batch.py first.")
    return json.loads(path.read_text())


# ── Reset DB to clean demo state ──────────────────────────────────────────────

# ── LLM provider toggle (claude ↔ openai) ─────────────────────────────────────

@router.get("/admin/llm-provider")
def get_llm_provider():
    from agent.llm_client import get_provider
    return {"provider": get_provider()}


@router.post("/admin/llm-provider/{provider}")
def set_llm_provider(provider: str):
    if provider not in ("claude", "openai", "azure_openai"):
        raise HTTPException(status_code=400, detail="Provider must be 'claude', 'openai', or 'azure_openai'")
    from agent.llm_client import set_provider
    set_provider(provider)
    return {"provider": provider, "message": f"Switched to {provider}"}


@router.post("/admin/seed-demo")
def seed_demo(db: Session = Depends(get_db)):
    """
    Delete all AuditRecord and LLMCallLog rows, then re-seed the DB from CSV/JSON.
    Suppliers and Rules are left untouched (they come from files, not demo runs).
    Call this before a live demo to start from a clean slate.
    """
    deleted_logs = db.query(LLMCallLog).delete()
    deleted_records = db.query(AuditRecord).delete()
    db.commit()

    # Re-run loaders (idempotent — suppliers/rules already exist, skips duplicates)
    try:
        from db.loaders import load_all
        load_all()
    except Exception as e:
        return {"status": "partial", "deleted_records": deleted_records, "deleted_logs": deleted_logs, "warning": str(e)}

    return {
        "status": "ok",
        "deleted_records": deleted_records,
        "deleted_llm_logs": deleted_logs,
        "message": "DB reset to clean demo state. Suppliers and rules intact.",
    }


# ── Abandon expired clarification requests (called by background job) ─────────

@router.post("/admin/expire-clarifications")
def expire_clarifications(db: Session = Depends(get_db)):
    """Marks all clarification_needed records past their deadline as abandoned."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    pending = db.query(AuditRecord).filter(
        AuditRecord.state == "clarification_needed",
        AuditRecord.clarification_deadline < now,
    ).all()
    for r in pending:
        r.state = "abandoned"
    db.commit()
    return {"abandoned": len(pending)}


@router.post("/admin/clear-history")
def clear_history(db: Session = Depends(get_db)):
    """Delete all audit records and LLM logs. Demo reset only."""
    db.query(LLMCallLog).delete()
    db.query(AuditRecord).delete()
    db.commit()
    return {"status": "cleared"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_escalated_to(r: AuditRecord) -> str | None:
    """Derive the approver name from policy results for escalated decisions."""
    if r.decision_type != "escalated":
        return None
    try:
        policy = json.loads(r.policy_results) if r.policy_results else {}
        # Check explicit escalation targets
        for esc in policy.get("escalations", []):
            if esc.get("escalate_to"):
                return esc["escalate_to"]
        # Fall back to fuzzy threshold approver
        ft = policy.get("fuzzy_threshold", {})
        if ft and ft.get("approver"):
            return ft["approver"]
    except Exception:
        pass
    # Last resort: budget-based
    try:
        structured = json.loads(r.structured_request) if r.structured_request else {}
        budget = structured.get("budget_eur", 0) or 0
        if budget > 5_000_000:
            return "CPO"
        elif budget > 500_000:
            return "Head of Strategic Sourcing"
        elif budget > 100_000:
            return "Head of Category"
        elif budget > 25_000:
            return "Procurement Manager"
    except Exception:
        pass
    return "Procurement Manager"


def _record_to_dict(r: AuditRecord) -> dict:
    return {
        "record_id": r.id,
        "created_at": r.created_at,
        "state": r.state or "completed",
        "agent_version": r.agent_version,
        "raw_request": r.raw_request,
        "structured_request": json.loads(r.structured_request),
        "policy_results": json.loads(r.policy_results),
        "supplier_results": json.loads(r.supplier_candidates),
        "scoring_result": json.loads(r.scored_suppliers),
        "decision": {
            "type": r.decision_type,
            "recommended_supplier_id": r.recommended_supplier_id,
            "recommended_supplier_name": r.recommended_supplier_name,
            "estimated_total_eur": r.estimated_total_eur,
            "confidence": r.confidence,
            "reasoning_narrative": r.reasoning_narrative,
            "escalation_reason": r.escalation_reason,
            "escalated_to": _resolve_escalated_to(r),
            "rejection_reason": r.rejection_reason,
        },
        "ais": {
            "score": r.ais_score,
            "grade": r.ais_grade,
            "components": json.loads(r.ais_components) if r.ais_components else {},
            "eu_ai_act_article_13_compliant": r.eu_ai_act_compliant,
        },
        "risk_score": r.risk_score,
        "pipeline_trace": json.loads(r.pipeline_trace) if r.pipeline_trace else [],
        "fuzzy_trace": json.loads(r.fuzzy_trace) if r.fuzzy_trace else None,
        "clarification": {
            "questions": json.loads(r.clarification_questions) if r.clarification_questions else [],
            "deadline": r.clarification_deadline,
            "answered_at": r.clarification_answered_at,
            "parent_record_id": r.parent_record_id,
        } if r.state in ("clarification_needed", "abandoned") else None,
        "approval": {
            "required": bool(r.approval_required),
            "questions": json.loads(r.approval_questions) if r.approval_questions else [],
            "deadline": r.approval_deadline,
            "answered_at": r.approval_answered_at,
            "answers": json.loads(r.approval_answers) if r.approval_answers else None,
            "responder": r.approval_responder,
        } if r.approval_required else None,
    }


def _record_to_summary(r: AuditRecord) -> dict:
    return {
        "record_id": r.id,
        "created_at": r.created_at,
        "state": r.state or "completed",
        "raw_request": r.raw_request[:100] + "..." if len(r.raw_request) > 100 else r.raw_request,
        "decision_type": r.decision_type,
        "recommended_supplier_name": r.recommended_supplier_name,
        "estimated_total_eur": r.estimated_total_eur,
        "ais_score": r.ais_score,
        "ais_grade": r.ais_grade,
        "risk_score": r.risk_score,
    }


def _generate_pdf(record: AuditRecord) -> bytes:
    """Generate a clean audit PDF using reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    # Header
    title_style = ParagraphStyle("title", parent=styles["Heading1"],
                                  textColor=colors.HexColor("#1a1a2e"), fontSize=18)
    story.append(Paragraph("AuditChain — Procurement Decision Audit Record", title_style))
    story.append(Spacer(1, 0.3*cm))

    badge_color = {
        "approved": colors.HexColor("#22c55e"),
        "escalated": colors.HexColor("#f59e0b"),
        "rejected": colors.HexColor("#ef4444"),
    }.get(record.decision_type, colors.grey)

    meta = [
        ["Record ID", record.id],
        ["Timestamp", record.created_at],
        ["Agent Version", record.agent_version],
        ["Decision", record.decision_type.upper()],
        ["AIS Score", f"{record.ais_score} / 100 — {record.ais_grade}"],
        ["Risk Score", f"{record.risk_score} / 100" if record.risk_score is not None else "N/A"],
        ["EU AI Act Art.13", "COMPLIANT" if record.eu_ai_act_compliant else "NON-COMPLIANT"],
    ]
    t = Table(meta, colWidths=[5*cm, 12*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("TEXTCOLOR", (1, 3), (1, 3), badge_color),
        ("FONTNAME", (1, 3), (1, 3), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.5*cm))

    story.append(Paragraph("Original Request", styles["Heading2"]))
    story.append(Paragraph(record.raw_request, styles["Normal"]))
    story.append(Spacer(1, 0.4*cm))

    if record.reasoning_narrative:
        story.append(Paragraph("Decision Reasoning", styles["Heading2"]))
        story.append(Paragraph(record.reasoning_narrative, styles["Normal"]))
        story.append(Spacer(1, 0.4*cm))

    if record.escalation_reason:
        story.append(Paragraph("Escalation Reason", styles["Heading2"]))
        story.append(Paragraph(record.escalation_reason, styles["Normal"]))
        story.append(Spacer(1, 0.4*cm))
    if record.rejection_reason:
        story.append(Paragraph("Rejection Reason", styles["Heading2"]))
        story.append(Paragraph(record.rejection_reason, styles["Normal"]))
        story.append(Spacer(1, 0.4*cm))

    if record.recommended_supplier_name:
        story.append(Paragraph("Recommended Supplier", styles["Heading2"]))
        supplier_data = [
            ["Name", record.recommended_supplier_name],
            ["Estimated Total (EUR)", f"€{record.estimated_total_eur:,.2f}" if record.estimated_total_eur else "N/A"],
            ["Confidence", f"{int((record.confidence or 0) * 100)}%"],
        ]
        st = Table(supplier_data, colWidths=[5*cm, 12*cm])
        st.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0fdf4")),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(st)
        story.append(Spacer(1, 0.4*cm))

    story.append(Paragraph("Decision Quality Score Breakdown", styles["Heading2"]))
    ais_components = json.loads(record.ais_components) if record.ais_components else {}
    max_pts = {"request_completeness": 20, "policy_coverage": 15, "traceability": 25,
                "supplier_justification": 20, "decision_correctness": 20}
    ais_data = [["Component", "Score", "Max"]] + [
        [k.replace("_", " ").title(), str(v), str(max_pts.get(k, 20))]
        for k, v in ais_components.items()
    ]
    at = Table(ais_data, colWidths=[9*cm, 4*cm, 4*cm])
    at.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(at)
    story.append(Spacer(1, 0.4*cm))

    # Pipeline trace
    if record.pipeline_trace:
        trace = json.loads(record.pipeline_trace)
        story.append(Paragraph("Pipeline Execution Trace", styles["Heading2"]))
        trace_data = [["Step", "Duration (ms)", "LLM", "Summary"]] + [
            [s["step"], str(s["ms"]), "✓" if s["llm"] else "—", s["summary"][:60]]
            for s in trace
        ]
        tt = Table(trace_data, colWidths=[3.5*cm, 3*cm, 1.5*cm, 9*cm])
        tt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(tt)
        story.append(Spacer(1, 0.4*cm))

    footer_style = ParagraphStyle("footer", parent=styles["Normal"],
                                   fontSize=8, textColor=colors.grey)
    story.append(Paragraph(
        f"This document is an auto-generated audit record. "
        f"Record ID: {record.id} | Generated by AuditChain v{record.agent_version}",
        footer_style,
    ))

    doc.build(story)
    return buffer.getvalue()
