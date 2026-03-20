"""
Phase 1 Pre-validation — Wrapper for Casimir's 4 modules.

Runs integrity, parser, completeness, and normalization checks
as an additive validation layer BEFORE the main pipeline parse.

Gated by OPENAI_API_KEY — if not set, returns skipped result.
Does NOT block the pipeline on failure — graceful degradation.
"""
from __future__ import annotations
import os
import time


def run_phase1_validation(raw_text: str, requester_context: dict | None = None) -> dict:
    """
    Orchestrate Casimir's 4 Phase 1 modules.

    Returns:
    {
        "passed": bool,
        "skipped": bool,
        "integrity": {...} | None,
        "parse": {...} | None,
        "completeness": {...} | None,
        "normalization": {...} | None,
        "blocking_issues": [...],
        "warnings": [...],
        "timing_ms": int
    }
    """
    if not os.getenv("OPENAI_API_KEY"):
        return {"passed": True, "skipped": True, "blocking_issues": [], "warnings": []}

    t0 = time.time()
    ctx = requester_context or {}
    blocking_issues = []
    warnings = []

    # Build the request dict that Casimir's modules expect
    request = {
        "request_text": raw_text,
        "created_at": "",  # not critical for validation
        "delivery_countries": [ctx.get("country")] if ctx.get("country") else [],
        "incumbent_supplier": None,
    }

    # ── 1. Integrity check ─────────────────────────────────────────────
    integrity_result = None
    try:
        from agent.integrity import run as integrity_run
        integrity_result = integrity_run(request)
        if integrity_result.get("status") == "escalate":
            esc = integrity_result.get("escalation", {})
            blocking_issues.append(f"Integrity: {esc.get('reason', 'Failed integrity check')}")
        elif integrity_result.get("issues"):
            warnings.extend(f"Integrity: {i}" for i in integrity_result["issues"])
    except Exception as e:
        warnings.append(f"Integrity check failed: {e}")

    # ── 2. Parser (OpenAI extraction) ──────────────────────────────────
    parse_result = None
    try:
        from agent.parser import run as parser_run
        parse_result = parser_run(request)
    except Exception as e:
        warnings.append(f"Phase 1 parser failed: {e}")

    # ── 3. Completeness check (pure Python, no LLM) ───────────────────
    completeness_result = None
    if parse_result:
        try:
            from agent.completeness import run as completeness_run
            completeness_result = completeness_run(request, parse_result)
            if completeness_result.get("status") == "escalate":
                missing = completeness_result.get("missing_fields", [])
                warnings.append(f"Completeness: missing {', '.join(missing)}")
        except Exception as e:
            warnings.append(f"Completeness check failed: {e}")

    # ── 4. Normalization / cross-validation ────────────────────────────
    normalization_result = None
    if parse_result:
        try:
            from agent.normalization import run as normalization_run
            normalization_result = normalization_run(request, parse_result)
            if normalization_result.get("status") == "escalate":
                conflicts = normalization_result.get("conflicts", [])
                blocking_issues.append(f"Normalization: {'; '.join(conflicts)}")
        except Exception as e:
            warnings.append(f"Normalization check failed: {e}")

    elapsed_ms = int((time.time() - t0) * 1000)

    return {
        "passed": len(blocking_issues) == 0,
        "skipped": False,
        "integrity": integrity_result,
        "parse": parse_result,
        "completeness": completeness_result,
        "normalization": normalization_result,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "timing_ms": elapsed_ms,
    }
