"""
Data loaders for Chain IQ provided datasets.
Run at startup: python -m db.loaders

File priority (place in solution/backend/data/):
  suppliers.csv       → overwrites seed mock suppliers
  pricing.csv         → populates pricing tiers
  policies.json       → overwrites seed mock rules
  historical_awards.csv → populates award history
  requests.json       → loads demo/test cases
"""
from __future__ import annotations
import csv
import json
import os
from pathlib import Path
from sqlalchemy.orm import Session
from db.database import SessionLocal
from db.models import Supplier, Rule, PricingTier, HistoricalAward

DATA_DIR = Path(__file__).parent.parent / "data"

# Map category_l2 (subcategory) to our scoring weight categories
_CAT_L2_MAP: dict[str, str] = {
    # IT Hardware
    "Laptops": "hardware", "Mobile Workstations": "hardware",
    "Desktop Workstations": "hardware", "Monitors": "hardware",
    "Docking Stations": "hardware", "Tablets": "hardware",
    "Smartphones": "hardware", "Rugged Devices": "hardware",
    "Accessories Bundles": "hardware",
    "Replacement / Break-Fix Pool Devices": "hardware",
    # IT Cloud / Software
    "Cloud Compute": "software", "Cloud Storage": "software",
    "Cloud Networking": "software", "Managed Cloud Platform Services": "software",
    "Cloud Security Services": "software", "Enterprise Software Licenses": "software",
    "SaaS Solutions": "software",
    # Professional Services
    "Cloud Architecture Consulting": "services",
    "Cybersecurity Advisory": "services",
    "Data Engineering Services": "services",
    "IT Project Management Services": "services",
    "Software Development Services": "services",
    # Facilities
    "Workstations and Desks": "facilities", "Office Chairs": "facilities",
    "Meeting Room Furniture": "facilities", "Storage Cabinets": "facilities",
    "Reception and Lounge Furniture": "facilities",
    # Marketing → default
    "Search Engine Marketing (SEM)": "default",
    "Social Media Advertising": "default",
    "Content Production Services": "default",
    "Marketing Analytics Services": "default",
    "Influencer Campaign Management": "default",
}

_CAT_L1_MAP: dict[str, str] = {
    "IT": "hardware",
    "Facilities": "facilities",
    "Professional Services": "services",
    "Marketing": "default",
}


def _map_category(cat_l1: str, cat_l2: str) -> str:
    """Map ChainIQ category_l1/l2 to our scoring weight category."""
    return _CAT_L2_MAP.get(cat_l2) or _CAT_L1_MAP.get(cat_l1) or "default"


def _slug(s: str) -> str:
    """Simple slug: spaces → underscores, remove special chars."""
    return s.replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "")


# ── Suppliers ─────────────────────────────────────────────────────────────────

def load_suppliers(db: Session, path: Path | None = None) -> int:
    """
    Load suppliers.csv.
    Real ChainIQ columns:
    supplier_id, supplier_name, category_l1, category_l2, country_hq,
    service_regions, currency, pricing_model, quality_score, risk_score,
    esg_score, preferred_supplier, is_restricted, restriction_reason,
    contract_status, data_residency_supported, capacity_per_month, notes
    """
    p = path or DATA_DIR / "suppliers.csv"
    if not p.exists():
        print(f"[loaders] suppliers.csv not found at {p} — using seed data")
        return 0

    db.query(Supplier).delete()
    count = 0
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            supplier_id = row.get("supplier_id") or row.get("id") or f"S{count:03d}"
            cat_l2 = row.get("category_l2", "")
            cat_l1 = row.get("category_l1", "")

            # Composite ID: one Supplier record per (supplier, category) pair
            composite_id = f"{supplier_id}_{_slug(cat_l2)}" if cat_l2 else supplier_id

            is_restricted = row.get("is_restricted", "").lower() in ("true", "1", "yes")
            preferred = row.get("preferred_supplier", "").lower() in ("true", "1", "yes")

            db.add(Supplier(
                id=composite_id,
                name=row.get("supplier_name") or row.get("name") or supplier_id,
                category=_map_category(cat_l1, cat_l2),
                category_l1=cat_l1,
                category_l2=cat_l2,
                unit_price_eur=0.0,      # filled from pricing.csv in load_pricing()
                min_quantity=1,           # filled from pricing.csv moq
                delivery_days=14,         # filled from pricing.csv standard_lead_time_days
                compliance_status="blocked" if is_restricted else "approved",
                esg_score=int(row.get("esg_score") or 70),
                preferred_tier="preferred" if preferred else "approved",
                contract_status=row.get("contract_status") or "active",
                country=row.get("country_hq") or row.get("country") or "Unknown",
                service_regions=row.get("service_regions") or "",
                eu_based=_is_eu(row.get("country_hq") or row.get("country") or ""),
                data_residency_supported=row.get("data_residency_supported", "").lower() in ("true", "1", "yes"),
                capacity_per_month=int(row.get("capacity_per_month") or 0) or None,
                notes=(row.get("restriction_reason") or row.get("notes") or ""),
            ))
            count += 1
    db.commit()
    print(f"[loaders] Loaded {count} suppliers from {p.name}")
    return count


# ── Pricing Tiers ─────────────────────────────────────────────────────────────

def load_pricing(db: Session, path: Path | None = None) -> int:
    """
    Load pricing.csv and update Supplier records with base-tier prices.
    Real ChainIQ columns:
    pricing_id, supplier_id, category_l1, category_l2, region, currency,
    pricing_model, min_quantity, max_quantity, unit_price, moq,
    standard_lead_time_days, expedited_lead_time_days, expedited_unit_price,
    valid_from, valid_to, notes
    """
    p = path or DATA_DIR / "pricing.csv"
    if not p.exists():
        print(f"[loaders] pricing.csv not found at {p} — no volume discounts")
        return 0

    db.query(PricingTier).delete()
    count = 0

    # Track base tier per (supplier_id, category_l2) — used to update Supplier records
    base_tier: dict[str, dict] = {}  # key = composite_id → {price, delivery, moq}

    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            supplier_id = row.get("supplier_id", "")
            cat_l2 = row.get("category_l2", "")
            composite_id = f"{supplier_id}_{_slug(cat_l2)}" if cat_l2 else supplier_id

            min_qty = int(row.get("min_quantity") or 0)
            max_qty_raw = row.get("max_quantity")
            max_qty = int(max_qty_raw) if max_qty_raw and max_qty_raw.strip() else None
            unit_price = float(row.get("unit_price") or row.get("unit_price_eur") or 0)
            moq = int(row.get("moq") or 1)
            lead_time = int(row.get("standard_lead_time_days") or 14)

            db.add(PricingTier(
                supplier_id=composite_id,
                min_quantity=min_qty,
                max_quantity=max_qty,
                unit_price_eur=unit_price,
                discount_pct=0.0,
            ))
            count += 1

            # Track the base (lowest min_qty) tier for this supplier+category
            # moq = minimum order quantity for this tier (may differ from min_quantity)
            if composite_id not in base_tier or min_qty < base_tier[composite_id]["min_qty"]:
                base_tier[composite_id] = {
                    "price": unit_price,
                    "delivery": lead_time,
                    "moq": moq,
                    "min_qty": min_qty,   # track for comparison
                }

    db.commit()

    # Update Supplier records with base-tier price and delivery
    updated = 0
    for composite_id, tier_data in base_tier.items():
        supplier = db.query(Supplier).filter(Supplier.id == composite_id).first()
        if supplier:
            supplier.unit_price_eur = tier_data["price"]
            supplier.delivery_days = tier_data["delivery"]
            supplier.min_quantity = tier_data["moq"]
            updated += 1
    db.commit()
    print(f"[loaders] Loaded {count} pricing tiers from {p.name}, updated {updated} supplier prices")
    return count


# ── Policies ──────────────────────────────────────────────────────────────────

def load_policies(db: Session, path: Path | None = None) -> int:
    """
    Load policies.json. ChainIQ format is a rich dict with multiple sections:
    approval_thresholds, preferred_suppliers, restricted_suppliers,
    category_rules, geography_rules, escalation_rules.

    We convert the key sections to Rule objects, preserving IDs R01/R05 that
    the deterministic check_policy() logic depends on.
    """
    p = path or DATA_DIR / "policies.json"
    if not p.exists():
        print(f"[loaders] policies.json not found at {p} — using seed rules")
        return 0

    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    # If it's a plain list (old format / seed format), treat as-is
    if isinstance(data, list):
        rules = data
        return _insert_rules(db, rules)

    db.query(Rule).delete()
    count = 0

    # R01: Budget threshold — use AT-003 (€100k = Head of Category)
    db.add(Rule(
        id="R01",
        name="Budget Approval Threshold",
        description="Purchases above €100,000 require Head of Category approval (AT-003)",
        action="escalate",
        escalate_to="Head of Category",
        active=True,
    ))
    count += 1

    # R03: Blocked compliance status (from is_restricted in suppliers.csv)
    db.add(Rule(
        id="R03",
        name="Restricted Supplier Block",
        description="Suppliers marked as restricted in the policy list cannot be awarded",
        action="block",
        escalate_to="Procurement Manager",
        active=True,
    ))
    count += 1

    # R05: Emergency timeline (< 3 business days)
    db.add(Rule(
        id="R05",
        name="Emergency Timeline",
        description="Delivery required in under 3 business days triggers emergency procurement process",
        action="escalate",
        escalate_to="Head of Category",
        active=True,
    ))
    count += 1

    # R06: ESG minimum threshold
    db.add(Rule(
        id="R06",
        name="ESG Minimum Score",
        description="Suppliers with ESG score below 60 are disqualified",
        action="block",
        warn_reason="ESG score below minimum threshold of 60",
        active=True,
    ))
    count += 1

    # R07: GDPR data residency
    db.add(Rule(
        id="R07",
        name="GDPR Data Residency",
        description="Non-EU suppliers cannot be used for software/services categories involving personal data",
        action="block",
        warn_reason="Non-EU supplier not permitted for GDPR-sensitive categories",
        active=True,
    ))
    count += 1

    # R08: Contract status warning
    db.add(Rule(
        id="R08",
        name="Expired Contract Warning",
        description="Suppliers with expired contracts incur a compliance penalty",
        action="warn",
        warn_reason="Contract expired — use with caution and notify procurement",
        active=True,
    ))
    count += 1

    # R10: Spot vendor high value
    db.add(Rule(
        id="R10",
        name="Spot Vendor Value Limit",
        description="Spot vendors (no framework contract) cannot be used above €50,000",
        action="block",
        warn_reason="Spot vendor not eligible for high-value purchases",
        active=True,
    ))
    count += 1

    # Restricted suppliers from policies.json
    restricted = data.get("restricted_suppliers", [])
    for i, rs in enumerate(restricted):
        rule_id = f"RS-{i+1:03d}"
        scope = ", ".join(rs.get("restriction_scope", ["all"]))
        db.add(Rule(
            id=rule_id,
            name=f"Restricted: {rs.get('supplier_name', 'Unknown')}",
            description=f"{rs.get('supplier_name')} restricted in {rs.get('category_l1')}/{rs.get('category_l2')} for scope [{scope}]. Reason: {rs.get('restriction_reason', 'Policy restriction')}",
            action="escalate",
            escalate_to="Procurement Manager",
            warn_reason=rs.get("restriction_reason", "Policy restriction"),
            active=True,
        ))
        count += 1

    # Escalation rules from policies.json
    for er in data.get("escalation_rules", []):
        rule_id = er.get("rule_id", f"ER-{count:03d}")
        db.add(Rule(
            id=rule_id,
            name=f"Escalation: {er.get('trigger', rule_id)}",
            description=er.get("trigger", ""),
            action="escalate",
            escalate_to=er.get("escalate_to") or er.get("escalation_target") or "Procurement Manager",
            active=True,
        ))
        count += 1

    # Category rules from policies.json
    for cr in data.get("category_rules", []):
        rule_id = cr.get("rule_id", f"CR-{count:03d}")
        db.add(Rule(
            id=rule_id,
            name=f"Category Rule: {cr.get('rule_type', rule_id)}",
            description=cr.get("rule_text", ""),
            action="warn",
            active=True,
        ))
        count += 1

    db.commit()
    print(f"[loaders] Loaded {count} policy rules from {p.name}")
    return count


def _insert_rules(db: Session, rules: list) -> int:
    """Fallback: insert a plain list of rule dicts."""
    db.query(Rule).delete()
    count = 0
    for i, r in enumerate(rules):
        db.add(Rule(
            id=r.get("id") or f"R{i+1:02d}",
            name=r.get("name", f"Rule {i+1}"),
            description=r.get("description") or r.get("rule") or r.get("name", ""),
            action=r.get("action") or "warn",
            escalate_to=r.get("escalate_to") or r.get("approver"),
            warn_reason=r.get("warn_reason") or r.get("message"),
            active=r.get("active", True),
        ))
        count += 1
    db.commit()
    return count


# ── Historical Awards ─────────────────────────────────────────────────────────

def load_historical_awards(db: Session, path: Path | None = None) -> int:
    """
    Load historical_awards.csv.
    Real ChainIQ columns:
    award_id, request_id, award_date, category_l1, category_l2, country,
    business_unit, supplier_id, supplier_name, total_value, currency,
    quantity, required_by_date, awarded, award_rank, decision_rationale,
    policy_compliant, preferred_supplier_used, escalation_required,
    escalated_to, savings_pct, lead_time_days, risk_score_at_award, notes
    """
    p = path or DATA_DIR / "historical_awards.csv"
    if not p.exists():
        print(f"[loaders] historical_awards.csv not found at {p} — no historical context")
        return 0

    db.query(HistoricalAward).delete()
    count = 0
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            supplier_id = row.get("supplier_id", "")
            cat_l2 = row.get("category_l2", "")
            # Use composite supplier ID to match Supplier table
            composite_supplier_id = f"{supplier_id}_{_slug(cat_l2)}" if cat_l2 else supplier_id

            awarded = row.get("awarded", "").lower() in ("true", "1", "yes")
            total_eur_raw = row.get("total_value") or row.get("total_eur") or "0"

            try:
                total_eur = float(total_eur_raw)
            except ValueError:
                total_eur = 0.0

            qty_raw = row.get("quantity", "0") or "0"
            try:
                qty = int(float(qty_raw))
            except ValueError:
                qty = 0

            db.add(HistoricalAward(
                award_id=row.get("award_id") or f"A{count:04d}",
                supplier_id=composite_supplier_id,
                supplier_name=row.get("supplier_name") or "Unknown",
                category=_map_category(row.get("category_l1", ""), row.get("category_l2", "")),
                quantity=qty,
                total_eur=total_eur,
                award_date=row.get("award_date") or row.get("date"),
                outcome="completed" if awarded else "cancelled",
            ))
            count += 1
    db.commit()
    print(f"[loaders] Loaded {count} historical awards from {p.name}")
    return count


# ── Demo Requests ──────────────────────────────────────────────────────────────

def load_demo_requests(path: Path | None = None) -> list[dict]:
    """Return list of demo requests from requests.json."""
    p = path or DATA_DIR / "requests.json"
    if not p.exists():
        print(f"[loaders] requests.json not found at {p}")
        return []
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    requests = data if isinstance(data, list) else data.get("requests", [])
    print(f"[loaders] Loaded {len(requests)} demo requests from {p.name}")
    return requests


# ── Master loader ──────────────────────────────────────────────────────────────

def load_all(db: Session | None = None) -> dict:
    """Load all available data files. Gracefully skips missing files."""
    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        results = {
            "suppliers": load_suppliers(db),
            "pricing": load_pricing(db),
            "policies": load_policies(db),
            "historical_awards": load_historical_awards(db),
            "requests": len(load_demo_requests()),
        }
        return results
    finally:
        if own_session:
            db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

EU_COUNTRIES = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT",
    "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    # EEA
    "NO", "IS", "LI",
    # Adequacy decision
    "CH", "GB",
}


def _is_eu(country: str) -> bool:
    return country.strip().upper() in EU_COUNTRIES


if __name__ == "__main__":
    from db.database import init_db
    from db.seed import seed
    init_db()
    seed()
    results = load_all()
    print(f"\n✓ Data loading complete: {results}")
