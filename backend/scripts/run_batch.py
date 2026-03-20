#!/usr/bin/env python3
"""
Batch runner — feeds all 304 requests from requests.json through the pipeline.
Generates summary statistics and saves results to data/batch_results.json.

Usage:
    cd solution/backend
    source .venv/bin/activate
    python scripts/run_batch.py
"""
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Fix import path ────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

# Load .env before any other imports
from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env", override=True)

# Use OpenAI for batch (cheaper) unless explicitly overridden with --claude
if "--claude" not in sys.argv:
    os.environ["LLM_PROVIDER"] = "openai"

from db.database import init_db, SessionLocal
from db.seed import seed
from db.loaders import load_demo_requests, load_all
from db.models import AuditRecord
from agent.pipeline import run_pipeline
from agent.llm_client import set_provider, get_provider


def auto_fill_answers(req: dict) -> dict:
    """Build clarification answers from request JSON metadata."""
    answers = {}
    if req.get("budget_amount"):
        answers["budget_eur"] = float(req["budget_amount"])
    if req.get("quantity"):
        answers["quantity"] = int(req["quantity"])
    if req.get("required_by_date"):
        try:
            target = datetime.fromisoformat(req["required_by_date"].replace("Z", "+00:00"))
            days = (target - datetime.now(timezone.utc)).days
            answers["deadline_days"] = max(1, days)
        except Exception:
            answers["deadline_days"] = 14
    if req.get("category_l2"):
        answers["item_description"] = f"{req.get('quantity', '')} {req['category_l2']}".strip()
    return answers


def _save_partial(results, start_time, backend_dir):
    """Save partial results for crash recovery."""
    import time as _t
    partial = {
        "total_requests": len(results),
        "total_time_s": round(_t.time() - start_time, 1),
        "partial": True,
        "results": results,
    }
    output_path = backend_dir / "data" / "batch_results.json"
    output_path.write_text(json.dumps(partial, indent=2))


def run_batch():
    # Force provider switch at runtime (module-level var may have been set at import time)
    if "--claude" not in sys.argv:
        set_provider("openai")
    print(f"═══ AuditChain Batch Runner ═══  (LLM: {get_provider()})")
    print()

    # Initialize DB
    print("Initializing database...")
    init_db()
    seed()
    load_all()

    # Clean previous batch runs (keep supplier/rule data)
    db = SessionLocal()
    deleted = db.query(AuditRecord).delete()
    db.commit()
    if deleted:
        print(f"Cleared {deleted} previous records.")

    # Load requests
    requests = load_demo_requests()
    total = len(requests)
    print(f"Loaded {total} requests from requests.json")
    print()

    # Process each request
    results = []
    errors = []
    start_time = time.time()
    DELAY_BETWEEN = 1  # seconds — retry logic handles 429s, minimal delay for burst prevention

    def _run_with_retry(fn, max_retries=3):
        """Retry on rate-limit (429) with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return fn()
            except Exception as e:
                if "429" in str(e) or "rate_limit" in str(e).lower() or "quota" in str(e).lower() or "resource_exhausted" in str(e).lower():
                    wait = (2 ** attempt) * 10  # 10s, 20s, 40s
                    print(f"    ⏳ Rate limited, waiting {wait}s (attempt {attempt+1}/{max_retries})...")
                    time.sleep(wait)
                else:
                    raise
        return fn()  # final attempt, let it raise

    for i, req in enumerate(requests):
        req_id = req.get("request_id", f"REQ-{i+1:06d}")
        tags = req.get("scenario_tags", ["untagged"])
        tag_str = ",".join(tags)
        t0 = time.time()

        try:
            result = _run_with_retry(lambda: run_pipeline(
                raw_request=req["request_text"],
                db=db,
            ))

            state = result.get("state", "unknown")
            decision_type = None
            ais_score = None
            risk_score = None
            confidence = None
            supplier_name = None

            # If clarification needed, auto-fill and retry
            if state == "clarification_needed":
                answers = auto_fill_answers(req)
                if answers:
                    record_id = result["record_id"]
                    # Mark original as processing
                    record = db.query(AuditRecord).filter(AuditRecord.id == record_id).first()
                    if record:
                        record.state = "processing"
                        record.clarification_answered_at = datetime.now(timezone.utc).isoformat()
                        record.clarification_answers = json.dumps(answers)
                        db.commit()

                    # Merge answers into structured request
                    partial = json.loads(record.structured_request) if record else {}
                    merged = {**partial, **answers}
                    merged["missing_fields"] = [
                        f for f in merged.get("missing_fields", [])
                        if f not in answers
                    ]

                    time.sleep(DELAY_BETWEEN)
                    result2 = _run_with_retry(lambda: run_pipeline(
                        raw_request=req["request_text"],
                        db=db,
                        partial_structured=merged,
                        parent_record_id=record_id,
                    ))

                    if record:
                        record.state = "completed"
                        db.commit()

                    state = result2.get("state", "clarified")
                    decision_type = result2.get("decision", {}).get("decision_type")
                    ais_score = result2.get("ais", {}).get("score")
                    risk_score = result2.get("risk_score", {}).get("score") if isinstance(result2.get("risk_score"), dict) else result2.get("risk_score")
                    confidence = result2.get("decision", {}).get("confidence")
                    rec_sup = result2.get("decision", {}).get("recommended_supplier")
                    supplier_name = rec_sup.get("name") if isinstance(rec_sup, dict) else None
                    state = "clarified→" + (decision_type or "completed")
                else:
                    state = "clarification_needed (no auto-fill)"

            elif state == "completed":
                decision_type = result.get("decision", {}).get("decision_type")
                ais_score = result.get("ais", {}).get("score")
                risk_score = result.get("risk_score", {}).get("score") if isinstance(result.get("risk_score"), dict) else result.get("risk_score")
                confidence = result.get("decision", {}).get("confidence")
                rec_sup = result.get("decision", {}).get("recommended_supplier")
                supplier_name = rec_sup.get("name") if isinstance(rec_sup, dict) else None

            elif state == "awaiting_approval":
                decision_type = result.get("decision", {}).get("decision_type")
                ais_score = result.get("ais", {}).get("score")
                risk_score = result.get("risk_score", {}).get("score") if isinstance(result.get("risk_score"), dict) else result.get("risk_score")
                confidence = result.get("decision", {}).get("confidence")
                rec_sup = result.get("decision", {}).get("recommended_supplier")
                supplier_name = rec_sup.get("name") if isinstance(rec_sup, dict) else None

            elapsed = time.time() - t0
            entry = {
                "request_id": req_id,
                "scenario_tags": tags,
                "state": state,
                "decision_type": decision_type,
                "ais_score": ais_score,
                "risk_score": risk_score,
                "confidence": confidence,
                "supplier_name": supplier_name,
                "elapsed_s": round(elapsed, 2),
            }
            results.append(entry)

            # Progress line
            ais_str = f"AIS {ais_score}" if ais_score else ""
            dec_str = (decision_type or state).upper()
            print(f"  [{i+1:3d}/{total}] {req_id} ({tag_str:20s}) → {dec_str:12s} {ais_str:8s} {elapsed:.1f}s")

        except Exception as e:
            elapsed = time.time() - t0
            errors.append({"request_id": req_id, "error": str(e)})
            results.append({
                "request_id": req_id,
                "scenario_tags": tags,
                "state": "error",
                "decision_type": None,
                "ais_score": None,
                "risk_score": None,
                "confidence": None,
                "supplier_name": None,
                "elapsed_s": round(elapsed, 2),
                "error": str(e),
            })
            print(f"  [{i+1:3d}/{total}] {req_id} ({tag_str:20s}) → ERROR: {str(e)[:60]}")

        # Rate-limit delay between requests
        if i < total - 1:
            time.sleep(DELAY_BETWEEN)

        # Incremental save every 25 requests
        if (i + 1) % 25 == 0:
            _save_partial(results, start_time, BACKEND_DIR)
            print(f"  --- checkpoint saved ({i+1}/{total}) ---")

    db.close()
    total_time = time.time() - start_time

    # ── Summary ──────────────────────────────────────────────────────────────────
    print()
    print("═" * 60)
    print(f"  BATCH COMPLETE: {total} requests in {total_time:.0f}s")
    print("═" * 60)
    print()

    # Group by scenario tag
    tag_results = defaultdict(list)
    for r in results:
        for tag in r["scenario_tags"]:
            tag_results[tag].append(r)

    for tag in sorted(tag_results.keys()):
        entries = tag_results[tag]
        n = len(entries)
        decision_counts = Counter(e.get("decision_type") for e in entries if e.get("decision_type"))
        state_counts = Counter(e.get("state") for e in entries)
        approved = decision_counts.get("approved", 0)
        escalated = decision_counts.get("escalated", 0)
        rejected = decision_counts.get("rejected", 0)
        clarified = sum(1 for e in entries if "clarified" in (e.get("state") or ""))
        err = sum(1 for e in entries if e.get("state") == "error")

        parts = []
        if approved:
            parts.append(f"{approved} approved")
        if escalated:
            parts.append(f"{escalated} escalated")
        if rejected:
            parts.append(f"{rejected} rejected")
        if clarified:
            parts.append(f"{clarified} clarified")
        if err:
            parts.append(f"{err} errors")

        print(f"  {tag:20s} ({n:3d}):  {', '.join(parts)}")

    # Aggregate stats
    valid = [r for r in results if r.get("ais_score") is not None]
    avg_ais = sum(r["ais_score"] for r in valid) / len(valid) if valid else 0
    risk_valid = [r for r in results if r.get("risk_score") is not None]
    avg_risk = sum(r["risk_score"] for r in risk_valid) / len(risk_valid) if risk_valid else 0
    avg_time = sum(r["elapsed_s"] for r in results) / len(results) if results else 0

    print()
    print(f"  Avg AIS: {avg_ais:.1f}  |  Avg Risk: {avg_risk:.1f}  |  Avg Latency: {avg_time:.1f}s")
    print(f"  Errors: {len(errors)}/{total}")
    print()

    # Build summary object
    summary = {
        "total_requests": total,
        "total_time_s": round(total_time, 1),
        "avg_latency_s": round(avg_time, 2),
        "avg_ais": round(avg_ais, 1),
        "avg_risk": round(avg_risk, 1),
        "errors": len(errors),
        "by_scenario": {},
        "by_decision": dict(Counter(r.get("decision_type") for r in results if r.get("decision_type"))),
        "results": results,
    }

    for tag in sorted(tag_results.keys()):
        entries = tag_results[tag]
        dec_counts = Counter(e.get("decision_type") for e in entries if e.get("decision_type"))
        summary["by_scenario"][tag] = {
            "count": len(entries),
            "approved": dec_counts.get("approved", 0),
            "escalated": dec_counts.get("escalated", 0),
            "rejected": dec_counts.get("rejected", 0),
            "clarified": sum(1 for e in entries if "clarified" in (e.get("state") or "")),
            "errors": sum(1 for e in entries if e.get("state") == "error"),
        }

    # Save results
    output_path = BACKEND_DIR / "data" / "batch_results.json"
    output_path.write_text(json.dumps(summary, indent=2))
    print(f"  Results saved to: {output_path}")
    print()


if __name__ == "__main__":
    run_batch()
