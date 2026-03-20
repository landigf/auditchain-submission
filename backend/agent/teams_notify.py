"""
Teams notification helper — sends proactive messages via the Teams bot's
internal API.

Used by phase2_flow.py DAG steps to notify:
  - Clients:  clarification questions, final decision notifications
  - Internal: review requests, approval requests

The Teams bot must be running and reachable at TEAMS_BOT_URL.
If the bot is not available, notifications are logged but do not block the DAG.
"""
from __future__ import annotations

import os
import requests

TEAMS_BOT_URL = os.environ.get("TEAMS_BOT_URL", "http://teams-bot:3978").rstrip("/")


def _send(target: str, record_id: str, message: str, message_type: str = "notification") -> bool:
    """Send a proactive message. Returns True if sent, False otherwise."""
    try:
        resp = requests.post(
            f"{TEAMS_BOT_URL}/api/proactive/send",
            json={
                "target": target,
                "record_id": record_id,
                "message": message,
                "message_type": message_type,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"[Teams] Sent {message_type} to {target}: {data}")
            return True
        else:
            print(f"[Teams] Failed to send {message_type} to {target}: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"[Teams] Bot not reachable ({type(e).__name__}): {e}")
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def notify_client_clarification(record_id: str, questions: list[str], item_summary: str = "") -> bool:
    """Ask the client clarification questions via Teams."""
    q_lines = "\n".join(f"- {q}" for q in questions)
    msg = (
        f"\u2753 **Clarification needed** for your procurement request"
        f"{f': *{item_summary}*' if item_summary else ''}\n\n"
        f"{q_lines}\n\n"
        f"Please reply here with the additional details, or update your request in the dashboard."
    )
    return _send("client", record_id, msg, "clarification")


def notify_client_decision(record_id: str, decision_type: str, supplier: str | None = None,
                           total_cost: float | None = None, ais_score: int | None = None,
                           is_basket: bool = False, basket_count: int = 0) -> bool:
    """Notify the client of the final decision."""
    emoji = {
        "approved": "\u2705", "escalated": "\u26a0\ufe0f", "rejected": "\u274c"
    }.get(decision_type.lower(), "\U0001f535")

    parts = [f"{emoji} **Decision: {decision_type.upper()}**"]
    if is_basket:
        parts.append(f"Basket: {basket_count} items")
    if supplier:
        parts.append(f"Supplier: **{supplier}**")
    if total_cost:
        parts.append(f"Total: \u20ac{total_cost:,.0f}")
    if ais_score:
        parts.append(f"AIS: {ais_score}/100")

    msg = " | ".join(parts)
    return _send("client", record_id, msg, "notification")


def request_internal_review(record_id: str, item_summary: str,
                            confidence: float, uncertainty_signals: list[dict],
                            top_suppliers: list[dict]) -> bool:
    """Send an internal review request to the ChainIQ reviewer."""
    signals_text = "\n".join(
        f"- {s.get('detail', s.get('label', '?'))}" for s in uncertainty_signals[:5]
    )
    suppliers_text = "\n".join(
        f"- {s.get('name', '?')}: {s.get('final_score', 0):.0f} pts"
        for s in top_suppliers[:3]
    )

    msg = (
        f"\U0001f50d **Internal Review Required** \u2014 `{record_id[:8]}`\n\n"
        f"**Request:** {item_summary}\n"
        f"**Confidence:** {confidence:.0%}\n\n"
        f"**Uncertainty signals:**\n{signals_text}\n\n"
        f"**Top suppliers:**\n{suppliers_text}\n\n"
        f"Reply with **approve** or **reject [reason]** to proceed."
    )
    return _send("internal", record_id, msg, "review")


def request_manager_approval(record_id: str, item_summary: str,
                             budget: float, authority_rule: str,
                             escalation_detail: str) -> bool:
    """Send a manager approval request to the ChainIQ reviewer."""
    msg = (
        f"\U0001f6e1\ufe0f **Manager Approval Required** \u2014 `{record_id[:8]}`\n\n"
        f"**Request:** {item_summary}\n"
        f"**Budget:** \u20ac{budget:,.0f}\n"
        f"**Rule:** {authority_rule}\n"
        f"**Detail:** {escalation_detail}\n\n"
        f"Reply with **approve** or **reject [reason]** to proceed."
    )
    return _send("internal", record_id, msg, "approval")
