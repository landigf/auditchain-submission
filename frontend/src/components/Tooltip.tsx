import { useState, useRef, useEffect } from 'react'
import { Info } from 'lucide-react'

interface Props {
  content: string | React.ReactNode
  children?: React.ReactNode
  icon?: boolean  // show (i) icon instead of wrapping children
}

export function Tooltip({ content, children, icon }: Props) {
  const [show, setShow] = useState(false)
  const [position, setPosition] = useState<'above' | 'below'>('above')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (show && ref.current) {
      const rect = ref.current.getBoundingClientRect()
      setPosition(rect.top < 200 ? 'below' : 'above')
    }
  }, [show])

  return (
    <span
      className="relative inline-flex items-center"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {icon ? (
        <Info className="w-3.5 h-3.5 text-slate-500 hover:text-slate-300 cursor-help transition-colors" />
      ) : (
        children
      )}
      {show && (
        <div
          ref={ref}
          className={`absolute z-50 w-72 px-3 py-2.5 rounded-lg bg-slate-800 border border-slate-600/50
                      text-xs text-slate-300 leading-relaxed shadow-xl shadow-black/40
                      ${position === 'above' ? 'bottom-full mb-2' : 'top-full mt-2'} left-1/2 -translate-x-1/2`}
        >
          {content}
        </div>
      )}
    </span>
  )
}

/** Pre-built explanation tooltips for common decision elements */
export const EXPLANATIONS = {
  decision_approved: (
    <span>
      <strong className="text-green-400">APPROVED</strong> — All policy rules passed. The recommended supplier
      was selected based on the highest weighted composite score across price, delivery, compliance, and ESG factors.
      <br /><br />
      <span className="text-slate-500">This decision is fully stored and can be replayed from the audit record at any time.</span>
    </span>
  ),
  decision_escalated: (
    <span>
      <strong className="text-amber-400">ESCALATED</strong> — One or more policy rules require human approval.
      The agent identified the correct approver based on the escalation hierarchy.
      No supplier was auto-selected — a human must review and confirm.
      <br /><br />
      <span className="text-slate-500">Escalation trigger rules are stored in the policy_results field.</span>
    </span>
  ),
  decision_rejected: (
    <span>
      <strong className="text-red-400">REJECTED</strong> — A hard policy violation was detected (e.g., restricted supplier,
      compliance failure). The agent cannot approve this request under current rules.
      <br /><br />
      <span className="text-slate-500">Rejection reasons are immutable — stored in the audit record.</span>
    </span>
  ),
  confidence: (
    <span>
      <strong>Confidence</strong> measures how clearly the top supplier outperforms alternatives.
      High confidence (&gt;90%) means a large score gap between #1 and #2.
      Low confidence means suppliers are closely ranked — a human reviewer may want to compare.
      <br /><br />
      <span className="text-slate-500">Formula: 0.6 + (score_gap / 100) × 2, capped at 99%.</span>
    </span>
  ),
  ais_score: (
    <span>
      <strong>Audit Intelligence Score (AIS)</strong> — measures how defensible this decision would be
      in a compliance audit under EU AI Act Article 13.
      <br /><br />
      5 components: completeness (25), rule coverage (20), decision traceability (25),
      contestability (20), escalation appropriateness (10).
      <br /><br />
      <span className="text-green-400">90+</span> = Audit-Ready &nbsp;
      <span className="text-amber-400">70-89</span> = Review Recommended &nbsp;
      <span className="text-red-400">&lt;70</span> = Manual Review Required
    </span>
  ),
  risk_score: (
    <span>
      <strong>Risk Score</strong> — continuous 0-100 score computed from budget ratio, spending authority ratio,
      delivery urgency, and vendor tier. Additive to hard rules (never overrides decisions).
      <br /><br />
      <span className="text-green-400">&lt;40</span> = Low risk &nbsp;
      <span className="text-amber-400">40-70</span> = Medium risk &nbsp;
      <span className="text-red-400">&gt;70</span> = High risk
      <br /><br />
      <span className="text-slate-500">Two approaches: linear (default) or fuzzy logic (catches near-miss cases).</span>
    </span>
  ),
  supplier_score: (
    <span>
      <strong>Composite Score</strong> — weighted sum of: price score (category-specific weight),
      delivery speed, compliance rating, ESG score, plus a historical performance bonus (0-10).
      <br /><br />
      Weights vary by category: hardware weights price 40%, services weights compliance 30%.
      <br /><br />
      <span className="text-slate-500">Full score breakdown is stored per supplier in the scored_suppliers field.</span>
    </span>
  ),
  policy_rules: (
    <span>
      <strong>Policy Engine</strong> — 30+ deterministic rules loaded from the database.
      Rules cover: approval thresholds (5 tiers), restricted suppliers, ESG minimums,
      GDPR data residency, geographic coverage, and spending authority limits.
      <br /><br />
      <span className="text-red-400">Block</span> = hard violation, request rejected.&nbsp;
      <span className="text-amber-400">Escalate</span> = human approval needed.&nbsp;
      <span className="text-yellow-400">Warn</span> = flagged but not blocking.
    </span>
  ),
  disqualified: (
    <span>
      <strong>Disqualified suppliers</strong> were found in the category but failed one or more
      compliance checks: ESG below 60, geographic mismatch, restricted status, or GDPR non-compliance.
      <br /><br />
      <span className="text-slate-500">Each disqualification reason is tagged with the specific rule ID
      (e.g., R06 for ESG, R07 for GDPR) and stored permanently in the audit record.</span>
    </span>
  ),
}
