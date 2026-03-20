"""
Completeness Agent  (Decision parameter #3)
--------------------------------------------
Input:  raw request dict  +  request_interpretation (from parser agent)
Output: completeness result dict

Checks that all fields required for sourcing are present and non-null.
This is a pure rule-based check — no LLM needed.

Triggers ER-001 → Requester if any required field is missing.
"""

REQUIRED_FIELDS = [
    ("quantity",          "Quantity is missing — cannot determine pricing tier"),
    ("budget_amount",     "Budget amount is missing — cannot evaluate threshold or supplier fit"),
    ("currency",          "Currency is missing — cannot apply approval thresholds"),
    ("category_l1",       "Top-level category is missing — cannot identify eligible suppliers"),
    ("category_l2",       "Specific category is missing — cannot match suppliers precisely"),
    ("required_by_date",  "Required delivery date is missing — cannot assess lead time feasibility"),
    ("delivery_country",  "Delivery country is missing — cannot check supplier geography coverage"),
]


def run(request: dict, interpretation: dict) -> dict:
    """
    Returns:
    {
      "status": "pass" | "escalate",
      "field_checks": { "<field>": "present" | "missing", ... },
      "missing_fields": ["<field>", ...],
      "escalation": None | { "rule": "ER-001", "escalate_to": "Requester", "reason": "...", "blocking": True }
    }
    """
    field_checks = {}
    missing = []

    for field, reason in REQUIRED_FIELDS:
        # Check interpretation first, fall back to raw request
        value = interpretation.get(field) if interpretation.get(field) is not None else request.get(field)
        if value is None or value == [] or value == "":
            field_checks[field] = {"status": "missing", "reason": reason}
            missing.append(field)
        else:
            field_checks[field] = {"status": "present", "value": value}

    return {
        "status": "escalate" if missing else "pass",
        "field_checks": field_checks,
        "missing_fields": missing,
        "escalation": {
            "rule": "ER-001",
            "escalate_to": "Requester",
            "reason": f"Request is incomplete. Missing fields: {', '.join(missing)}.",
            "blocking": True,
        } if missing else None,
    }
