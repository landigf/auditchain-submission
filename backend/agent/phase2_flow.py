"""
AuditChain Phase 2 — Deterministic Decision Pipeline (Metaflow FlowSpec)
========================================================================
NOW WITH CARDS + MULTI-ITEM BASKET SUPPORT (foreach).

Steps + Cards (foreach fan-out with conditional branching):
    start             → Parsed fields table
    split_lines       → Line item breakdown (foreach fan-out)
    score_line        → Per-item: policy + suppliers + scoring + decision [foreach body]
    merge_lines       → Basket aggregation + weakest-link decision [foreach join]
    decide            → Confidence donut + uncertainty signals (BRANCH 1)
      ├─ internal_review → ChainIQ analyst review (purple) (BRANCH 2)
      │     ├─ mgr_approval → Manager approval gate (purple)
      │     └─ (skip)
      ├─ mgr_approval    → Manager approval gate (purple)
      └─ (skip)
    narrative         → LLM audit narrative (text)
    risk_score_step   → Risk gauge
    ais_step          → AIS breakdown radar
    persist           → DB confirmation + basket columns
    notify_client     → Client notification summary (coral)
    end               → Full audit summary + cross-run comparison chart
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND_ROOT, ".env"), override=True)

from metaflow import FlowSpec, step, card, current, Parameter, Flow
from metaflow.cards import Markdown, Table, Artifact, VegaChart, ProgressBar

from agent.tools import check_policy, query_suppliers, score_suppliers, compute_ais, CATEGORY_WEIGHTS
from agent.llm_client import generate_narrative_logged
from agent.risk_scorer import compute_risk_score
from agent.teams_notify import (
    notify_client_decision, request_internal_review, request_manager_approval,
)
from db.database import SessionLocal, init_db
from db.models import AuditRecord, LLMCallLog, Rule

AGENT_VERSION = "2.2.0-basket"


class Phase2Flow(FlowSpec):
    """
    AuditChain Phase 2 with live Cards + multi-item basket.
    Submit via React UI → watch Cards build in Metaflow UI at :3000.
    """

    structured_json = Parameter(
        "structured_json",
        help='Validated structured request JSON from Phase 1.',
        required=True,
    )
    requester_context_json = Parameter(
        "requester_context_json", default="{}",
        help='Requester profile JSON.',
    )
    raw_request = Parameter(
        "raw_request", default="",
        help="Original free-text request (audit trail display).",
    )
    parent_record_id = Parameter(
        "parent_record_id", default="",
        help="Phase 1 record_id (links clarification chain).",
    )

    # ── Start ─────────────────────────────────────────────────────────────────

    @card(type="blank", id="parse", refresh_interval=3)
    @step
    def start(self):
        """Unpack inputs and display extracted fields."""
        self.record_id = str(uuid.uuid4())
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.structured = json.loads(self.structured_json)
        self.ctx = json.loads(self.requester_context_json) if self.requester_context_json.strip() not in ("", "{}") else {}
        self.parent_id = self.parent_record_id or None
        self.trace: list = []
        self.llm_logs: list = []

        if self.ctx.get("spending_authority_eur"):
            self.structured["_spending_authority_eur"] = float(self.ctx["spending_authority_eur"])

        # ── Card: extracted fields ───────────────────────────────────────
        current.card["parse"].append(Markdown("## Request intake"))
        if self.raw_request:
            current.card["parse"].append(Markdown(f"> {self.raw_request[:120]}"))

        rows = []
        for key in ["item_description", "category", "category_l2", "quantity",
                     "budget_eur", "currency", "deadline_days", "delivery_country",
                     "preferred_supplier_name"]:
            val = self.structured.get(key)
            status = "\u2713" if val is not None else "\u26a0"
            rows.append([Markdown(f"`{key}`"),
                         Markdown(f"**{val}**" if val else "*null*"),
                         Markdown(status)])
        current.card["parse"].append(Table(rows, headers=["Field", "Value", ""]))

        # Show line items if multi-item
        line_items = self.structured.get("line_items", [])
        if len(line_items) > 1:
            current.card["parse"].append(Markdown(f"### Multi-item basket: {len(line_items)} items"))
            for i, li in enumerate(line_items):
                current.card["parse"].append(Markdown(
                    f"- **Line {i+1}**: {li.get('item_description', '?')[:40]} "
                    f"({li.get('category', '?')}, qty {li.get('quantity', '?')})"))

        ambiguities = self.structured.get("ambiguities", [])
        if ambiguities:
            current.card["parse"].append(Markdown("### \u26a0 Ambiguities\n" + "\n".join(f"- {a}" for a in ambiguities)))

        current.card["parse"].refresh()

        print(f"[start] record_id={self.record_id[:8]}")
        self.next(self.split_lines)

    # ── Split lines (foreach fan-out) ─────────────────────────────────────────

    @card(type="blank", id="split", refresh_interval=3)
    @step
    def split_lines(self):
        """Extract line items and fan out to per-item processing."""
        t0 = time.time()

        line_items = self.structured.get("line_items")
        if not line_items:
            # Backward compat: single-item request without line_items
            line_items = [self.structured]

        # Build per-line structured dicts with shared fields
        self.line_items_for_foreach = []
        for i, item in enumerate(line_items):
            line = item.copy()
            line["_line_idx"] = i
            if not line.get("deadline_days"):
                line["deadline_days"] = self.structured.get("deadline_days")
            if not line.get("delivery_country"):
                line["delivery_country"] = self.structured.get("delivery_country")
            self.line_items_for_foreach.append(line)

        n = len(self.line_items_for_foreach)

        # Card
        current.card["split"].append(Markdown(
            f"## {'Multi-item basket' if n > 1 else 'Single item'}: {n} line(s)"))
        rows = []
        for item in self.line_items_for_foreach:
            rows.append([
                Markdown(f"**{item.get('_line_idx', 0) + 1}**"),
                Markdown((item.get("item_description") or "?")[:40]),
                Markdown(item.get("category") or "?"),
                Markdown(str(item.get("quantity") or "?")),
                Markdown(f"\u20ac{item.get('budget_eur', 0):,.0f}" if item.get("budget_eur") else "?"),
            ])
        current.card["split"].append(Table(rows, headers=["#", "Item", "Category", "Qty", "Budget"]))
        current.card["split"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": "split_lines", "ms": ms, "llm": False,
                           "summary": f"{n} line items"})
        print(f"[split_lines] {n} line items ({ms}ms)")
        self.next(self.score_line, foreach='line_items_for_foreach')

    # ── Per-line scoring (foreach body) ────────────────────────────────────

    @card(type="blank", id="line_score", refresh_interval=3)
    @step
    def score_line(self):
        """Full per-line pipeline: policy -> filter -> feasibility -> score -> decision."""
        from agent.pipeline import make_decision
        from agent.fuzzy_policy import (
            fuzzy_threshold_classify, fuzzy_score_supplier,
            sensitivity_analysis, generate_counterfactuals, fuzzy_confidence_gate,
        )

        t0 = time.time()
        line = self.input.copy()
        line_idx = line.pop("_line_idx", 0)

        # Inherit spending authority from top-level structured
        if "_spending_authority_eur" in self.structured:
            line["_spending_authority_eur"] = self.structured["_spending_authority_eur"]

        db = SessionLocal()
        try:
            # ── 1. Policy check ────────────────────────────────────────
            rules = db.query(Rule).filter(Rule.active == True).all()
            line_policy = check_policy(line, rules)

            budget = line.get("budget_eur") or line.get("_budget") or 0
            currency = line.get("currency") or "EUR"
            line_threshold = fuzzy_threshold_classify(budget, currency) if budget > 0 else {}
            if line_threshold:
                line_policy["fuzzy_threshold"] = line_threshold

            # ── 2. Filter suppliers ────────────────────────────────────
            line_suppliers = query_suppliers(line, db)
            candidates = line_suppliers.get("candidates", [])
            if candidates:
                line["_preferred_tier"] = candidates[0].get("preferred_tier", "approved")

            # ── 3. Feasibility + auto-adjust ───────────────────────────
            infeasibility = line_suppliers.get("infeasibility") or {}
            infeasible = infeasibility.get("infeasible", False)
            ambiguities = line.get("ambiguities", [])

            if infeasible:
                max_affordable = infeasibility.get("max_affordable_qty", 0)
                if max_affordable > 0:
                    line["_original_quantity"] = line.get("quantity", 1)
                    line["quantity"] = max_affordable

            # ── 4. Score suppliers ─────────────────────────────────────
            line_scoring = score_suppliers(candidates, line, db=db)
            scored = line_scoring.get("scored", [])

            # Fuzzy scoring overlay
            line_fuzzy_scores = []
            for s in scored:
                bd = s.get("score_breakdown", {})
                fs = fuzzy_score_supplier(
                    price_normalized=bd.get("price_score", 50) / 100,
                    delivery_normalized=bd.get("delivery_score", 50) / 100,
                    compliance_normalized=bd.get("compliance_score", 50) / 100,
                    esg_normalized=bd.get("esg_score_normalized", 50) / 100,
                )
                s["fuzzy_score"] = fs["score"]
                s["fuzzy_linguistic"] = fs["linguistic"]
                s["fuzzy_rules_fired"] = fs["rules_fired"]
                line_fuzzy_scores.append(fs)

            # Sensitivity
            category = line.get("category", "default")
            weights = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["default"])
            line_sens = sensitivity_analysis(scored, weights) if scored else {}
            line_cfs = generate_counterfactuals(scored, line_fuzzy_scores)

            # ── 5. Per-line decision ───────────────────────────────────
            line_decision = make_decision(line, line_policy, line_scoring, line_suppliers)

            line_conf = fuzzy_confidence_gate(
                threshold_result=line_threshold,
                top_supplier_score=scored[0]["score"] if scored else 0,
                second_supplier_score=scored[1]["score"] if len(scored) > 1 else None,
                num_candidates=line_suppliers.get("total_eligible", 0),
                has_ambiguities=bool(line.get("ambiguities")),
                has_missing_fields=bool(line.get("missing_fields")),
            )
            line_decision["confidence"] = line_conf["confidence"]
            line_decision["confidence_label"] = line_conf.get("confidence_label", "unknown")

            if line_conf["should_escalate"] and line_decision["decision_type"] == "approved":
                line_decision["decision_type"] = "escalated"
                line_decision["escalation_reason"] = line_conf.get("escalation_reason", "Low confidence")

        finally:
            db.close()

        # Store all per-line results as self attributes (for join)
        self.line_idx = line_idx
        self.line_structured = line
        self.line_item_desc = (line.get("item_description") or "Unknown")[:50]
        self.line_category = line.get("category", "default")
        self.line_policy_results = line_policy
        self.line_threshold_result = line_threshold
        self.line_supplier_results = line_suppliers
        self.line_scoring_result = line_scoring
        self.line_fuzzy_scores = line_fuzzy_scores
        self.line_sens_result = line_sens
        self.line_counterfactuals = line_cfs
        self.line_decision = line_decision
        self.line_confidence_result = line_conf

        # ── Card ───────────────────────────────────────────────────────
        dtype = line_decision["decision_type"].upper()
        eligible = line_suppliers.get("total_eligible", 0)
        viol = len(line_policy.get("violations", []))
        esc = len(line_policy.get("escalations", []))

        current.card["line_score"].append(Markdown(
            f"## Line {line_idx + 1}: {self.line_item_desc}"))
        current.card["line_score"].append(Markdown(
            f"**Category**: {category} | **Qty**: {line.get('quantity', '?')} | "
            f"**Budget**: \u20ac{budget:,.0f}"))

        # Policy
        current.card["line_score"].append(Markdown(
            f"**Policy**: {viol} violations, {esc} escalations | "
            f"**Suppliers**: {eligible} eligible"))

        # Ranking table
        if scored:
            rank_rows = [[Markdown(f"#{s['rank']}"), Markdown(s["name"]),
                           Markdown(f"{s['score']:.1f}"),
                           Markdown(f"\u20ac{s.get('total_cost_eur', 0):,.0f}")]
                          for s in scored[:5]]
            current.card["line_score"].append(
                Table(rank_rows, headers=["#", "Supplier", "Score", "Cost"]))

        # Grouped bar chart for this line item
        radar_data = []
        for s in scored[:5]:
            bd = s.get("score_breakdown", {})
            for crit in ["price", "delivery", "compliance", "esg"]:
                score_key = f"{crit}_score" if crit != "esg" else "esg_score_normalized"
                radar_data.append({"supplier": s["name"][:15], "criterion": crit,
                                   "score": bd.get(score_key, 50)})
        if radar_data:
            current.card["line_score"].append(VegaChart({
                "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                "title": f"Score breakdown \u2014 Line {line_idx + 1}",
                "data": {"values": radar_data},
                "mark": "bar",
                "encoding": {
                    "column": {"field": "criterion", "type": "nominal"},
                    "x": {"field": "supplier", "type": "nominal",
                           "axis": {"labelAngle": -35, "labelLimit": 60}},
                    "y": {"field": "score", "type": "quantitative",
                           "scale": {"domain": [0, 100]}},
                    "color": {"field": "supplier", "scale": {"scheme": "tableau10"}},
                },
                "width": 70, "height": 140
            }))

        # Sensitivity badge
        if line_sens:
            current.card["line_score"].append(Markdown(
                f"**Sensitivity**: {'Stable' if line_sens.get('ranking_stable') else 'UNSTABLE'} "
                f"({line_sens.get('stability_score', 0):.0%})"))

        # Decision summary
        conf_pct = round(line_conf["confidence"] * 100)
        rec = line_decision.get("recommended_supplier")
        current.card["line_score"].append(Markdown(
            f"### Decision: **{dtype}** | Confidence: {conf_pct}%"))
        if rec:
            current.card["line_score"].append(Markdown(
                f"Recommended: **{rec['name']}** \u2014 \u20ac{rec.get('total_cost_eur', 0):,.0f}"))
        if infeasible:
            current.card["line_score"].append(Markdown(
                f"*Budget infeasible \u2014 quantity adjusted to {line.get('quantity')}*"))

        current.card["line_score"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": f"score_line_{line_idx}", "ms": ms, "llm": False,
                           "summary": f"Line {line_idx+1}: {dtype}, {eligible} suppliers"})
        print(f"[score_line] Line {line_idx+1}: {dtype} ({ms}ms)")
        self.next(self.merge_lines)

    # ── Merge lines (foreach join) ─────────────────────────────────────────

    @card(type="blank", id="basket_summary", refresh_interval=3)
    @step
    def merge_lines(self, inputs):
        """Join per-line results into basket decision (weakest-link)."""
        t0 = time.time()

        self.merge_artifacts(inputs, exclude=[
            "line_policy_results", "line_threshold_result", "line_supplier_results",
            "line_scoring_result", "line_fuzzy_scores", "line_sens_result",
            "line_decision", "line_confidence_result", "line_counterfactuals",
            "line_idx", "line_structured", "line_category", "line_item_desc",
            "line_items_for_foreach", "trace",
        ])

        # Manually merge traces from all foreach branches
        self.trace = []
        for inp in inputs:
            self.trace.extend(getattr(inp, "trace", []))

        # Collect per-line results
        self.basket_lines = []
        for inp in inputs:
            self.basket_lines.append({
                "line_idx": inp.line_idx,
                "item_description": inp.line_item_desc,
                "category": inp.line_category,
                "structured": inp.line_structured,
                "policy_results": inp.line_policy_results,
                "threshold_result": inp.line_threshold_result,
                "supplier_results": inp.line_supplier_results,
                "scoring_result": inp.line_scoring_result,
                "fuzzy_scores": inp.line_fuzzy_scores,
                "sens_result": inp.line_sens_result,
                "decision": inp.line_decision,
                "confidence_result": inp.line_confidence_result,
                "counterfactuals": inp.line_counterfactuals,
            })
        self.basket_lines.sort(key=lambda x: x["line_idx"])
        self.is_basket = len(self.basket_lines) > 1

        # ── Weakest-link basket decision ───────────────────────────────
        decision_priority = {"rejected": 0, "escalated": 1, "approved": 2}
        weakest = min(self.basket_lines,
                      key=lambda x: decision_priority.get(x["decision"]["decision_type"], 2))
        self.decision = weakest["decision"].copy()

        if self.is_basket:
            esc_lines = [bl for bl in self.basket_lines
                         if bl["decision"]["decision_type"] == "escalated"]
            rej_lines = [bl for bl in self.basket_lines
                         if bl["decision"]["decision_type"] == "rejected"]
            if rej_lines:
                self.decision["decision_type"] = "rejected"
                reasons = [
                    f"Line {bl['line_idx']+1} ({bl['item_description']}): "
                    f"{bl['decision'].get('rejection_reason', '?')}"
                    for bl in rej_lines
                ]
                self.decision["rejection_reason"] = "; ".join(reasons)
            elif esc_lines:
                self.decision["decision_type"] = "escalated"
                reasons = [
                    f"Line {bl['line_idx']+1} ({bl['item_description']}): "
                    f"{bl['decision'].get('escalation_reason', '?')}"
                    for bl in esc_lines
                ]
                self.decision["escalation_reason"] = "; ".join(reasons)

            # Basket confidence = minimum across all lines
            self.decision["confidence"] = min(
                bl["decision"].get("confidence", 0) for bl in self.basket_lines)

        # Aggregate total cost
        self.basket_total_cost = 0.0
        for bl in self.basket_lines:
            rec = bl["decision"].get("recommended_supplier")
            if rec and "total_cost_eur" in rec:
                self.basket_total_cost += rec["total_cost_eur"]

        # Set downstream artifacts from first line (backward compat)
        first = self.basket_lines[0]
        self.policy_results = first["policy_results"]
        self.threshold_result = first["threshold_result"]
        self.supplier_results = first["supplier_results"]
        self.scoring_result = first["scoring_result"]
        self.fuzzy_scores = first["fuzzy_scores"]
        self.sens_result = first["sens_result"]
        self.confidence_result = first["confidence_result"]
        self.counterfactuals = first["counterfactuals"]

        # For multi-item: merge policy results across all lines
        if self.is_basket:
            all_esc, all_viol, all_warn = [], [], []
            for bl in self.basket_lines:
                pr = bl["policy_results"]
                all_esc.extend(pr.get("escalations", []))
                all_viol.extend(pr.get("violations", []))
                all_warn.extend(pr.get("warnings", []))
            self.policy_results = {
                "violations": all_viol, "warnings": all_warn,
                "escalations": all_esc,
                "all_clear": len(all_viol) == 0 and len(all_esc) == 0,
                "fuzzy_threshold": first["threshold_result"],
            }
            # Basket confidence result: should_escalate if ANY line says so
            self.confidence_result = first["confidence_result"].copy()
            self.confidence_result["should_escalate"] = any(
                bl["confidence_result"].get("should_escalate", False)
                for bl in self.basket_lines
            )

        # Basket line decisions for DB persistence
        self.basket_line_decisions = [
            {
                "line_idx": bl["line_idx"],
                "category": bl["category"],
                "item": bl["item_description"],
                "decision_type": bl["decision"]["decision_type"],
                "supplier": (bl["decision"]["recommended_supplier"]["name"]
                             if bl["decision"].get("recommended_supplier") else None),
                "cost": (bl["decision"]["recommended_supplier"].get("total_cost_eur")
                         if bl["decision"].get("recommended_supplier") else None),
                "confidence": bl["decision"].get("confidence", 0),
            }
            for bl in self.basket_lines
        ]

        # ── Card ───────────────────────────────────────────────────────
        basket_label = "Basket" if self.is_basket else "Single-item"
        current.card["basket_summary"].append(Markdown(
            f"## {basket_label} Summary"))

        if self.is_basket:
            rows = []
            for bl in self.basket_lines:
                rec = bl["decision"].get("recommended_supplier")
                dt = bl["decision"]["decision_type"].upper()
                cost_str = f"\u20ac{rec.get('total_cost_eur', 0):,.0f}" if rec else "\u2014"
                rows.append([
                    Markdown(f"**{bl['line_idx']+1}**"),
                    Markdown(bl["item_description"]),
                    Markdown(bl["category"]),
                    Markdown(f"**{dt}**"),
                    Markdown(rec["name"] if rec else "\u2014"),
                    Markdown(cost_str),
                ])
            current.card["basket_summary"].append(
                Table(rows, headers=["#", "Item", "Category", "Decision", "Supplier", "Cost"]))
            current.card["basket_summary"].append(Markdown(
                f"### Basket total: \u20ac{self.basket_total_cost:,.0f}"))
            current.card["basket_summary"].append(Markdown(
                f"### Basket decision: **{self.decision['decision_type'].upper()}** (weakest-link)"))
        else:
            bl = self.basket_lines[0]
            rec = bl["decision"].get("recommended_supplier")
            desc = bl["item_description"]
            dt = bl["decision"]["decision_type"].upper()
            supp = rec["name"] if rec else "N/A"
            cost_str = f"\u20ac{rec.get('total_cost_eur', 0):,.0f}" if rec else ""
            current.card["basket_summary"].append(Markdown(
                f"**{desc}** ({bl['category']})\n"
                f"Decision: **{dt}** | Supplier: **{supp}** | {cost_str}"))

        current.card["basket_summary"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": "merge_lines", "ms": ms, "llm": False,
                           "summary": f"{len(self.basket_lines)} lines, basket={self.decision['decision_type']}"})
        print(f"[merge_lines] {len(self.basket_lines)} lines, "
              f"decision={self.decision['decision_type']} ({ms}ms)")
        self.next(self.decide)

    # ── Decision ──────────────────────────────────────────────────────────────

    @card(type="blank", id="decision", refresh_interval=3)
    @step
    def decide(self):
        """Basket-level decision visualization + 3-way branch."""
        from agent.fuzzy_policy import generate_counterfactuals

        t0 = time.time()

        # Decision and confidence are pre-computed by merge_lines
        cr = self.confidence_result
        scored = self.scoring_result.get("scored", [])

        dtype = self.decision["decision_type"].upper()
        conf_pct = round(self.decision.get("confidence", 0) * 100)
        gauge_color = "#1D9E75" if conf_pct >= 75 else "#EF9F27" if conf_pct >= 45 else "#E24B4A"

        # ── Card: decision ───────────────────────────────────────────────
        current.card["decision"].append(Markdown(f"## Decision: **{dtype}**"))

        # Basket info
        if getattr(self, "is_basket", False):
            current.card["decision"].append(Markdown(
                f"**Basket**: {len(self.basket_lines)} items | "
                f"Total: \u20ac{self.basket_total_cost:,.0f}"))
            for bld in self.basket_line_decisions:
                supp = bld["supplier"] or "N/A"
                cost = f"\u20ac{bld['cost']:,.0f}" if bld.get("cost") else ""
                current.card["decision"].append(Markdown(
                    f"- Line {bld['line_idx']+1}: {bld['item']} \u2192 "
                    f"**{bld['decision_type'].upper()}** ({supp} {cost})"))

        # Confidence donut
        current.card["decision"].append(VegaChart({
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "title": f"Confidence: {conf_pct}% ({cr.get('confidence_label', '?')})",
            "data": {"values": [
                {"category": "Confidence", "value": conf_pct},
                {"category": "Remaining", "value": 100 - conf_pct},
            ]},
            "mark": {"type": "arc", "innerRadius": 50},
            "encoding": {
                "theta": {"field": "value", "type": "quantitative"},
                "color": {"field": "category", "scale": {"range": [gauge_color, "#E8E8E8"]},
                          "legend": None},
            },
            "width": 200, "height": 200
        }))

        # Uncertainty signals
        if cr.get("uncertainty_signals"):
            current.card["decision"].append(Markdown("### Uncertainty signals"))
            sig_rows = [[Markdown(s["signal"]), Markdown(f"**{s['severity']:.0%}**"),
                          Markdown(s["detail"])] for s in cr["uncertainty_signals"]]
            current.card["decision"].append(Table(sig_rows, headers=["Signal", "Severity", "Detail"]))

        # Counterfactuals
        self.counterfactuals = generate_counterfactuals(scored, self.fuzzy_scores if hasattr(self, "fuzzy_scores") else [])
        if self.counterfactuals:
            current.card["decision"].append(Markdown("### What would need to change?"))
            for cf in self.counterfactuals[:3]:
                lines = f"**{cf['supplier_name']}** (gap: {cf['gap_to_winner']:.1f} pts)\n"
                for wf in cf["what_if"][:2]:
                    lines += f"- {wf}\n"
                current.card["decision"].append(Markdown(lines))

        # Recommendation
        if self.decision.get("recommended_supplier"):
            s = self.decision["recommended_supplier"]
            current.card["decision"].append(Markdown(
                f"### \u27a4 Recommendation: **{s['name']}**\n"
                f"Score: {s['score']:.1f} | Cost: \u20ac{s.get('total_cost_eur', 0):,.0f} | "
                f"Fuzzy: {s.get('fuzzy_linguistic', '?')}"
            ))

        current.card["decision"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": "decide", "ms": ms, "llm": False, "summary": dtype})
        print(f"[decide] {dtype} confidence={conf_pct}% ({ms}ms)")

        # ── 3-way branch using fuzzy gate ──────────────────────────────
        has_authority = any(
            e.get("rule_id") == "AT-AUTHORITY"
            for e in self.policy_results.get("escalations", [])
        )

        if cr.get("should_escalate"):
            self.decide_branch = "internal_review"
        elif has_authority:
            self.decide_branch = "mgr_approval"
        else:
            self.decide_branch = "narrative"
        self.next({
            "internal_review": self.internal_review,
            "mgr_approval": self.mgr_approval,
            "narrative": self.narrative,
        }, condition="decide_branch")

    # ── Internal review (ChainIQ analyst) ────────────────────────────────────

    @card(type="blank", id="review", refresh_interval=3)
    @step
    def internal_review(self):
        """ChainIQ analyst review for low-confidence decisions."""
        t0 = time.time()

        cr = self.confidence_result
        scored = self.scoring_result.get("scored", [])

        current.card["review"].append(Markdown("## ChainIQ Internal Review Required"))

        # Confidence summary
        conf_pct = round(cr["confidence"] * 100)
        current.card["review"].append(Table([
            [Markdown("**Confidence**"), Markdown(f"**{conf_pct}%** ({cr.get('confidence_label', '?')})")],
            [Markdown("**Decision**"), Markdown(f"**{self.decision['decision_type'].upper()}**")],
            [Markdown("**Escalation reason**"), Markdown(cr.get("escalation_reason", self.decision.get("escalation_reason", "Low confidence")))],
        ], headers=["", ""]))

        # Uncertainty signals
        if cr.get("uncertainty_signals"):
            current.card["review"].append(Markdown("### Uncertainty signals"))
            sig_rows = [[Markdown(s["signal"]), Markdown(f"**{s['severity']:.0%}**"),
                          Markdown(s["detail"])] for s in cr["uncertainty_signals"]]
            current.card["review"].append(Table(sig_rows, headers=["Signal", "Severity", "Detail"]))

        # Top suppliers comparison
        if len(scored) >= 2:
            gap = scored[0]["score"] - scored[1]["score"]
            current.card["review"].append(Markdown(
                f"### Supplier comparison\n"
                f"- **#1 {scored[0]['name']}**: {scored[0]['score']:.1f} pts\n"
                f"- **#2 {scored[1]['name']}**: {scored[1]['score']:.1f} pts\n"
                f"- Score gap: **{gap:.1f}** pts " + ("(narrow — ranking fragile)" if gap < 5 else "")
            ))

        # Historical awards for top supplier
        if scored:
            top = scored[0]
            hist_bonus = top.get("historical_bonus", 0)
            hist_note = top.get("score_breakdown", {}).get("historical_note", "")
            if hist_bonus > 0:
                current.card["review"].append(Markdown(
                    f"### Historical performance \u2014 {top['name']}\n"
                    f"Award bonus: **+{hist_bonus:.1f}** | {hist_note}"
                ))
            else:
                current.card["review"].append(Markdown(
                    f"### Historical performance \u2014 {top['name']}\n"
                    f"No prior awards in this category"
                ))

        # Sensitivity
        sr = self.sens_result if hasattr(self, "sens_result") else {}
        if sr:
            current.card["review"].append(Markdown(
                f"### Sensitivity: {'Stable' if sr.get('ranking_stable') else 'UNSTABLE'} "
                f"({sr.get('stability_score', 0):.0%})"
            ))
            if sr.get("flips"):
                for f in sr["flips"][:3]:
                    current.card["review"].append(Markdown(
                        f"- {f['criterion']} {f['direction']} flips winner to **{f['new_winner_name']}**"
                    ))

        # ── Teams: send review request to ChainIQ internal ─────────
        item_summary = self.structured.get("item_description", self.raw_request[:80])
        request_internal_review(
            record_id=self.record_id,
            item_summary=item_summary,
            confidence=cr["confidence"],
            uncertainty_signals=cr.get("uncertainty_signals", []),
            top_suppliers=scored[:3],
        )

        # Analyst decision (simulated for demo — in production, would await response)
        current.card["review"].append(Markdown(
            "---\n### Analyst Decision: **CONFIRMED** *(simulated for demo)*\n"
            "*Internal review complete \u2014 proceeding with recommendation*"
        ))

        current.card["review"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": "internal_review", "ms": ms, "llm": False,
                           "summary": f"confidence={conf_pct}%, confirmed, teams_notified"})
        print(f"[internal_review] confirmed ({ms}ms)")

        # Chain: if authority escalation also applies, route to mgr_approval
        has_authority = any(
            e.get("rule_id") == "AT-AUTHORITY"
            for e in self.policy_results.get("escalations", [])
        )
        self.review_branch = "mgr_approval" if has_authority else "narrative"
        self.next({"mgr_approval": self.mgr_approval, "narrative": self.narrative}, condition="review_branch")

    # ── Manager approval ─────────────────────────────────────────────────────

    @card(type="blank", id="approval", refresh_interval=3)
    @step
    def mgr_approval(self):
        """Procurement Manager approval gate for AT-AUTHORITY escalations."""
        t0 = time.time()

        current.card["approval"].append(Markdown("## Procurement Manager Approval Required"))

        # Find the AT-AUTHORITY escalation details
        authority_esc = None
        for e in self.policy_results.get("escalations", []):
            if e.get("rule_id") == "AT-AUTHORITY":
                authority_esc = e
                break

        budget = self.structured.get("budget_eur") or self.structured.get("_budget") or 0
        spending_auth = self.structured.get("_spending_authority_eur", 0)

        current.card["approval"].append(Table([
            [Markdown("**Request budget**"), Markdown(f"\u20ac{budget:,.0f}")],
            [Markdown("**Requester authority**"), Markdown(f"\u20ac{spending_auth:,.0f}")],
            [Markdown("**Overage**"), Markdown(f"\u20ac{budget - spending_auth:,.0f}")],
            [Markdown("**Rule**"), Markdown(f"`{authority_esc['rule_id']}`" if authority_esc else "AT-AUTHORITY")],
            [Markdown("**Detail**"), Markdown(authority_esc["detail"] if authority_esc else "Budget exceeds spending authority")],
        ], headers=["", ""]))

        # ── Teams: send approval request to ChainIQ internal ──────
        item_summary = self.structured.get("item_description", self.raw_request[:80])
        rule_id = authority_esc["rule_id"] if authority_esc else "AT-AUTHORITY"
        detail = authority_esc["detail"] if authority_esc else "Budget exceeds spending authority"
        request_manager_approval(
            record_id=self.record_id,
            item_summary=item_summary,
            budget=budget,
            authority_rule=rule_id,
            escalation_detail=detail,
        )

        # Required approver (from escalation rules)
        current.card["approval"].append(Markdown(
            "### Required approver\n"
            "**Procurement Manager** (per ER-003: Spending authority exceeded)\n\n"
            "---\n"
            "### Manager Decision: **APPROVED** *(simulated for demo)*\n"
            "*Authorization granted \u2014 proceeding with recommendation*"
        ))

        current.card["approval"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": "mgr_approval", "ms": ms, "llm": False,
                           "summary": f"authority \u20ac{spending_auth:,.0f} < budget \u20ac{budget:,.0f}, approved, teams_notified"})
        print(f"[mgr_approval] approved ({ms}ms)")
        self.next(self.narrative)

    # ── Narrative (LLM) ───────────────────────────────────────────────────────

    @card(type="blank", id="narrative_card", refresh_interval=5)
    @step
    def narrative(self):
        """Claude writes audit explanation AFTER decision is locked."""
        t0 = time.time()

        progress = ProgressBar(max=2, label="Generating narrative")
        current.card["narrative_card"].append(Markdown("## Audit narrative"))
        current.card["narrative_card"].append(progress)
        current.card["narrative_card"].refresh()

        scored_list = self.scoring_result.get("scored", [])

        # For basket, include all line decisions in context
        basket_context = None
        if getattr(self, "is_basket", False):
            basket_context = {
                "is_basket": True,
                "line_count": len(self.basket_lines),
                "line_decisions": self.basket_line_decisions,
                "basket_total_cost": self.basket_total_cost,
            }

        context = {
            "request": self.structured,
            "policy_checks": {
                "escalations": self.policy_results.get("escalations", []),
                "warnings": self.policy_results.get("warnings", []),
                "violations": self.policy_results.get("violations", []),
            },
            "top_suppliers": scored_list[:3],
            "decision": self.decision,
        }
        if basket_context:
            context["basket"] = basket_context

        self.narrative_text, narr_log = generate_narrative_logged(context)
        progress.update(1)

        if narr_log:
            narr_log["record_id"] = self.record_id
            self.llm_logs.append(narr_log)
        self.decision["reasoning_narrative"] = self.narrative_text

        # Show narrative in card
        current.card["narrative_card"].append(Markdown(f"---\n{self.narrative_text}"))
        current.card["narrative_card"].append(Markdown(
            f"*Model: {narr_log.get('model', '?')} | "
            f"Tokens: {narr_log.get('input_tokens', 0)}+{narr_log.get('output_tokens', 0)} | "
            f"Latency: {narr_log.get('latency_ms', 0)}ms*"
        ))
        progress.update(2)
        current.card["narrative_card"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": "narrative", "ms": ms, "llm": True,
                           "summary": f"{len(self.narrative_text)} chars"})
        print(f"[narrative] {len(self.narrative_text)} chars ({ms}ms)")
        self.next(self.risk_score_step)

    # ── Risk score ────────────────────────────────────────────────────────────

    @card(type="blank", id="risk", refresh_interval=3)
    @step
    def risk_score_step(self):
        """Risk score with breakdown."""
        t0 = time.time()
        self.risk_result = compute_risk_score(self.structured, self.ctx)
        self.risk_score_val = self.risk_result["score"]
        approach = self.risk_result["approach"]

        # Card: risk gauge
        risk_color = "#1D9E75" if self.risk_score_val < 40 else "#EF9F27" if self.risk_score_val < 70 else "#E24B4A"
        current.card["risk"].append(Markdown(f"## Risk: {self.risk_score_val}/100 ({approach})"))
        current.card["risk"].append(VegaChart({
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": [
                {"label": "Risk", "value": self.risk_score_val},
                {"label": "Safe", "value": 100 - self.risk_score_val},
            ]},
            "mark": {"type": "arc", "innerRadius": 40},
            "encoding": {
                "theta": {"field": "value", "type": "quantitative"},
                "color": {"field": "label", "scale": {"range": [risk_color, "#E8E8E8"]}, "legend": None},
            },
            "width": 160, "height": 160
        }))

        # Breakdown
        bd = self.risk_result.get("breakdown", {})
        if bd:
            bd_rows = [[Markdown(k), Markdown(f"{v:.3f}")] for k, v in bd.items()]
            current.card["risk"].append(Table(bd_rows, headers=["Factor", "Value"]))

        # Fuzzy memberships if available
        if self.risk_result.get("memberships"):
            current.card["risk"].append(Markdown("### Fuzzy memberships"))
            current.card["risk"].append(Artifact(self.risk_result["memberships"]))

        current.card["risk"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": "risk_score", "ms": ms, "llm": False,
                           "summary": f"{self.risk_score_val}/100 ({approach})"})
        print(f"[risk_score] {self.risk_score_val}/100 ({ms}ms)")
        self.next(self.ais_step)

    # ── AIS ───────────────────────────────────────────────────────────────────

    @card(type="blank", id="ais", refresh_interval=3)
    @step
    def ais_step(self):
        """Decision Quality Score breakdown."""
        t0 = time.time()
        self.ais = compute_ais(
            self.structured, self.policy_results,
            self.supplier_results, self.scoring_result, self.decision,
        )

        # Card: DQS bar chart
        components = self.ais.get("components", {})
        max_pts = {"request_completeness": 20, "policy_coverage": 15, "traceability": 25,
                   "supplier_justification": 20, "decision_correctness": 20}

        ais_data = [{"component": k.replace("_", " "), "score": v, "max": max_pts.get(k, 20)}
                     for k, v in components.items()]

        current.card["ais"].append(Markdown(
            f"## Decision Quality: {self.ais['score']}/100 \u2014 {self.ais['grade']}"))

        current.card["ais"].append(VegaChart({
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "title": "Decision Quality Score breakdown",
            "layer": [
                {"mark": {"type": "bar", "color": "#D3D1C7"},
                 "encoding": {"x": {"field": "component", "type": "nominal", "axis": {"labelAngle": -25}},
                              "y": {"field": "max", "type": "quantitative", "axis": {"title": "Points"}}}},
                {"mark": {"type": "bar", "color": "#534AB7"},
                 "encoding": {"x": {"field": "component", "type": "nominal"},
                              "y": {"field": "score", "type": "quantitative"}}},
            ],
            "data": {"values": ais_data},
            "width": 380, "height": 180
        }))

        if self.ais.get("flags"):
            for flag in self.ais["flags"]:
                current.card["ais"].append(Markdown(f"- \u26a0 {flag}"))

        current.card["ais"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": "ais", "ms": ms, "llm": False,
                           "summary": f"{self.ais['score']}/100 {self.ais['grade']}"})
        print(f"[ais] {self.ais['score']}/100 ({ms}ms)")
        self.next(self.persist)

    # ── Persist ───────────────────────────────────────────────────────────────

    @step
    def persist(self):
        """Write immutable AuditRecord to DB (with basket columns)."""
        t0 = time.time()
        init_db()
        db = SessionLocal()
        try:
            rec_supplier = self.decision.get("recommended_supplier")
            is_basket = getattr(self, "is_basket", False)
            record = AuditRecord(
                id=self.record_id, created_at=self.created_at,
                agent_version=AGENT_VERSION,
                raw_request=self.raw_request or self.structured.get("item_description", ""),
                structured_request=json.dumps(self.structured),
                policy_results=json.dumps({
                    "violations": self.policy_results.get("violations", []),
                    "warnings": self.policy_results.get("warnings", []),
                    "escalations": self.policy_results.get("escalations", []),
                }),
                supplier_candidates=json.dumps(self.supplier_results),
                scored_suppliers=json.dumps(self.scoring_result),
                decision_type=self.decision["decision_type"],
                recommended_supplier_id=rec_supplier["id"] if rec_supplier else None,
                recommended_supplier_name=rec_supplier["name"] if rec_supplier else None,
                estimated_total_eur=rec_supplier["total_cost_eur"] if rec_supplier and "total_cost_eur" in rec_supplier else None,
                confidence=self.decision.get("confidence"),
                reasoning_narrative=getattr(self, "narrative_text", ""),
                escalation_reason=self.decision.get("escalation_reason"),
                rejection_reason=self.decision.get("rejection_reason"),
                ais_score=self.ais["score"], ais_grade=self.ais["grade"],
                ais_components=json.dumps(self.ais["components"]),
                eu_ai_act_compliant=self.ais["eu_ai_act_article_13_compliant"],
                state="completed", parent_record_id=self.parent_id,
                pipeline_trace=json.dumps(self.trace),
                fuzzy_trace=json.dumps(self.risk_result) if self.risk_result.get("approach") == "fuzzy" else None,
                risk_score=self.risk_score_val,
                # Basket columns
                is_basket=is_basket,
                basket_line_count=len(self.basket_lines) if is_basket else None,
                basket_line_decisions=json.dumps(self.basket_line_decisions) if is_basket else None,
                basket_total_cost=self.basket_total_cost if is_basket else None,
            )
            for log_data in self.llm_logs:
                db.add(LLMCallLog(
                    id=log_data["id"], record_id=log_data["record_id"],
                    call_type=log_data["call_type"], model=log_data["model"],
                    temperature=log_data.get("temperature", 0.0),
                    system_prompt=log_data["system_prompt"], user_message=log_data["user_message"],
                    raw_response=log_data["raw_response"],
                    extracted_result=log_data.get("extracted_result"),
                    input_tokens=log_data.get("input_tokens"),
                    output_tokens=log_data.get("output_tokens"),
                    latency_ms=log_data.get("latency_ms"),
                    timestamp=log_data["timestamp"], parse_method=log_data.get("parse_method", "llm"),
                ))
            db.add(record)
            db.commit()
            self.final_state = "completed"
            print(f"[persist] record_id={self.record_id[:8]}")
        finally:
            db.close()

        self.trace.append({"step": "persist", "ms": int((time.time() - t0) * 1000),
                           "llm": False, "summary": f"record_id={self.record_id[:8]}"})
        self.next(self.notify_client)

    # ── Notify client ────────────────────────────────────────────────────────

    @card(type="blank", id="notification", refresh_interval=3)
    @step
    def notify_client(self):
        """Prepare and display the final client notification."""
        t0 = time.time()

        dtype = self.decision["decision_type"].upper()
        rec_supplier = self.decision.get("recommended_supplier")

        current.card["notification"].append(Markdown("## Client Notification"))

        # Decision outcome
        if dtype == "APPROVED":
            badge = "APPROVED"
        elif dtype == "ESCALATED":
            badge = "ESCALATED \u2014 Action Required"
        else:
            badge = "REJECTED"

        rows = [
            [Markdown("**Decision**"), Markdown(f"**{badge}**")],
            [Markdown("**Confidence**"), Markdown(f"{self.decision.get('confidence', 0):.0%}")],
        ]

        # Basket info
        if getattr(self, "is_basket", False):
            rows.append([Markdown("**Basket items**"), Markdown(str(len(self.basket_lines)))])
            rows.append([Markdown("**Basket total**"), Markdown(f"\u20ac{self.basket_total_cost:,.0f}")])

        if rec_supplier:
            rows.append([Markdown("**Recommended supplier**"), Markdown(f"**{rec_supplier['name']}**")])
            rows.append([Markdown("**Estimated cost**"), Markdown(f"\u20ac{rec_supplier.get('total_cost_eur', 0):,.0f}")])

        if dtype == "ESCALATED":
            reason = self.decision.get("escalation_reason", "Further review required")
            rows.append([Markdown("**Required action**"), Markdown(reason)])

        if dtype == "REJECTED":
            reason = self.decision.get("rejection_reason", "Policy violation")
            rows.append([Markdown("**Rejection reason**"), Markdown(reason)])

        rows.append([Markdown("**AIS score**"), Markdown(f"**{self.ais['score']}/100** ({self.ais['grade']})")])
        rows.append([Markdown("**Delivery**"), Markdown("Frontend dashboard")])

        current.card["notification"].append(Table(rows, headers=["", ""]))

        # ── Teams: notify client of final decision ─────────────────
        rec_name = rec_supplier["name"] if rec_supplier else None
        rec_cost = rec_supplier.get("total_cost_eur") if rec_supplier else None
        ais_score = self.ais.get("score") if hasattr(self, "ais") else None
        is_basket = getattr(self, "is_basket", False)
        basket_count = len(self.basket_lines) if is_basket else 0
        teams_sent = notify_client_decision(
            record_id=self.record_id,
            decision_type=self.decision["decision_type"],
            supplier=rec_name,
            total_cost=rec_cost,
            ais_score=ais_score,
            is_basket=is_basket,
            basket_count=basket_count,
        )
        delivery = "Teams + Dashboard" if teams_sent else "Dashboard"

        current.card["notification"].append(Markdown(
            f"---\n*Notification delivered to requester via {delivery}*"
        ))
        current.card["notification"].refresh()

        ms = int((time.time() - t0) * 1000)
        self.trace.append({"step": "notify_client", "ms": ms, "llm": False,
                           "summary": f"notified: {dtype}, teams={teams_sent}"})
        print(f"[notify_client] {dtype} teams={teams_sent} ({ms}ms)")
        self.next(self.end)

    # ── End ───────────────────────────────────────────────────────────────────

    @card(type="blank", id="summary", refresh_interval=5)
    @step
    def end(self):
        """Audit summary + cross-run comparison."""
        from itertools import islice

        dtype = self.decision["decision_type"].upper()
        total_ms = sum(s["ms"] for s in self.trace)

        current.card["summary"].append(Markdown(f"## \u2713 Pipeline complete \u2014 {dtype}"))
        summary_rows = [
            [Markdown("Decision"), Markdown(f"**{dtype}**")],
            [Markdown("Confidence"), Markdown(f"**{self.decision.get('confidence', 0):.0%}**")],
            [Markdown("Supplier"), Markdown(self.decision["recommended_supplier"]["name"] if self.decision.get("recommended_supplier") else "N/A")],
            [Markdown("AIS"), Markdown(f"{self.ais['score']}/100 {self.ais['grade']}")],
            [Markdown("Risk"), Markdown(f"{self.risk_score_val}/100")],
            [Markdown("Total time"), Markdown(f"{total_ms}ms")],
        ]
        if getattr(self, "is_basket", False):
            summary_rows.insert(1, [Markdown("Basket items"), Markdown(str(len(self.basket_lines)))])
            summary_rows.insert(2, [Markdown("Basket total"), Markdown(f"\u20ac{self.basket_total_cost:,.0f}")])
        current.card["summary"].append(Table(summary_rows, headers=["", ""]))

        # ── Cross-run comparison ─────────────────────────────────────────
        try:
            compare_data = []
            for run in islice(Flow("Phase2Flow"), 8):
                try:
                    d = run["decide"].task.data
                    compare_data.append({
                        "run": str(run.id)[-6:],
                        "confidence": round(getattr(d, "confidence_result", {}).get("confidence", d.decision.get("confidence", 0)) * 100, 1),
                        "decision": d.decision["decision_type"],
                    })
                except Exception:
                    continue

            if len(compare_data) > 1:
                current.card["summary"].append(Markdown("### Confidence trend"))
                current.card["summary"].append(VegaChart({
                    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                    "data": {"values": compare_data},
                    "mark": {"type": "line", "point": True},
                    "encoding": {
                        "x": {"field": "run", "type": "nominal"},
                        "y": {"field": "confidence", "type": "quantitative", "scale": {"domain": [0, 100]}},
                        "color": {"field": "decision", "type": "nominal",
                                  "scale": {"range": ["#1D9E75", "#E24B4A", "#EF9F27"]}},
                    },
                    "width": 380, "height": 160
                }))
        except Exception:
            pass

        current.card["summary"].append(Markdown("---\n*Every Card is an immutable audit artifact.*"))
        current.card["summary"].refresh()

        print(f"\n{'=' * 60}")
        print(f"  Phase 2 Complete \u2014 {dtype} \u2014 AIS {self.ais['score']}/100")
        print(f"  Open http://localhost:3000 \u2192 click this run \u2192 click any step \u2192 Card tab")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    Phase2Flow()
