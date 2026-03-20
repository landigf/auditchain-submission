"""Seed mock suppliers and rules. Run once: python -m db.seed"""
from .database import SessionLocal, init_db
from .models import Supplier, Rule


SUPPLIERS = [
    # HARDWARE CATEGORY
    dict(id="S01", name="AlpineSystems AG", category="hardware",
         unit_price_eur=750.0, min_quantity=10, delivery_days=7,
         compliance_status="approved", esg_score=88, preferred_tier="preferred",
         contract_status="active", country="Switzerland", eu_based=True,
         notes="Top-tier preferred vendor. Active framework contract."),

    dict(id="S02", name="TechGlobal Europe", category="hardware",
         unit_price_eur=690.0, min_quantity=50, delivery_days=10,
         compliance_status="approved", esg_score=82, preferred_tier="preferred",
         contract_status="active", country="Germany", eu_based=True,
         notes="Preferred vendor for large-volume hardware."),

    dict(id="S03", name="ConnectPro Ltd (Supplier X)", category="hardware",
         unit_price_eur=640.0, min_quantity=1, delivery_days=5,
         compliance_status="blocked", esg_score=61, preferred_tier="approved",
         contract_status="active", country="Ireland", eu_based=True,
         notes="COMPLIANCE HOLD: Under investigation for billing discrepancy Q4 2025."),

    dict(id="S04", name="LaptopDirect EU", category="hardware",
         unit_price_eur=720.0, min_quantity=20, delivery_days=8,
         compliance_status="approved", esg_score=71, preferred_tier="approved",
         contract_status="expired", country="Netherlands", eu_based=True,
         notes="Good pricing. Contract expired Jan 2026 — renewal pending."),

    dict(id="S05", name="FastShip Tech", category="hardware",
         unit_price_eur=810.0, min_quantity=5, delivery_days=2,
         compliance_status="approved", esg_score=44, preferred_tier="spot",
         contract_status="none", country="Poland", eu_based=True,
         notes="Fast delivery but low ESG score. Spot vendor only."),

    dict(id="S06", name="BudgetTech India", category="hardware",
         unit_price_eur=520.0, min_quantity=100, delivery_days=21,
         compliance_status="approved", esg_score=78, preferred_tier="spot",
         contract_status="none", country="India", eu_based=False,
         notes="Cheapest option but non-EU. GDPR-sensitive categories excluded."),

    dict(id="S07", name="SecureHardware GmbH", category="hardware",
         unit_price_eur=800.0, min_quantity=10, delivery_days=9,
         compliance_status="approved", esg_score=91, preferred_tier="approved",
         contract_status="active", country="Germany", eu_based=True,
         notes="High ESG. Specializes in government/regulated sector hardware."),

    # SERVICES CATEGORY
    dict(id="S08", name="GlobalConsult Partners", category="services",
         unit_price_eur=1850.0, min_quantity=1, delivery_days=1,
         compliance_status="approved", esg_score=75, preferred_tier="preferred",
         contract_status="active", country="Switzerland", eu_based=True,
         notes="IT consulting preferred vendor. Daily rate."),

    dict(id="S09", name="NexaServices EU", category="services",
         unit_price_eur=1600.0, min_quantity=1, delivery_days=3,
         compliance_status="approved", esg_score=80, preferred_tier="approved",
         contract_status="active", country="France", eu_based=True,
         notes="Strong delivery. Slightly lower rate than preferred tier."),

    dict(id="S10", name="OffshoreConsult Pro", category="services",
         unit_price_eur=900.0, min_quantity=1, delivery_days=2,
         compliance_status="under_review", esg_score=55, preferred_tier="spot",
         contract_status="none", country="Ukraine", eu_based=False,
         notes="Under compliance review. Not EU-based. Do not use for GDPR sensitive work."),

    # FACILITIES CATEGORY
    dict(id="S11", name="OfficeSupply AG", category="facilities",
         unit_price_eur=320.0, min_quantity=1, delivery_days=5,
         compliance_status="approved", esg_score=84, preferred_tier="preferred",
         contract_status="active", country="Switzerland", eu_based=True,
         notes="Preferred for office furniture and supplies."),

    dict(id="S12", name="EuroFacilities BV", category="facilities",
         unit_price_eur=280.0, min_quantity=10, delivery_days=8,
         compliance_status="approved", esg_score=77, preferred_tier="approved",
         contract_status="active", country="Netherlands", eu_based=True,
         notes="Good pricing for bulk facilities orders."),

    dict(id="S13", name="GreenSpace Solutions", category="facilities",
         unit_price_eur=350.0, min_quantity=5, delivery_days=10,
         compliance_status="approved", esg_score=95, preferred_tier="approved",
         contract_status="active", country="Sweden", eu_based=True,
         notes="Highest ESG score in category. Sustainable certified."),

    # SOFTWARE / LICENSES CATEGORY
    dict(id="S14", name="CloudSoft Enterprise", category="software",
         unit_price_eur=45000.0, min_quantity=1, delivery_days=1,
         compliance_status="approved", esg_score=79, preferred_tier="preferred",
         contract_status="active", country="USA", eu_based=False,
         notes="SaaS enterprise licenses. Non-EU but standard for software category."),

    dict(id="S15", name="EuroSoft AG", category="software",
         unit_price_eur=52000.0, min_quantity=1, delivery_days=1,
         compliance_status="approved", esg_score=86, preferred_tier="approved",
         contract_status="active", country="Germany", eu_based=True,
         notes="EU-based alternative. Higher price but GDPR-native."),
]


RULES = [
    dict(id="R01", name="Budget Approval Threshold",
         description="Purchases above €100,000 require CFO approval before proceeding",
         action="escalate", escalate_to="CFO",
         warn_reason=None, active=True),

    dict(id="R02", name="Single Source Justification",
         description="If fewer than 2 compliant suppliers are available, document justification",
         action="warn", escalate_to=None,
         warn_reason="Single source procurement — justification required per policy §4.2",
         active=True),

    dict(id="R03", name="Blocked Supplier Check",
         description="Suppliers with compliance_status='blocked' cannot be selected under any circumstances",
         action="block", escalate_to=None,
         warn_reason=None, active=True),

    dict(id="R04", name="Preferred Supplier Override",
         description="If requester specifies a non-preferred supplier when preferred options exist, flag for review",
         action="warn", escalate_to=None,
         warn_reason="Requester specified non-preferred vendor while preferred alternatives are available",
         active=True),

    dict(id="R05", name="Emergency Timeline",
         description="Requests with deadline under 3 business days require emergency procurement approval",
         action="escalate", escalate_to="Procurement Manager",
         warn_reason=None, active=True),

    dict(id="R06", name="ESG Minimum Score",
         description="Suppliers must achieve ESG score ≥ 60 to be considered",
         action="block", escalate_to=None,
         warn_reason=None, active=True),

    dict(id="R07", name="GDPR Geographic Restriction",
         description="For GDPR-sensitive categories (software, services), only EU-based suppliers are permitted",
         action="block", escalate_to=None,
         warn_reason=None, active=True),

    dict(id="R08", name="Expired Contract Warning",
         description="If the chosen supplier has an expired contract, flag for contract renewal before PO",
         action="warn", escalate_to=None,
         warn_reason="Supplier contract is expired — new contract must be signed before issuing PO",
         active=True),

    dict(id="R09", name="Under Review Supplier",
         description="Suppliers with compliance status 'under_review' require Procurement Manager sign-off",
         action="escalate", escalate_to="Procurement Manager",
         warn_reason=None, active=True),

    dict(id="R10", name="Spot Vendor High Value",
         description="Spot vendors (non-contracted) cannot be used for purchases over €50,000",
         action="escalate", escalate_to="Procurement Manager",
         warn_reason=None, active=True),
]


def seed():
    init_db()
    db = SessionLocal()
    try:
        if db.query(Supplier).count() == 0:
            for s in SUPPLIERS:
                db.add(Supplier(**s))
            print(f"Seeded {len(SUPPLIERS)} suppliers")

        if db.query(Rule).count() == 0:
            for r in RULES:
                db.add(Rule(**r))
            print(f"Seeded {len(RULES)} rules")

        db.commit()
        print("Database seeded successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
