"""
Deterministic procurement tool implementations.
LLM is only used for parse_request() and generate_narrative().
All policy/scoring/AIS logic is pure Python — fully auditable.
"""
from __future__ import annotations
import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional
from sqlalchemy.orm import Session
from db.models import Supplier, Rule, PricingTier, HistoricalAward


# ── Category weights for supplier scoring ───────────────────────────────────
CATEGORY_WEIGHTS: dict[str, dict[str, float]] = {
    "hardware":   {"price": 0.40, "delivery": 0.25, "compliance": 0.20, "esg": 0.15},
    "software":   {"price": 0.30, "delivery": 0.10, "compliance": 0.35, "esg": 0.25},
    "services":   {"price": 0.25, "delivery": 0.30, "compliance": 0.25, "esg": 0.20},
    "facilities": {"price": 0.35, "delivery": 0.25, "compliance": 0.20, "esg": 0.20},
    "default":    {"price": 0.35, "delivery": 0.25, "compliance": 0.25, "esg": 0.15},
}

GDPR_SENSITIVE_CATEGORIES = {"software", "services"}

_DATA_DIR = Path(__file__).parent.parent / "data"


@lru_cache(maxsize=1)
def _load_policies() -> dict:
    """Load policies.json once and cache. Returns {} if not found."""
    p = _DATA_DIR / "policies.json"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _get_approval_threshold(budget: float, currency: str = "EUR") -> dict | None:
    """
    Return the matching approval threshold dict from policies.json.
    Thresholds: AT-001 (0–25k), AT-002 (25k–100k), AT-003 (100k–500k),
                AT-004 (500k–5M), AT-005 (5M+).
    """
    policies = _load_policies()
    for tier in policies.get("approval_thresholds", []):
        if tier.get("currency") != currency:
            continue
        lo = tier.get("min_amount", 0)
        hi = tier.get("max_amount", float("inf"))
        if lo <= budget <= hi:
            return tier
    return None


def _conditional_restriction_check(supplier_id: str, category_l2: str, delivery_country: str, budget: float) -> str | None:
    """
    Check policies.json restricted_suppliers for conditional restrictions.
    Returns restriction_reason if applicable, else None.
    The is_restricted flag in suppliers.csv is just a hint — always check here.
    """
    policies = _load_policies()
    for rs in policies.get("restricted_suppliers", []):
        if rs.get("supplier_id") != supplier_id:
            continue
        # Category must match (if specified)
        if rs.get("category_l2") and rs.get("category_l2") != category_l2:
            continue
        scope = rs.get("restriction_scope", [])
        reason = rs.get("restriction_reason", "Policy restriction")

        # scope = ["all"] → value-conditional: check notes for threshold
        if "all" in scope:
            # SUP-0045: "Can be used only below EUR 75000 without exception approval"
            if "75000" in reason and budget >= 75_000:
                return f"{reason} (budget €{budget:,.0f} ≥ €75,000 threshold)"
            elif "75000" not in reason:
                return reason
            continue

        # scope = list of country codes
        if delivery_country and delivery_country.upper() in [c.upper() for c in scope]:
            return f"{reason} (restriction applies in {delivery_country})"
        elif not delivery_country and scope:
            # No delivery country known — flag as warning (potential restriction)
            return f"{reason} (restriction may apply — delivery country unknown)"

    return None


# ── Policy Engine ────────────────────────────────────────────────────────────

def check_policy(structured_request: dict, rules: list[Rule]) -> dict:
    """
    Run all active rules against the structured request.
    Returns violations (block), warnings, escalations.
    All logic is deterministic — no LLM involved.

    Key rules:
    - Tiered budget thresholds: AT-001 (0–25k), AT-002 (25k–100k), AT-003 (100k–500k),
      AT-004 (500k–5M), AT-005 (5M+)
    - R05: emergency timeline < 3 days → escalate
    - R06: ESG < 60 → disqualify in supplier filter
    - R07: GDPR sensitive + non-EU → disqualify in supplier filter
    - Conditional restrictions: checked per-supplier in query_suppliers()
    - Category/geo mismatch for preferred supplier: logged here as warning
    """
    violations, warnings, escalations = [], [], []

    budget = structured_request.get("budget_eur", 0) or 0
    deadline_days = structured_request.get("deadline_days") or 999
    category = structured_request.get("category", "default")
    delivery_country = (structured_request.get("delivery_country") or "").upper()
    gdpr_sensitive = category in GDPR_SENSITIVE_CATEGORIES

    rule_map = {r.id: r for r in rules if r.active}

    def _make_result(rule_id: str, rule_name: str, description: str,
                     action: str, detail: str, escalate_to: str | None = None) -> dict:
        return {
            "rule_id": rule_id,
            "rule_name": rule_name,
            "description": description,
            "triggered": True,
            "action": action,
            "detail": detail,
            "escalate_to": escalate_to,
        }

    preferred_supplier_name = (structured_request.get("preferred_supplier_name") or "").lower()

    # ── Tiered budget approval thresholds ────────────────────────────────────
    # AT-001: 0–25k    → business only, 1 quote, no escalation
    # AT-002: 25k–100k → business+procurement, 2 quotes, Procurement Manager deviation
    # AT-003: 100k–500k → procurement, 3 quotes, Head of Category
    # AT-004: 500k–5M  → procurement, 3 quotes, Head of Strategic Sourcing
    # AT-005: 5M+      → procurement, 3 quotes, CPO
    if budget > 0:
        if budget >= 5_000_000:
            escalations.append(_make_result(
                "AT-005", "Budget: CPO Approval Required",
                "Purchases above €5,000,000 require CPO approval",
                "escalate",
                f"Budget €{budget:,.0f} exceeds €5M threshold — CPO sign-off required with minimum 3 supplier quotes",
                "CPO"
            ))
        elif budget >= 500_000:
            escalations.append(_make_result(
                "AT-004", "Budget: Head of Strategic Sourcing Approval",
                "Purchases €500k–€5M require Head of Strategic Sourcing approval",
                "escalate",
                f"Budget €{budget:,.0f} falls in €500k–€5M range — Head of Strategic Sourcing approval required, minimum 3 quotes",
                "Head of Strategic Sourcing"
            ))
        elif budget >= 100_000:
            warnings.append(_make_result(
                "AT-003", "Budget: Head of Category Involvement",
                "Purchases €100k–€500k involve Head of Category review",
                "warn",
                f"Budget €{budget:,.0f} in €100k–€500k range — Head of Category review recommended, minimum 3 supplier quotes",
                "Head of Category"
            ))
        elif budget >= 25_000:
            # Warning only for AT-002 (doesn't block, but requires Procurement Manager for deviations)
            warnings.append(_make_result(
                "AT-002", "Budget: Procurement Manager Involvement",
                "Purchases €25k–€100k involve procurement and require minimum 2 supplier quotes",
                "warn",
                f"Budget €{budget:,.0f} in €25k–€100k range — involves business + procurement management; Procurement Manager approval required for supplier deviations",
                "Procurement Manager"
            ))

    # ── R05: Emergency timeline ───────────────────────────────────────────────
    if r := rule_map.get("R05"):
        if 0 < deadline_days < 3:
            escalations.append(_make_result(
                "R05", r.name, r.description, "escalate",
                f"Deadline of {deadline_days} business day(s) triggers emergency procurement process",
                r.escalate_to or "Head of Category"
            ))

    # ── Restricted/preferred supplier check ──────────────────────────────────
    preferred_supplier_name_raw = structured_request.get("preferred_supplier_name") or ""
    if preferred_supplier_name_raw:
        from db.database import SessionLocal
        _db = SessionLocal()
        try:
            # Find the preferred supplier in DB
            pref_suppliers = _db.query(Supplier).filter(
                Supplier.name.ilike(f"%{preferred_supplier_name_raw}%")
            ).all()

            if pref_suppliers:
                ps = pref_suppliers[0]
                req_category = category
                req_category_l2 = (structured_request.get("category_l2") or "").strip()

                # Category mismatch: preferred supplier registered under different category
                if ps.category != req_category:
                    warnings.append(_make_result(
                        "W-CAT-MISMATCH", "Preferred Supplier Category Mismatch",
                        "Preferred supplier is registered under a different procurement category",
                        "warn",
                        f"'{preferred_supplier_name_raw}' is registered under '{ps.category}' "
                        f"but this request is for '{req_category}'. "
                        f"The supplier preference will be disregarded; compliant suppliers in '{req_category}' will be evaluated instead.",
                    ))
                    # Mark preference as discarded
                    structured_request["preferred_supplier_discarded"] = True
                    structured_request["preferred_supplier_discard_reason"] = "category_mismatch"

                # Geographic mismatch: preferred supplier doesn't serve delivery country
                elif delivery_country and ps.service_regions:
                    regions = [r.strip().upper() for r in (ps.service_regions or "").split(";") if r.strip()]
                    if regions and delivery_country not in regions:
                        warnings.append(_make_result(
                            "W-GEO-MISMATCH", "Preferred Supplier Geographic Mismatch",
                            "Preferred supplier does not cover the delivery country",
                            "warn",
                            f"'{preferred_supplier_name_raw}' serves regions: {', '.join(regions)}. "
                            f"Delivery country '{delivery_country}' is not covered. "
                            f"The supplier preference will be disregarded.",
                        ))
                        structured_request["preferred_supplier_discarded"] = True
                        structured_request["preferred_supplier_discard_reason"] = "geographic_mismatch"

                # Conditional restriction check for preferred supplier
                if not structured_request.get("preferred_supplier_discarded"):
                    # Extract supplier_id (strip category suffix from composite ID)
                    base_supplier_id = ps.id.split("_")[0] if "_" in ps.id else ps.id
                    restriction = _conditional_restriction_check(
                        base_supplier_id,
                        ps.category_l2 or "",
                        delivery_country,
                        budget,
                    )
                    if restriction:
                        if r := rule_map.get("R03"):
                            violations.append(_make_result(
                                "R03", r.name, r.description, "block",
                                f"Preferred supplier '{preferred_supplier_name_raw}' is conditionally restricted: {restriction}",
                                "Procurement Manager"
                            ))
                        else:
                            violations.append(_make_result(
                                "RS-CONDITIONAL", "Conditional Supplier Restriction",
                                "Supplier has a policy-defined conditional restriction",
                                "block",
                                f"'{preferred_supplier_name_raw}': {restriction}",
                                "Procurement Manager"
                            ))

                # Hard blocked in DB
                if ps.compliance_status == "blocked" and not structured_request.get("preferred_supplier_discarded"):
                    if r := rule_map.get("R03"):
                        violations.append(_make_result(
                            "R03", r.name, r.description, "block",
                            f"Preferred supplier '{preferred_supplier_name_raw}' is on the compliance hold list",
                            r.escalate_to or "Procurement Manager"
                        ))
        finally:
            _db.close()

    # ── Spending authority check ──────────────────────────────────────────────
    # If requester's spending authority is known and budget exceeds it → escalate
    # even if budget is below the €100k AT-003 threshold
    spending_authority = structured_request.get("_spending_authority_eur") or float("inf")
    if budget > 0 and budget > spending_authority and spending_authority < float("inf"):
        # Only add if not already covered by a higher threshold escalation
        existing_threshold_ids = {e["rule_id"] for e in escalations}
        if not any(t in existing_threshold_ids for t in ("AT-003", "AT-004", "AT-005")):
            escalations.append(_make_result(
                "AT-AUTHORITY", "Spending Authority Exceeded",
                "Purchase exceeds requester's individual spending authority",
                "escalate",
                f"Budget €{budget:,.0f} exceeds requester spending authority of €{spending_authority:,.0f} — "
                f"requires Procurement Manager approval before award",
                "Procurement Manager"
            ))

    # ── Missing required fields → escalate for clarification ─────────────────
    missing_fields = structured_request.get("missing_fields", [])
    if missing_fields and (r := rule_map.get("ER-001")):
        escalations.append(_make_result(
            "ER-001", r.name, r.description, "escalate",
            f"Missing required information: {', '.join(missing_fields)}",
            "Requester Clarification"
        ))

    # ── Store computed values for downstream steps ────────────────────────────
    structured_request["_gdpr_sensitive"] = gdpr_sensitive
    structured_request["_budget"] = budget
    structured_request["_deadline_days"] = deadline_days
    structured_request["_preferred_supplier_name"] = preferred_supplier_name
    structured_request["_delivery_country"] = delivery_country

    return {
        "violations": violations,   # hard blocks — stop processing
        "warnings": warnings,
        "escalations": escalations,
        "all_clear": len(violations) == 0 and len(escalations) == 0,
        "gdpr_sensitive": gdpr_sensitive,
    }


# ── Supplier Filtering ───────────────────────────────────────────────────────

def query_suppliers(structured_request: dict, db: Session) -> dict:
    """
    Filter suppliers by category + eligibility rules.
    Returns candidates (eligible) and disqualified (with reasons).

    Checks applied per-supplier:
    - R03: compliance_status == "blocked"
    - R06: ESG score < 60
    - R07: GDPR — non-EU supplier in software/services category
    - R10: Spot vendor for high-value purchases (> €50k)
    - Geographic: delivery_country not in service_regions
    - Conditional restrictions: policies.json per country/value
    - Minimum order quantity exceeded
    """
    category = structured_request.get("category", "default")
    gdpr_sensitive = structured_request.get("_gdpr_sensitive", False)
    budget = structured_request.get("_budget", 0)
    quantity = structured_request.get("quantity", 1) or 1
    delivery_country = (structured_request.get("_delivery_country") or
                        structured_request.get("delivery_country") or "").upper()
    category_l2 = structured_request.get("category_l2") or ""

    # Filter by category_l2 subcategory when available — avoids mixing e.g. Office Chairs with Meeting Room Furniture
    q = db.query(Supplier).filter(Supplier.category == category)
    if category_l2:
        matching_l2 = q.filter(Supplier.category_l2 == category_l2).all()
        all_suppliers = matching_l2 if matching_l2 else q.all()  # fall back to full category if no match
    else:
        all_suppliers = q.all()

    candidates = []
    disqualified = []

    for s in all_suppliers:
        reasons = []

        # R03: Hard blocked
        if s.compliance_status == "blocked":
            reasons.append("R03: Supplier is on the compliance hold list")

        # R06: ESG minimum
        if s.esg_score < 60:
            reasons.append(f"R06: ESG score {s.esg_score} below minimum threshold of 60")

        # R07: GDPR — non-EU for sensitive categories
        if gdpr_sensitive and not s.eu_based:
            reasons.append(f"R07: Non-EU supplier not permitted for GDPR-sensitive category '{category}'")

        # R10: Spot vendor for high-value
        if s.preferred_tier == "spot" and budget > 50_000:
            reasons.append(f"R10: Spot vendor cannot be used for purchases above €50,000 (budget: €{budget:,.0f})")

        # Minimum order quantity
        if s.min_quantity > quantity:
            reasons.append(f"Minimum order quantity {s.min_quantity} units exceeds requested {quantity} units")

        # ER-006: Capacity check — supplier monthly capacity vs requested quantity
        if hasattr(s, 'capacity_per_month') and s.capacity_per_month and quantity > s.capacity_per_month:
            reasons.append(
                f"ER-006: Requested {quantity} units exceeds supplier monthly capacity of "
                f"{s.capacity_per_month} units — Sourcing Excellence Lead escalation required"
            )

        # ER-005: Data residency — check if supplier supports data residency when required
        data_residency = structured_request.get("data_residency_required", False)
        if data_residency and hasattr(s, 'data_residency_supported') and not s.data_residency_supported:
            reasons.append(
                f"ER-005: Data residency required but supplier does not support in-country data storage "
                f"— Security/Compliance escalation required"
            )

        # Geographic coverage: check service_regions
        if delivery_country and s.service_regions:
            regions = [r.strip().upper() for r in s.service_regions.split(";") if r.strip()]
            if regions and delivery_country not in regions:
                reasons.append(
                    f"Geographic: Supplier serves {', '.join(regions)} — "
                    f"does not cover delivery country '{delivery_country}'"
                )

        # Conditional restriction from policies.json
        if not any("R03" in r for r in reasons):  # skip if already hard-blocked
            base_supplier_id = s.id.split("_")[0] if "_" in s.id else s.id
            restriction = _conditional_restriction_check(
                base_supplier_id,
                s.category_l2 or category_l2 or "",
                delivery_country,
                budget,
            )
            if restriction:
                reasons.append(f"Conditional restriction: {restriction}")

        supplier_dict = {
            "id": s.id,
            "supplier_id": s.id.split("_")[0] if "_" in s.id else s.id,
            "name": s.name,
            "category": s.category,
            "category_l2": s.category_l2 or "",
            "unit_price_eur": s.unit_price_eur,
            "min_quantity": s.min_quantity,
            "delivery_days": s.delivery_days,
            "compliance_status": s.compliance_status,
            "esg_score": s.esg_score,
            "preferred_tier": s.preferred_tier,
            "contract_status": s.contract_status,
            "country": s.country,
            "service_regions": s.service_regions or "",
            "eu_based": s.eu_based,
            "data_residency_supported": s.data_residency_supported or False,
            "notes": s.notes or "",
        }

        if reasons:
            supplier_dict["disqualified"] = True
            supplier_dict["disqualification_reasons"] = reasons
            disqualified.append(supplier_dict)
        else:
            supplier_dict["disqualified"] = False
            supplier_dict["disqualification_reasons"] = []
            candidates.append(supplier_dict)

    # ── Infeasibility check ──────────────────────────────────────────────────
    infeasibility = None
    if budget > 0 and candidates:
        eligible_costs = [s["unit_price_eur"] * quantity for s in candidates]
        min_feasible_cost = min(eligible_costs)
        if min_feasible_cost > budget:
            cheapest_unit = min(s["unit_price_eur"] for s in candidates)
            max_affordable_qty = int(budget / cheapest_unit)
            infeasibility = {
                "infeasible": True,
                "reason": (
                    f"Minimum cost for {quantity} units is €{min_feasible_cost:,.0f}, "
                    f"exceeding budget of €{budget:,.0f}. "
                    f"Maximum affordable quantity at this budget: {max_affordable_qty} units."
                ),
                "min_cost_eur": round(min_feasible_cost, 2),
                "cheapest_unit_eur": round(cheapest_unit, 2),
                "max_affordable_qty": max_affordable_qty,
            }

    # No suppliers at all for this category → explicit message
    no_suppliers_reason = None
    if not all_suppliers:
        no_suppliers_reason = f"No suppliers found in category '{category}'" + (f" / '{category_l2}'" if category_l2 else "")
    elif not candidates and not infeasibility:
        no_suppliers_reason = f"All {len(all_suppliers)} suppliers in '{category}' were disqualified"

    return {
        "candidates": candidates,
        "disqualified": disqualified,
        "total_found": len(all_suppliers),
        "total_eligible": len(candidates),
        "infeasibility": infeasibility,
        "no_suppliers_reason": no_suppliers_reason,
    }


# ── Volume Pricing ───────────────────────────────────────────────────────────

def _get_volume_price(supplier_id: str, quantity: int, db: Session) -> float | None:
    """
    Look up the correct unit price from PricingTier for the given quantity.
    Returns None if no tier found (fall back to Supplier.unit_price_eur).
    """
    tiers = (
        db.query(PricingTier)
        .filter(PricingTier.supplier_id == supplier_id)
        .order_by(PricingTier.min_quantity.asc())
        .all()
    )
    if not tiers:
        return None

    applicable = None
    for tier in tiers:
        if quantity >= tier.min_quantity:
            if tier.max_quantity is None or quantity <= tier.max_quantity:
                applicable = tier
    if applicable:
        return applicable.unit_price_eur
    # Fallback: use lowest tier price
    return tiers[0].unit_price_eur


# ── Historical Awards Bonus ──────────────────────────────────────────────────

def _historical_bonus(supplier_id: str, category: str, db: Session) -> tuple[float, str]:
    """
    Compute 0-10 bonus points from historical award performance.
    Formula: (completed / total) × recency_factor × 10

    Returns (bonus_points, explanation_string).
    """
    awards = (
        db.query(HistoricalAward)
        .filter(
            HistoricalAward.supplier_id == supplier_id,
            HistoricalAward.category == category,
        )
        .all()
    )
    if not awards:
        return 0.0, "No historical awards in this category"

    total = len(awards)
    completed = sum(1 for a in awards if (a.outcome or "").lower() == "completed")
    completion_rate = completed / total if total > 0 else 0

    # Recency factor: more recent = higher weight (simplified: use total count as proxy)
    recency_factor = min(1.0, total / 10.0)  # caps at 10 awards

    bonus = round(completion_rate * recency_factor * 10, 1)
    explanation = (
        f"{completed}/{total} completed in category "
        f"(completion rate: {completion_rate:.0%}, bonus: +{bonus:.1f})"
    )
    return bonus, explanation


# ── Supplier Scoring ─────────────────────────────────────────────────────────

def score_suppliers(candidates: list[dict], structured_request: dict, db: Session | None = None) -> dict:
    """
    Score each eligible supplier using weighted factors + volume pricing + historical bonus.
    Fully deterministic — every score component is explainable.

    Scoring factors:
    - Price (volume-adjusted from PricingTier if available)
    - Delivery speed
    - Compliance tier (preferred > approved > spot) + contract status
    - ESG score
    - Historical awards bonus (+0 to +10)
    """
    category = structured_request.get("category", "default")
    weights = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["default"])
    quantity = structured_request.get("quantity", 1) or 1
    budget = structured_request.get("_budget", 0)

    if not candidates:
        return {"scored": [], "scoring_warnings": []}

    # Apply volume pricing if DB available
    if db is not None:
        for s in candidates:
            volume_price = _get_volume_price(s["id"], quantity, db)
            if volume_price is not None and volume_price != s["unit_price_eur"]:
                s["unit_price_eur_volume"] = volume_price
                s["volume_discount_note"] = (
                    f"Volume price for qty {quantity}: €{volume_price:.2f}/unit "
                    f"(base: €{s['unit_price_eur']:.2f}/unit)"
                )
                s["unit_price_eur"] = volume_price  # use for scoring

    # Normalize price (lower = better) across candidates
    prices = [s["unit_price_eur"] for s in candidates]
    min_price, max_price = min(prices), max(prices)
    price_range = max_price - min_price or 1

    # Normalize delivery (lower days = better)
    deliveries = [s["delivery_days"] for s in candidates]
    min_del, max_del = min(deliveries), max(deliveries)
    del_range = max_del - min_del or 1

    scored = []
    warnings = []

    for s in candidates:
        # Price score: inverted (cheaper = higher score)
        price_score = 1.0 - (s["unit_price_eur"] - min_price) / price_range

        # Delivery score: inverted (faster = higher score)
        delivery_score = 1.0 - (s["delivery_days"] - min_del) / del_range

        # Compliance score: preferred > approved > spot
        compliance_map = {"preferred": 1.0, "approved": 0.7, "spot": 0.3}
        compliance_score = compliance_map.get(s["preferred_tier"], 0.5)

        # Contract status modifier
        if s["contract_status"] == "expired":
            compliance_score *= 0.85
            warnings.append({"supplier_id": s["id"], "rule_id": "R08",
                              "detail": f"{s['name']}: contract expired — R08 applied (-15% compliance score)"})
        elif s["contract_status"] == "none":
            compliance_score *= 0.75

        # ESG score: normalized 0-1
        esg_score_norm = s["esg_score"] / 100.0

        # Composite score (0-1)
        composite = (
            weights["price"] * price_score +
            weights["delivery"] * delivery_score +
            weights["compliance"] * compliance_score +
            weights["esg"] * esg_score_norm
        )

        # Historical awards bonus (0-10 extra points on 0-100 scale)
        hist_bonus, hist_note = (0.0, "DB unavailable")
        if db is not None:
            base_sid = s.get("supplier_id", s["id"])
            hist_bonus, hist_note = _historical_bonus(base_sid, category, db)

        total_cost = s["unit_price_eur"] * quantity
        raw_score = round(composite * 100, 1)
        final_score = round(min(100, raw_score + hist_bonus), 1)

        scored.append({
            **s,
            "score": final_score,
            "score_raw": raw_score,
            "historical_bonus": hist_bonus,
            "score_breakdown": {
                "price_score": round(price_score * 100, 1),
                "delivery_score": round(delivery_score * 100, 1),
                "compliance_score": round(compliance_score * 100, 1),
                "esg_score_normalized": round(esg_score_norm * 100, 1),
                "historical_bonus": hist_bonus,
                "historical_note": hist_note,
                "weights_used": weights,
            },
            "total_cost_eur": round(total_cost, 2),
            "within_budget": total_cost <= budget if budget > 0 else True,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    for i, s in enumerate(scored):
        s["rank"] = i + 1

    return {"scored": scored, "scoring_warnings": warnings}


# ── AIS Computation ──────────────────────────────────────────────────────────

def compute_ais(
    structured_request: dict,
    policy_results: dict,
    supplier_results: dict,
    scoring_result: dict,
    decision: dict,
) -> dict:
    """
    Decision Quality Score (0-100).
    Measures how well the system handled this request — completeness of analysis,
    correctness of the decision, and audit-readiness under EU AI Act Art.13.
    """
    components = {}
    decision_type = decision.get("decision_type", "")
    escalations = policy_results.get("escalations", [])
    violations = policy_results.get("violations", [])
    warnings = policy_results.get("warnings", [])
    infeasible = (supplier_results.get("infeasibility") or {}).get("infeasible", False)
    scored = scoring_result.get("scored", [])

    # ── 1. Request Completeness (20 pts) ──────────────────────────────────────
    # Are all required fields present? Is the request feasible?
    required_fields = ["item_description", "category", "quantity", "budget_eur", "deadline_days"]
    present = sum(1 for f in required_fields if structured_request.get(f) is not None)
    ambiguities = len(structured_request.get("ambiguities", []))
    completeness = int((present / len(required_fields)) * 20)
    completeness -= ambiguities * 3
    if infeasible:
        completeness -= 12  # budget can't cover minimum cost — request is fundamentally broken
    components["request_completeness"] = max(0, min(20, completeness))

    # ── 2. Policy Coverage (15 pts) ───────────────────────────────────────────
    # Were relevant rules evaluated? More rules checked = more thorough.
    rules_checked = len(violations) + len(warnings) + len(escalations)
    if rules_checked >= 3:
        components["policy_coverage"] = 15
    elif rules_checked >= 1:
        components["policy_coverage"] = 12
    else:
        # No rules triggered is fine for clean requests, but less evidence of coverage
        components["policy_coverage"] = 8 if decision_type == "approved" else 10

    # ── 3. Decision Traceability (25 pts) ─────────────────────────────────────
    # Can the decision be fully reconstructed from the audit trail?
    traceability = 0
    if decision.get("decision_type"):           traceability += 7
    if decision.get("reasoning_narrative"):     traceability += 11
    if scored:                                  traceability += 7
    components["traceability"] = min(25, traceability)

    # ── 4. Supplier Justification (20 pts) ────────────────────────────────────
    # Can every supplier (selected or rejected) understand why?
    disqualified = supplier_results.get("disqualified", [])
    justification = 0
    # Recommended supplier has documented reasoning?
    if decision.get("reasoning_narrative"):
        justification += 10
    elif decision_type == "rejected" and decision.get("rejection_reason"):
        justification += 10
    else:
        justification += 4
    # Disqualified suppliers have documented reasons?
    if not disqualified:
        justification += 10  # no rejections = no justification gap
    elif all(len(s.get("disqualification_reasons", [])) > 0 for s in disqualified):
        justification += 10
    else:
        justification += 3
    components["supplier_justification"] = min(20, justification)

    # ── 5. Decision Correctness (20 pts) ──────────────────────────────────────
    # Did the system make the right call given the signals?
    if decision_type == "approved" and not escalations and not infeasible and not violations:
        correctness = 20  # clean approval, nothing flagged — correct
    elif decision_type == "escalated" and (escalations or infeasible):
        correctness = 16  # correctly escalated — but not a clean outcome
    elif decision_type == "rejected" and violations:
        correctness = 18  # correctly rejected based on violations
    elif decision_type == "approved" and escalations:
        correctness = 0   # WRONG: should have escalated
    elif decision_type == "approved" and infeasible:
        correctness = 0   # WRONG: approved infeasible request
    elif decision_type == "clarification_needed":
        correctness = 14  # asked for more info — reasonable
    else:
        correctness = 10  # ambiguous case
    components["decision_correctness"] = correctness

    total_score = sum(components.values())

    if total_score >= 85:
        grade = "Audit-Ready"
        eu_compliant = True
    elif total_score >= 65:
        grade = "Review Needed"
        eu_compliant = False
    else:
        grade = "Action Required"
        eu_compliant = False

    flags = []
    if components["request_completeness"] < 10:
        flags.append("Request has missing fields or infeasible budget")
    if components["traceability"] < 18:
        flags.append("Decision audit trail is incomplete")
    if components["supplier_justification"] < 12:
        flags.append("Some suppliers lack documented justification")
    if components["decision_correctness"] == 0:
        flags.append("CRITICAL: Decision does not match the policy signals — review required")
    if infeasible:
        flags.append("Budget cannot cover minimum supplier cost")

    return {
        "score": total_score,
        "grade": grade,
        "components": components,
        "eu_ai_act_article_13_compliant": eu_compliant,
        "flags": flags,
    }
