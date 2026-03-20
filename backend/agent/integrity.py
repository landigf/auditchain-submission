"""
Integrity Agent  (Decision parameter #1)
-----------------------------------------
Input:  raw request dict
Output: integrity result dict

Checks whether the raw request is interpretable:
- request_text is non-empty and meaningful
- language is supported or translatable
- category can be inferred
- delivery_countries is present
- required_by_date is present

Escalates (ER-007) if request cannot be reliably interpreted.
"""

import json
from openai import OpenAI

SUPPORTED_LANGUAGES = {"en", "fr", "de", "es", "it", "nl", "pt"}

PROMPT = """
You are a procurement intake validator. Check whether a purchase request can be reliably interpreted.

Evaluate:
1. is_text_meaningful: Is request_text non-empty and a recognisable purchase request (not gibberish, test data, or empty)?
2. is_language_supported: Is the language in the supported set (en, fr, de, es, it, nl, pt) or clearly translatable?
3. is_category_inferable: Can a procurement category be determined from the text?
4. confidence: Your overall confidence that this request can be processed ("high" | "medium" | "low")
5. reason: One sentence explaining any issues, or "Request is clear and interpretable."

Return JSON:
{
  "is_text_meaningful": true/false,
  "is_language_supported": true/false,
  "is_category_inferable": true/false,
  "confidence": "high" | "medium" | "low",
  "reason": "<string>"
}
"""


def run(request: dict) -> dict:
    """
    Returns:
    {
      "status": "pass" | "escalate",
      "checks": { is_text_meaningful, is_language_supported, is_category_inferable,
                  has_delivery_countries, has_required_by_date },
      "issues": ["<description>", ...],
      "escalation": None | { "rule": "ER-007", "escalate_to": "Requester", "reason": "..." }
    }
    """
    issues = []

    # Rule-based checks (no LLM needed)
    has_delivery_countries = bool(request.get("delivery_countries"))
    has_required_by_date = bool(request.get("required_by_date"))

    if not has_delivery_countries:
        issues.append("delivery_countries is missing")
    if not has_required_by_date:
        issues.append("required_by_date is missing")

    # LLM checks
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": (
                f"request_text: {request.get('request_text', '')}\n"
                f"request_language: {request.get('request_language', 'unknown')}"
            )},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    llm = json.loads(response.choices[0].message.content)

    if not llm.get("is_text_meaningful"):
        issues.append("request_text is empty or not a recognisable purchase request")
    if not llm.get("is_language_supported"):
        issues.append(f"language '{request.get('request_language')}' is not supported or translatable")
    if not llm.get("is_category_inferable"):
        issues.append("category cannot be determined from request_text")

    checks = {
        "is_text_meaningful":      llm.get("is_text_meaningful"),
        "is_language_supported":   llm.get("is_language_supported"),
        "is_category_inferable":   llm.get("is_category_inferable"),
        "has_delivery_countries":  has_delivery_countries,
        "has_required_by_date":    has_required_by_date,
        "confidence":              llm.get("confidence"),
    }

    # Escalate only if request cannot be reliably interpreted
    blocking = (
        not llm.get("is_text_meaningful")
        or not llm.get("is_language_supported")
        or not llm.get("is_category_inferable")
        or llm.get("confidence") == "low"
    )

    return {
        "status": "escalate" if blocking else "pass",
        "checks": checks,
        "issues": issues,
        "escalation": {
            "rule": "ER-007",
            "escalate_to": "Requester",
            "reason": llm.get("reason", "; ".join(issues)),
            "blocking": True,
        } if blocking else None,
    }
