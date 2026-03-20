"""
LLM provider abstraction.
Switch with: LLM_PROVIDER=openai (default) or LLM_PROVIDER=claude.
"""
from __future__ import annotations
import os
import json
import logging
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Retry config for rate-limit (429) errors ─────────────────────────────────
_RETRY_DELAYS = [5, 15, 30]  # seconds between retries


def _call_with_retry(fn, *args, **kwargs):
    """Call *fn* and retry up to 3 times on 429 rate-limit errors with exponential backoff."""
    for attempt, delay in enumerate(_RETRY_DELAYS):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            if status == 429:
                logger.warning("Rate-limited (429) on attempt %d/%d — retrying in %ds",
                               attempt + 1, len(_RETRY_DELAYS), delay)
                time.sleep(delay)
            else:
                raise
    # Final attempt — let any exception propagate
    return fn(*args, **kwargs)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()  # "openai" | "claude" | "azure_openai"


def get_provider() -> str:
    return LLM_PROVIDER


def set_provider(provider: str):
    global LLM_PROVIDER
    LLM_PROVIDER = provider.lower()


# ── Shared tool schema ───────────────────────────────────────────────────────

_LINE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "item_description": {"type": "string"},
        "category": {
            "type": "string",
            "enum": ["hardware", "software", "services", "facilities", "default"],
            "description": "Map: IT devices/hardware→hardware; cloud/SaaS/software→software; consulting/professional services→services; office/furniture/facilities→facilities",
        },
        "category_l2": {
            "type": ["string", "null"],
            "description": "Specific subcategory: Laptops, Mobile Workstations, Monitors, Tablets, Cloud Storage, Cloud Compute, Enterprise Software Licenses, SaaS Solutions, Office Chairs, Workstations and Desks, Meeting Room Furniture, etc.",
        },
        "quantity": {"type": "integer"},
        "unit": {"type": "string"},
        "budget_eur": {"type": "number", "description": "Budget in EUR for THIS line item. If a total budget is given for multiple items, split proportionally by estimated cost. Convert CHF×0.95, USD×0.92 if needed."},
        "currency": {"type": ["string", "null"], "description": "Original currency of the budget (EUR, CHF, USD, etc.)"},
        "delivery_country": {"type": ["string", "null"], "description": "ISO 2-letter country code for delivery location."},
        "preferred_supplier_id": {"type": ["string", "null"]},
        "preferred_supplier_name": {"type": ["string", "null"], "description": "Exact supplier name mentioned for this item, if any"},
        "special_requirements": {"type": "array", "items": {"type": "string"}},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
        "missing_fields": {"type": "array", "items": {"type": "string"}, "description": "List ONLY truly absent required fields: budget_eur, quantity, item_description. Do NOT add deadline_days or delivery_country."},
    },
    "required": ["item_description", "category", "quantity", "ambiguities", "missing_fields"],
}

PARSE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        # ── Top-level fields (shared across all line items) ──
        "deadline_days": {"type": "integer", "description": "Days until required_by_date from today (2026-04-01). If absolute date given, calculate days. Applies to entire request."},
        "delivery_country": {"type": ["string", "null"], "description": "ISO 2-letter country code. Zurich/Geneva→CH, Berlin/Munich→DE, Paris→FR, Madrid→ES, Milan→IT, London→GB, etc."},
        "requester_department": {"type": ["string", "null"]},
        # ── Multi-item: array of line items ──
        "line_items": {
            "type": "array",
            "items": _LINE_ITEM_SCHEMA,
            "description": "One entry per distinct product/service in the request. Even single-item requests MUST have exactly one entry.",
        },
        # ── Legacy flat fields for backward compat (single-item) ──
        "item_description": {"type": "string"},
        "category": {
            "type": "string",
            "enum": ["hardware", "software", "services", "facilities", "default"],
        },
        "category_l2": {"type": ["string", "null"]},
        "quantity": {"type": "integer"},
        "unit": {"type": "string"},
        "budget_eur": {"type": "number", "description": "Budget in EUR. Convert CHF×0.95, USD×0.92 if needed."},
        "currency": {"type": ["string", "null"]},
        "preferred_supplier_id": {"type": ["string", "null"]},
        "preferred_supplier_name": {"type": ["string", "null"]},
        "special_requirements": {"type": "array", "items": {"type": "string"}},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
        "missing_fields": {"type": "array", "items": {"type": "string"}, "description": "List ONLY truly absent required fields: budget_eur (if no monetary amount given), quantity (if no number of units given), item_description (if no item described). Do NOT add deadline_days or delivery_country — these have safe defaults (30 days, unspecified country)."},
    },
    "required": ["line_items", "ambiguities", "missing_fields"],
}

PARSE_SYSTEM = (
    "You are a procurement request parser for Chain IQ's enterprise sourcing platform. "
    "Extract structured fields from the purchase request text. "
    "IMPORTANT: Requests may contain MULTIPLE distinct items (e.g. 'chairs AND laptops'). "
    "Always populate the 'line_items' array with one entry per distinct product/service. "
    "Even single-item requests must have exactly one line_items entry. "
    "Also populate the legacy flat fields (item_description, category, etc.) with the FIRST item for backward compatibility. "
    "Shared fields like deadline_days and delivery_country go at the top level. "
    "If a total budget is given for multiple items, split it proportionally across line_items by estimated cost. "
    "Category mapping: IT laptops/devices/hardware→hardware; cloud/SaaS/software licenses→software; "
    "consulting/professional services/IT support→services; office furniture/facilities→facilities. "
    "For category_l2, pick the most specific matching subcategory: "
    "laptops→Laptops, workstations→Mobile Workstations or Desktop Workstations, monitors→Monitors, "
    "tablets→Tablets, chairs→Office Chairs, desks→Workstations and Desks, cloud storage→Cloud Storage, "
    "cloud compute→Cloud Compute, SaaS→SaaS Solutions, software licenses→Enterprise Software Licenses, "
    "consulting→Cloud Architecture Consulting or IT Project Management Services. "
    "For delivery_country: extract from city/country mentions in the request: "
    "Zurich/Bern/Geneva/Switzerland→CH, Berlin/Munich/Hamburg/Germany→DE, Paris/France→FR, "
    "Madrid/Barcelona/Spain→ES, Milan/Rome/Italy→IT, London/UK→GB, Amsterdam/Netherlands→NL, "
    "Vienna/Austria→AT, Warsaw/Poland→PL, Brussels/Belgium→BE. "
    "If budget is in CHF, multiply by 0.95 for EUR. If USD, multiply by 0.92. "
    "If the request has a numeric quantity field AND a quantity in the text that differ, note it in ambiguities. "
    "\n\n"
    "AMBIGUITY DETECTION — Be thorough about detecting ambiguities:\n"
    "- Vague urgency without specific date: 'ASAP', 'urgent', 'move quickly', 'as soon as possible' "
    "→ add to ambiguities: 'No specific delivery deadline provided — only vague urgency'\n"
    "- Contradictory supplier preferences: requesting a specific supplier while noting concerns, restrictions, "
    "or compliance issues with that same supplier → add to ambiguities\n"
    "- Missing delivery location when the request mentions multi-country or cross-border needs\n"
    "- Requests mentioning a supplier that may be restricted or flagged for concerns → note in ambiguities\n"
    "- Specifications that are vague or could be interpreted multiple ways\n"
    "\n"
    "Only add fields to 'missing_fields' if they are truly critical and absent: budget_eur, quantity, item_description. "
    "Never add deadline_days or delivery_country to missing_fields — they have sensible defaults (30 days, unspecified). "
    "But DO flag vague deadlines and missing locations in 'ambiguities' (not missing_fields). "
    "Respond using the provided function/tool only."
)

NARRATIVE_SYSTEM = (
    "You are a procurement compliance officer writing an audit document. "
    "Write a clear, professional 2-3 paragraph reasoning narrative for a sourcing decision. "
    "Explain: what was requested, what rules were applied, why the recommended supplier was chosen "
    "(or why the request was escalated/rejected), and what alternatives were considered. "
    "Write for an auditor. Be factual and precise. Plain text only, no markdown."
)


# ── Normalize parsed result (backward compat) ────────────────────────────────

def _normalize_parsed(result: dict) -> dict:
    """
    Ensure the parsed result always has 'line_items' and legacy flat fields.
    - If LLM returned line_items → keep them + populate flat fields from first item
    - If LLM returned flat fields only (no line_items) → build line_items from flat fields
    """
    line_items = result.get("line_items")

    if not line_items:
        # Legacy response: build line_items from flat fields
        item = {}
        for key in ("item_description", "category", "category_l2", "quantity", "unit",
                     "budget_eur", "currency", "delivery_country",
                     "preferred_supplier_id", "preferred_supplier_name",
                     "special_requirements", "ambiguities", "missing_fields"):
            if key in result:
                item[key] = result[key]
        result["line_items"] = [item]
    else:
        # Multi-item response: populate flat fields from first item for backward compat
        first = line_items[0]
        for key in ("item_description", "category", "category_l2", "quantity", "unit",
                     "budget_eur", "currency", "preferred_supplier_id",
                     "preferred_supplier_name", "special_requirements"):
            if key not in result or result.get(key) is None:
                result[key] = first.get(key)

    # Propagate shared top-level fields into each line item
    for item in result.get("line_items", []):
        if "deadline_days" not in item:
            item["deadline_days"] = result.get("deadline_days")
        if not item.get("delivery_country"):
            item["delivery_country"] = result.get("delivery_country")

    return result


# ── OpenAI provider ──────────────────────────────────────────────────────────

def _openai_parse(raw_text: str) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = _call_with_retry(
        client.chat.completions.create,
        model="gpt-4o",
        temperature=0,
        tools=[{
            "type": "function",
            "function": {
                "name": "submit_parsed_request",
                "description": "Submit the structured procurement fields",
                "parameters": PARSE_INPUT_SCHEMA,
            },
        }],
        tool_choice={"type": "function", "function": {"name": "submit_parsed_request"}},
        messages=[
            {"role": "system", "content": PARSE_SYSTEM},
            {"role": "user", "content": f"Parse this request:\n\n{raw_text}"},
        ],
    )
    tool_call = response.choices[0].message.tool_calls[0]
    return json.loads(tool_call.function.arguments)


def _openai_parse_logged(raw_text: str) -> tuple[dict, dict]:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    user_message = f"Parse this request:\n\n{raw_text}"
    t0 = time.time()
    response = _call_with_retry(
        client.chat.completions.create,
        model="gpt-4o",
        temperature=0,
        tools=[{
            "type": "function",
            "function": {
                "name": "submit_parsed_request",
                "description": "Submit the structured procurement fields",
                "parameters": PARSE_INPUT_SCHEMA,
            },
        }],
        tool_choice={"type": "function", "function": {"name": "submit_parsed_request"}},
        messages=[
            {"role": "system", "content": PARSE_SYSTEM},
            {"role": "user", "content": user_message},
        ],
    )
    latency_ms = int((time.time() - t0) * 1000)
    tool_call = response.choices[0].message.tool_calls[0]
    result = json.loads(tool_call.function.arguments)
    log_data = {
        "id": str(uuid.uuid4()),
        "call_type": "parse",
        "model": "gpt-4o",
        "temperature": 0.0,
        "system_prompt": PARSE_SYSTEM,
        "user_message": user_message,
        "raw_response": json.dumps(result),
        "extracted_result": json.dumps(result),
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parse_method": "llm",
    }
    return result, log_data


def _openai_narrative(context: dict) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = _call_with_retry(
        client.chat.completions.create,
        model="gpt-4o",
        temperature=0,
        messages=[
            {"role": "system", "content": NARRATIVE_SYSTEM},
            {"role": "user", "content": json.dumps(context, indent=2)},
        ],
    )
    return response.choices[0].message.content.strip()


def _openai_narrative_logged(context: dict) -> tuple[str, dict]:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    user_message = json.dumps(context, indent=2)
    t0 = time.time()
    response = _call_with_retry(
        client.chat.completions.create,
        model="gpt-4o",
        temperature=0,
        messages=[
            {"role": "system", "content": NARRATIVE_SYSTEM},
            {"role": "user", "content": user_message},
        ],
    )
    latency_ms = int((time.time() - t0) * 1000)
    narrative = response.choices[0].message.content.strip()
    log_data = {
        "id": str(uuid.uuid4()),
        "call_type": "narrative",
        "model": "gpt-4o",
        "temperature": 0.0,
        "system_prompt": NARRATIVE_SYSTEM,
        "user_message": user_message,
        "raw_response": narrative,
        "extracted_result": narrative,
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parse_method": "llm",
    }
    return narrative, log_data


# ── Claude provider ──────────────────────────────────────────────────────────

def _claude_parse_logged(raw_text: str) -> tuple[dict, dict]:
    import anthropic
    client = anthropic.Anthropic()
    user_message = f"Parse this request:\n\n{raw_text}"
    t0 = time.time()
    response = _call_with_retry(
        client.messages.create,
        model="claude-sonnet-4-5",
        max_tokens=1024,
        temperature=0,
        system=PARSE_SYSTEM,
        tools=[{
            "name": "submit_parsed_request",
            "description": "Submit the structured procurement fields",
            "input_schema": PARSE_INPUT_SCHEMA,
        }],
        tool_choice={"type": "tool", "name": "submit_parsed_request"},
        messages=[{"role": "user", "content": user_message}],
    )
    latency_ms = int((time.time() - t0) * 1000)
    result = None
    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            break
    if result is None:
        raise ValueError("Claude: no tool call returned")
    log_data = {
        "id": str(uuid.uuid4()),
        "call_type": "parse",
        "model": "claude-sonnet-4-5",
        "temperature": 0.0,
        "system_prompt": PARSE_SYSTEM,
        "user_message": user_message,
        "raw_response": json.dumps([{"type": b.type, **({
            "text": b.text} if hasattr(b, "text") else {
            "name": b.name, "input": b.input} if hasattr(b, "name") else {}
        )} for b in response.content]),
        "extracted_result": json.dumps(result),
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parse_method": "llm",
    }
    return result, log_data


def _claude_narrative_logged(context: dict) -> tuple[str, dict]:
    import anthropic
    client = anthropic.Anthropic()
    user_message = json.dumps(context, indent=2)
    t0 = time.time()
    response = _call_with_retry(
        client.messages.create,
        model="claude-sonnet-4-5",
        max_tokens=512,
        temperature=0,
        system=NARRATIVE_SYSTEM,
        messages=[{"role": "user", "content": user_message}],
    )
    latency_ms = int((time.time() - t0) * 1000)
    narrative = response.content[0].text.strip()
    log_data = {
        "id": str(uuid.uuid4()),
        "call_type": "narrative",
        "model": "claude-sonnet-4-5",
        "temperature": 0.0,
        "system_prompt": NARRATIVE_SYSTEM,
        "user_message": user_message,
        "raw_response": narrative,
        "extracted_result": narrative,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parse_method": "llm",
    }
    return narrative, log_data




# ── Azure OpenAI provider ────────────────────────────────────────────────────

def _azure_openai_parse_logged(raw_text: str) -> tuple[dict, dict]:
    from openai import AzureOpenAI
    client = AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
    )
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    user_message = f"Parse this request:\n\n{raw_text}"
    t0 = time.time()
    response = _call_with_retry(
        client.chat.completions.create,
        model=deployment,
        temperature=0,
        tools=[{
            "type": "function",
            "function": {
                "name": "submit_parsed_request",
                "description": "Submit the structured procurement fields",
                "parameters": PARSE_INPUT_SCHEMA,
            },
        }],
        tool_choice={"type": "function", "function": {"name": "submit_parsed_request"}},
        messages=[
            {"role": "system", "content": PARSE_SYSTEM},
            {"role": "user", "content": user_message},
        ],
    )
    latency_ms = int((time.time() - t0) * 1000)
    tool_call = response.choices[0].message.tool_calls[0]
    result = json.loads(tool_call.function.arguments)
    log_data = {
        "id": str(uuid.uuid4()),
        "call_type": "parse",
        "model": f"azure/{deployment}",
        "temperature": 0.0,
        "system_prompt": PARSE_SYSTEM,
        "user_message": user_message,
        "raw_response": json.dumps(result),
        "extracted_result": json.dumps(result),
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parse_method": "llm",
    }
    return result, log_data


def _azure_openai_narrative_logged(context: dict) -> tuple[str, dict]:
    from openai import AzureOpenAI
    client = AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
    )
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    user_message = json.dumps(context, indent=2)
    t0 = time.time()
    response = _call_with_retry(
        client.chat.completions.create,
        model=deployment,
        temperature=0,
        messages=[
            {"role": "system", "content": NARRATIVE_SYSTEM},
            {"role": "user", "content": user_message},
        ],
    )
    latency_ms = int((time.time() - t0) * 1000)
    narrative = response.choices[0].message.content.strip()
    log_data = {
        "id": str(uuid.uuid4()),
        "call_type": "narrative",
        "model": f"azure/{deployment}",
        "temperature": 0.0,
        "system_prompt": NARRATIVE_SYSTEM,
        "user_message": user_message,
        "raw_response": narrative,
        "extracted_result": narrative,
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parse_method": "llm",
    }
    return narrative, log_data


# ── Public interface ─────────────────────────────────────────────────────────

def parse_request(raw_text: str) -> dict:
    result, _ = parse_request_logged(raw_text)
    return _normalize_parsed(result)


def generate_narrative(context: dict) -> str:
    narrative, _ = generate_narrative_logged(context)
    return narrative


def parse_request_logged(raw_text: str) -> tuple[dict, dict]:
    if LLM_PROVIDER == "claude":
        result, log = _claude_parse_logged(raw_text)
    elif LLM_PROVIDER == "azure_openai":
        result, log = _azure_openai_parse_logged(raw_text)
    else:
        result, log = _openai_parse_logged(raw_text)
    return _normalize_parsed(result), log


def generate_narrative_logged(context: dict) -> tuple[str, dict]:
    if LLM_PROVIDER == "claude":
        return _claude_narrative_logged(context)
    if LLM_PROVIDER == "azure_openai":
        return _azure_openai_narrative_logged(context)
    return _openai_narrative_logged(context)
