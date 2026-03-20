"""
Parser Agent
------------
Input:  raw request dict (from requests.json)
Output: request_interpretation dict

Extracts structured fields from request_text using LLM.
Metadata fields (delivery_country, incumbent_supplier) and derived fields
(days_until_required) are merged in from the request object.
"""

import json
from datetime import datetime
from openai import OpenAI

PROMPT = """
You are a procurement request parser. Given a free-text purchase request, extract every field you can confidently determine.
Return null for anything you cannot infer — do not guess.

Output format (JSON):
{
  "category_l1": "<top-level category or null>",
  "category_l2": "<specific product/service type, Title Case, or null>",
  "quantity": <number or null>,
  "unit_of_measure": "<device | consulting_day | instance_hour | monthly_subscription | license | other | null>",
  "budget_amount": <number or null — if price-per-unit × quantity stated, compute total>,
  "currency": "<EUR | CHF | USD | null>",
  "required_by_date": "<YYYY-MM-DD or null>",
  "data_residency_required": <true | false>,
  "esg_requirement": <true | false>,
  "preferred_supplier_stated": "<exact supplier name as written, or null>",
  "incumbent_supplier": "<incumbent supplier name if mentioned, or null>",
  "contract_type_requested": "<purchase | framework call-off | subscription | null>",
  "requester_instruction": "<verbatim special instruction or constraint, or null>"
}

Inference rules:
- category_l1 must be one of: IT, Facilities, Professional Services, Marketing
- category_l2 examples by category:
    IT: Laptops, Docking Stations, Desktop Workstations, Mobile Workstations, Monitors,
        Cloud Compute, Managed Cloud Platform Services, Data Engineering Services,
        IT Project Management Services, Software Licenses
    Facilities: Office Furniture, Cleaning Services
    Professional Services: Legal, Consulting, IT Project Management Services
    Marketing: Events, Digital Advertising
- category_l2: always Title Case, match closest taxonomy term
- currency: € → EUR, CHF → CHF, $ → USD
- data_residency_required: default false unless explicitly mentioned
- esg_requirement: default false unless explicitly mentioned
- If price per unit × quantity stated, compute budget_amount as the total
- delivery_country: do NOT infer — leave out of output entirely
"""


def run(request: dict) -> dict:
    client = OpenAI()

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": (
                f"Request text: {request.get('request_text', '')}\n"
                f"Created at: {request.get('created_at', '')}"
            )},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    interpretation = json.loads(response.choices[0].message.content)

    # Merge metadata fields not in free text
    if not interpretation.get("incumbent_supplier") and request.get("incumbent_supplier"):
        interpretation["incumbent_supplier"] = request["incumbent_supplier"]

    delivery = request.get("delivery_countries")
    if delivery:
        interpretation["delivery_country"] = delivery[0] if len(delivery) == 1 else delivery

    # Calculate days_until_required
    interpretation["days_until_required"] = _days_until(
        interpretation.get("required_by_date"),
        request.get("created_at"),
    )

    # Canonical field order
    return {
        "category_l1":              interpretation.get("category_l1"),
        "category_l2":              interpretation.get("category_l2"),
        "quantity":                 interpretation.get("quantity"),
        "unit_of_measure":          interpretation.get("unit_of_measure"),
        "budget_amount":            interpretation.get("budget_amount"),
        "currency":                 interpretation.get("currency"),
        "delivery_country":         interpretation.get("delivery_country"),
        "required_by_date":         interpretation.get("required_by_date"),
        "days_until_required":      interpretation.get("days_until_required"),
        "data_residency_required":  interpretation.get("data_residency_required"),
        "esg_requirement":          interpretation.get("esg_requirement"),
        "preferred_supplier_stated": interpretation.get("preferred_supplier_stated"),
        "incumbent_supplier":       interpretation.get("incumbent_supplier"),
        "contract_type_requested":  interpretation.get("contract_type_requested"),
        "requester_instruction":    interpretation.get("requester_instruction"),
    }


def _days_until(required_by_date, created_at):
    if not required_by_date or not created_at:
        return None
    try:
        req = datetime.strptime(required_by_date, "%Y-%m-%d").date()
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
        return (req - created).days
    except (ValueError, AttributeError):
        return None
