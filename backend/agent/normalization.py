"""
Normalization Agent  (Decision parameter #2)
---------------------------------------------
Input:  raw request dict  +  request_interpretation (from parser agent)
Output: normalization result dict

Cross-checks extracted values against the structured fields already in the request:
- Does extracted quantity match request.quantity?
- Does extracted budget match request.budget_amount?
- Are units aligned with request.unit_of_measure?
- Are implicit values correctly extracted (e.g. "400 consulting days")?

Escalates if there is a material conflict between the text and the structured fields.
"""

import json
from openai import OpenAI

PROMPT = """
You are a procurement data validator. Compare extracted values (parsed from free text) against
the structured fields already recorded in the request system.

For each field, determine:
- "match":    values agree (within reasonable rounding/phrasing)
- "mismatch": values clearly conflict
- "missing":  the value is absent in one or both sources
- "inferred": the structured field is null but the text implies a value (acceptable)

Return JSON:
{
  "quantity": { "status": "match|mismatch|missing|inferred", "text_value": <x>, "structured_value": <y>, "note": "..." },
  "budget_amount": { "status": "...", "text_value": <x>, "structured_value": <y>, "note": "..." },
  "unit_of_measure": { "status": "...", "text_value": "<x>", "structured_value": "<y>", "note": "..." },
  "category": { "status": "...", "text_value": "<x>", "structured_value": "<y>", "note": "..." },
  "overall_conflict": true/false,
  "conflict_summary": "<one sentence or null>"
}

Rules:
- Small rounding differences (e.g. 400000 vs 400 000.00) are "match"
- Unit synonyms (e.g. "devices" vs "device") are "match"
- A null structured field with an extracted value is "inferred" (not a conflict)
- Only set overall_conflict=true for genuine mismatches that would affect sourcing decisions
"""


def run(request: dict, interpretation: dict) -> dict:
    """
    Returns:
    {
      "status": "pass" | "escalate",
      "field_checks": { quantity: {...}, budget_amount: {...}, unit_of_measure: {...}, category: {...} },
      "conflicts": ["<description>", ...],
      "escalation": None | { "rule": "ER-001", "escalate_to": "Requester", "reason": "..." }
    }
    """
    client = OpenAI()

    context = {
        "extracted_from_text": {
            "quantity":        interpretation.get("quantity"),
            "budget_amount":   interpretation.get("budget_amount"),
            "unit_of_measure": interpretation.get("unit_of_measure"),
            "category_l1":     interpretation.get("category_l1"),
            "category_l2":     interpretation.get("category_l2"),
        },
        "structured_fields": {
            "quantity":        request.get("quantity"),
            "budget_amount":   request.get("budget_amount"),
            "unit_of_measure": request.get("unit_of_measure"),
            "category_l1":     request.get("category_l1"),
            "category_l2":     request.get("category_l2"),
        },
        "request_text": request.get("request_text", ""),
    }

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(context)},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    result = json.loads(response.choices[0].message.content)

    conflicts = [
        f"{field}: extracted={v.get('text_value')} vs structured={v.get('structured_value')} — {v.get('note', '')}"
        for field, v in result.items()
        if isinstance(v, dict) and v.get("status") == "mismatch"
    ]

    has_conflict = result.get("overall_conflict", False)

    return {
        "status": "escalate" if has_conflict else "pass",
        "field_checks": {k: v for k, v in result.items() if isinstance(v, dict)},
        "conflicts": conflicts,
        "escalation": {
            "rule": "ER-001",
            "escalate_to": "Requester",
            "reason": result.get("conflict_summary") or "; ".join(conflicts),
            "blocking": True,
        } if has_conflict else None,
    }
