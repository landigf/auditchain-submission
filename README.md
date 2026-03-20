# AuditChain
- User UI: https://auditchain-frontend.ashywater-30acecc8.northeurope.azurecontainerapps.io/
- Metaflow: https://metaflow-ui.ashywater-30acecc8.northeurope.azurecontainerapps.io/?_group_limit=30&_limit=30&_order=%2Bflow_id&status=completed%2Cfailed%2Crunning

**Team:** PushToMain

**Participants:**
- Debmalya Chatterjee — debchatt21@gmail.com
- Gennaro Francesco Landi — landigf.eng@gmail.com
- Casimir Rönnlöf — casimirr04@gmail.com
- Francisca Ramos — franciscamaginaramos@gmail.com

---

## Submission

**Title:** AuditChain

**Slogan:** Smart sourcing. Zero guessing.

**Demo video:** https://drive.google.com/file/d/1Bj8I7ySGY-N2gjfyNibetPRlxDYIr-H_/view?usp=sharing

**GitHub:** This repo

---

## Description

AuditChain is designed so that only two steps use an LLM (temperature = 0): parsing free-text requests into structured data and generating the final audit narrative. Everything else—policy checks, supplier filtering, scoring, and decisions—is deterministic Python to ensure full reproducibility. Fuzzy logic is layered on top to handle uncertainty, especially around thresholds, supplier ranking, and confidence estimation. This ensures that identical structured inputs always produce identical decisions, while still capturing real-world ambiguity.

The process begins with parsing, where the LLM extracts fields such as category, quantity, budget (normalized to EUR), deadline, delivery country, and any preferred supplier. It also flags ambiguities and missing fields. If critical fields like budget, quantity, or item description are missing, the system enters a clarification state with a timeout based on urgency. Otherwise, the request proceeds to policy checks, where deterministic rules evaluate budget thresholds, spending authority, urgency, and supplier restrictions. Higher-value requests trigger escalation rules, while restricted or non-compliant suppliers trigger hard violations. A fuzzy overlay then detects proximity to thresholds (for example, a budget just below an escalation boundary) and flags borderline cases.

Next, the system queries and filters suppliers strictly based on eligibility criteria such as compliance status, ESG score, regulatory constraints, capacity, geographic coverage, and category match. It also checks feasibility by comparing the cheapest possible total cost against the budget. Eligible suppliers are then scored using a weighted formula that considers price, delivery time, compliance level, and ESG performance, with category-specific weights. Adjustments include volume pricing and a historical performance bonus based on past contract completion rates. A fuzzy scoring layer evaluates how meaningful the differences between suppliers actually are, while a sensitivity analysis perturbs weights to test whether the ranking is stable or easily flipped.

The decision step follows a strict priority order: infeasible requests escalate, policy violations reject, policy escalations trigger escalation, and only clean cases proceed to approval. A base confidence score is calculated from the gap between the top suppliers, but this is overridden by a fuzzy confidence gate that combines multiple uncertainty signals, including threshold proximity, small score gaps, limited supplier options, ambiguities, and missing data. These signals are aggregated into a final confidence score, and if it falls below 0.45 the system escalates instead of auto-approving.

After the decision is locked, the LLM generates a concise audit narrative explaining what was requested, which rules were applied, and why the outcome was reached. A separate risk score is computed using budget size, authority usage, urgency, and supplier type, providing an informational 0–100 risk level without affecting the decision itself. Finally, the Audit Intelligence Score (AIS) evaluates how defensible the decision is under audit conditions by measuring completeness, rule coverage, traceability, contestability, and whether escalation behavior was appropriate.

The overall flow is implemented as a branching DAG: ambiguous or infeasible requests go back to the client for clarification, low-confidence decisions trigger internal review, authority-related escalations go to a manager, and only high-confidence, policy-compliant cases are auto-approved. The result is a system that is deterministic, auditable, and compliant by design, while using fuzzy logic to avoid brittle decisions at the edges.
