import { useState } from 'react'
import { Trophy, ShieldCheck, DatabaseZap, X, ChevronDown, FileText, FileJson, ArrowLeft, Sparkles, ArrowRightLeft, CheckCircle2, ShieldAlert, Clock } from 'lucide-react'
import type { AuditRecord, ScoredSupplier, RiskScore, AIS } from '../types'
import { decisionType } from '../types'
import { DecisionBadge } from './DecisionBadge'
import { WeightExplorer } from './WeightExplorer'
import { Tooltip, EXPLANATIONS } from './Tooltip'
import { exportAuditPdfUrl, exportAuditJsonUrl } from '../api/client'
import clsx from 'clsx'

interface Props {
  record: AuditRecord
  onViewAudit: () => void
  onBack?: () => void
  onConfirmOrder?: (supplier: ScoredSupplier) => void
}

export function DecisionView({ record, onViewAudit, onBack, onConfirmOrder }: Props) {
  const decision = record.decision ?? {} as any
  const ais = record.ais ?? { score: 0, grade: 'Incomplete' as const, components: {}, eu_ai_act_article_13_compliant: false }
  const policy_results = record.policy_results ?? { violations: [], warnings: [], escalations: [], all_clear: true }
  const scoring_result = record.scoring_result
  const dtype = decisionType(decision)
  const scored = scoring_result?.scored ?? []
  const [aisOpen, setAisOpen] = useState(false)
  const [escalationOpen, setEscalationOpen] = useState(false)
  const [selectedSupplier, setSelectedSupplier] = useState<string | null>(
    scored.length > 0 ? scored[0].id : null
  )
  const canOrder = dtype === 'approved' && !!onConfirmOrder

  return (
    <div className="space-y-4">
      {/* Header */}
      {onBack && (
        <button onClick={onBack} className="flex items-center gap-2 pl-0 pr-3 py-1.5 rounded-lg text-neutral-400
                       hover:text-white transition-colors text-sm font-medium mb-4">
          <ArrowLeft className="w-5 h-5" /> Back
        </button>
      )}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h2 className="text-3xl font-semibold text-white mb-5">Sourcing Request</h2>
          <div className="flex items-center gap-3 mb-4">
            <button
              onClick={() => setAisOpen(true)}
              className={clsx(
                'px-4 py-2 rounded-lg border font-bold text-base hover:opacity-80 transition-opacity',
                ais.score >= 85 ? 'border-green-700 bg-green-950/40 text-green-400' :
                ais.score >= 65 ? 'border-amber-700 bg-amber-950/40 text-amber-400' :
                                  'border-red-700 bg-red-950/40 text-red-400'
              )}
              title="Decision Quality Score — how well the system handled this request"
            >
              {ais.score}
            </button>
            <button onClick={() => setEscalationOpen(true)} className="hover:opacity-80 transition-opacity">
              <DecisionBadge type={dtype} size="lg" />
            </button>
          </div>
          {record.raw_request && (
            <p className="text-neutral-500 text-sm leading-relaxed max-w-lg">{record.raw_request}</p>
          )}
        </div>
        <div className="flex items-center gap-2 mt-1">
          <a
            href={exportAuditPdfUrl(record.record_id)}
            download
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-neutral-700 text-neutral-400
                       hover:bg-neutral-800 hover:text-white transition-colors text-sm font-medium"
          >
            <FileText className="w-3.5 h-3.5" />
            PDF
          </a>
          <a
            href={exportAuditJsonUrl(record.record_id)}
            download
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-neutral-700 text-neutral-400
                       hover:bg-neutral-800 hover:text-white transition-colors text-sm font-medium"
          >
            <FileJson className="w-3.5 h-3.5" />
            JSON
          </a>
          <button
            onClick={onViewAudit}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-neutral-700 text-neutral-400
                       hover:bg-neutral-800 hover:text-white transition-colors text-sm font-medium whitespace-nowrap"
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            View trail
          </button>
        </div>
      </div>

      {/* Escalation banner — order cannot be completed */}
      {(dtype === 'escalated' || dtype === 'rejected') && (
        <div className={clsx(
          'card border space-y-3',
          dtype === 'rejected' ? 'border-red-800/40' : 'border-amber-800/40'
        )}>
          <div className="flex items-center gap-3">
            <div className={clsx(
              'w-10 h-10 rounded-full flex items-center justify-center shrink-0',
              dtype === 'rejected' ? 'bg-red-950/50 border border-red-800/50' : 'bg-amber-950/50 border border-amber-800/50'
            )}>
              <ShieldAlert className={clsx('w-5 h-5', dtype === 'rejected' ? 'text-red-400' : 'text-amber-400')} />
            </div>
            <div>
              <h3 className="text-white font-semibold">
                {dtype === 'rejected' ? 'Order Rejected' : `Escalated to ${decision.escalated_to || 'Procurement Manager'}`}
              </h3>
              <p className="text-sm text-neutral-400">
                {dtype === 'rejected'
                  ? 'This order has been rejected and cannot be completed.'
                  : `This order requires approval from ${decision.escalated_to || 'a Procurement Manager'} before it can be completed.`}
              </p>
            </div>
          </div>
          {decision.escalation_reason && dtype === 'escalated' && (
            <div className="bg-amber-950/30 border border-amber-800/30 rounded-lg p-3 text-sm text-amber-200">
              {decision.escalation_reason}
            </div>
          )}
          {decision.rejection_reason && dtype === 'rejected' && (
            <div className="bg-red-950/30 border border-red-800/30 rounded-lg p-3 text-sm text-red-200">
              {decision.rejection_reason}
            </div>
          )}
          {dtype === 'escalated' && (
            <div className="flex items-center gap-1.5 text-xs text-neutral-500">
              <Clock className="w-3 h-3" />
              Pending review — you will be notified once a decision is made
            </div>
          )}
        </div>
      )}

      {/* Supplier leaderboard — right after header */}
      {scored.length > 0 && (
        <div className="card">
          <h3 className="text-xs font-semibold text-neutral-500 mb-4 uppercase tracking-wider flex items-center gap-2">
            {canOrder ? 'Select Supplier' : 'Supplier Evaluation'} <Tooltip content={EXPLANATIONS.supplier_score} icon />
          </h3>
          <SupplierList
            suppliers={scored}
            selectable={canOrder}
            selectedId={selectedSupplier}
            onSelect={setSelectedSupplier}
          />
          {canOrder && selectedSupplier && (
            <button
              onClick={() => {
                const s = scored.find(x => x.id === selectedSupplier)
                if (s) onConfirmOrder!(s)
              }}
              className="w-full mt-4 py-3 rounded-xl bg-green-600 hover:bg-green-500 text-white font-semibold
                         transition-colors flex items-center justify-center gap-2"
            >
              <CheckCircle2 className="w-5 h-5" />
              Confirm Order
            </button>
          )}
        </div>
      )}

      {/* Counterfactual Explanations */}
      <CounterfactualSection record={record} />

      {/* What-If Weight Explorer */}
      {scored.length >= 2 && (
        <WeightExplorer
          scored={scored}
          category={record.structured_request.category ?? 'default'}
        />
      )}

      {/* Policy checks */}
      <div className="card">
        <h3 className="text-xs font-semibold text-neutral-500 mb-3 uppercase tracking-wider flex items-center gap-2">
          Policy Rules <Tooltip content={EXPLANATIONS.policy_rules} icon />
        </h3>
        <PolicyChecklist policy={policy_results} />
      </div>

      {/* Disqualified suppliers */}
      {record.supplier_results?.disqualified?.length > 0 && (
        <div className="card">
          <h3 className="text-xs font-semibold text-red-500 mb-3 uppercase tracking-wider flex items-center gap-2">
            Disqualified Suppliers <Tooltip content={EXPLANATIONS.disqualified} icon />
          </h3>
          <div className="space-y-2">
            {record.supplier_results.disqualified.map((s) => (
              <div key={s.id} className="flex items-start gap-3 text-sm">
                <span className="text-red-500 mt-0.5">✕</span>
                <div>
                  <span className="text-neutral-300 font-medium">{s.name}</span>
                  <div className="text-neutral-500 text-xs mt-0.5">
                    {s.disqualification_reasons?.join(' · ')}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* AIS Dialog */}
      {aisOpen && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in"
          onClick={() => setAisOpen(false)}>
          <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 max-w-sm w-full animate-fade-scale-in"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-semibold">Decision Quality Score</h3>
              <button onClick={() => setAisOpen(false)} className="text-neutral-500 hover:text-white transition-colors">
                <X className="w-4 h-4" />
              </button>
            </div>
            <AISDetail ais={ais} />
          </div>
        </div>
      )}

      {/* Decision Dialog */}
      {escalationOpen && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in"
          onClick={() => setEscalationOpen(false)}>
          <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 max-w-md w-full animate-fade-scale-in"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-semibold">
                {dtype === 'escalated' ? `Escalated to ${decision.escalated_to || 'Procurement Manager'}`
                  : dtype === 'rejected' ? 'Order Rejected'
                  : 'Decision Details'}
              </h3>
              <button onClick={() => setEscalationOpen(false)} className="text-neutral-500 hover:text-white transition-colors">
                <X className="w-4 h-4" />
              </button>
            </div>

            {dtype === 'escalated' && (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-amber-400 text-sm font-semibold">
                  <ShieldAlert className="w-4 h-4" />
                  This order cannot be completed automatically
                </div>
                {decision.escalation_reason && (
                  <div className="bg-amber-950/30 border border-amber-800/30 rounded-lg p-3">
                    <div className="text-xs text-amber-400 font-semibold uppercase tracking-wider mb-1">Reason</div>
                    <p className="text-sm text-amber-200 leading-relaxed">{decision.escalation_reason}</p>
                  </div>
                )}
                <div className="bg-neutral-800/50 rounded-lg p-3">
                  <div className="text-xs text-neutral-500 font-semibold uppercase tracking-wider mb-1">What happens next</div>
                  <p className="text-sm text-neutral-300 leading-relaxed">
                    The <span className="text-white font-medium">{decision.escalated_to || 'Procurement Manager'}</span> will review the request, verify feasibility, and decide whether to approve, modify, or reject it.
                  </p>
                </div>
              </div>
            )}

            {dtype === 'rejected' && (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-red-400 text-sm font-semibold">
                  <X className="w-4 h-4" />
                  This order has been rejected
                </div>
                {decision.rejection_reason && (
                  <p className="text-sm text-red-200 bg-red-950/30 border border-red-800/30 rounded-lg p-3 leading-relaxed">
                    {decision.rejection_reason}
                  </p>
                )}
              </div>
            )}

            {dtype === 'approved' && (
              <div className="flex items-center gap-2">
                <DatabaseZap className="w-4 h-4 text-green-500 shrink-0" />
                <span className="text-green-400 text-sm font-semibold">Auto-approved — ready to confirm</span>
                <span className="text-neutral-500 text-xs font-mono ml-1">{record.record_id.slice(0, 8)}…</span>
              </div>
            )}

            {dtype === 'clarification_needed' && (
              <p className="text-neutral-300 text-sm">Awaiting additional information from the requester.</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── AIS Detail ────────────────────────────────────────────────────────────────

function AISDetail({ ais }: { ais: AIS }) {
  const color = ais.score >= 85 ? 'text-green-400' : ais.score >= 65 ? 'text-amber-400' : 'text-red-400'
  const bgColor = ais.score >= 85 ? 'bg-green-400' : ais.score >= 65 ? 'bg-amber-400' : 'bg-red-400'

  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-3">
        <span className={clsx('text-5xl font-black', color)}>{ais.score}</span>
        <span className={clsx('text-sm font-semibold', color)}>{ais.grade}</span>
      </div>
      {ais.eu_ai_act_article_13_compliant && (
        <div className="text-xs bg-green-900/50 text-green-300 border border-green-700 px-3 py-1 rounded-full inline-block">
          EU AI Act Art.13 Compliant
        </div>
      )}
      <div className="space-y-2">
        {Object.entries(ais.components).map(([key, val]) => {
          const max = key === 'traceability' ? 25
                    : key === 'policy_coverage' ? 15
                    : 20
          return (
            <div key={key} className="flex items-center gap-3">
              <div className="text-xs text-neutral-400 w-40 truncate capitalize">{key.replace(/_/g, ' ')}</div>
              <div className="flex-1 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                <div className={clsx('h-full rounded-full', bgColor)} style={{ width: `${(val / max) * 100}%` }} />
              </div>
              <div className="text-xs text-neutral-400 w-8 text-right">{val}/{max}</div>
            </div>
          )
        })}
      </div>
      {ais.flags && ais.flags.length > 0 && (
        <div className="space-y-1">
          {ais.flags.map((flag, i) => (
            <div key={i} className="text-xs text-amber-400 bg-amber-950/50 border border-amber-800/50 rounded px-2 py-1">{flag}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Policy Checklist ─────────────────────────────────────────────────────────

function PolicyChecklist({ policy }: { policy: AuditRecord['policy_results'] }) {
  const all = [
    ...policy.violations.map(r => ({ ...r, severity: 'violation' as const })),
    ...policy.escalations.map(r => ({ ...r, severity: 'escalation' as const })),
    ...policy.warnings.map(r => ({ ...r, severity: 'warning' as const })),
  ]

  if (all.length === 0) {
    return <div className="text-sm text-green-400">All policy rules passed</div>
  }

  return (
    <div className="space-y-2">
      {all.map((rule) => (
        <div key={rule.rule_id} className="flex items-start gap-3 text-sm">
          <span className={clsx('mt-0.5',
            rule.severity === 'violation' ? 'text-red-400' :
            rule.severity === 'escalation' ? 'text-amber-400' : 'text-yellow-400'
          )}>
            {rule.severity === 'violation' ? '✕' : rule.severity === 'escalation' ? '⬆' : '—'}
          </span>
          <div>
            <span className="text-neutral-200 font-medium">{rule.rule_name}</span>
            <span className="ml-2 text-xs text-neutral-500">{rule.rule_id}</span>
            {rule.detail && <div className="text-neutral-400 text-xs mt-0.5">{rule.detail}</div>}
            {rule.escalate_to && <div className="text-amber-400 text-xs mt-0.5">→ Escalate to: {rule.escalate_to}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Supplier List (expandable rows) ──────────────────────────────────────────

function SupplierList({ suppliers, selectable, selectedId, onSelect }: {
  suppliers: ScoredSupplier[]
  selectable?: boolean
  selectedId?: string | null
  onSelect?: (id: string) => void
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  return (
    <div className="divide-y divide-neutral-800">
      {suppliers.map((s) => {
        const isOpen = expanded.has(s.id)
        const isSelected = selectable && selectedId === s.id
        return (
          <div key={s.id} className={clsx(
            selectable && isSelected && 'bg-green-950/20 rounded-lg border border-green-800/30 -mx-2 px-2',
            selectable && !isSelected && '-mx-2 px-2 border border-transparent'
          )}>
            <button
              className="w-full flex items-center gap-4 py-4 text-left hover:bg-neutral-800/30 transition-colors rounded-lg px-2 -mx-2"
              onClick={() => {
                if (selectable && onSelect) onSelect(s.id)
                else toggle(s.id)
              }}
            >
              {/* Selection indicator or Rank */}
              <div className="w-6 shrink-0 flex justify-center">
                {selectable ? (
                  <div className={clsx(
                    'w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors',
                    isSelected ? 'border-green-500 bg-green-500' : 'border-neutral-600'
                  )}>
                    {isSelected && <CheckCircle2 className="w-3.5 h-3.5 text-white" />}
                  </div>
                ) : (
                  s.rank === 1
                    ? <Trophy className="w-4 h-4 text-yellow-400" />
                    : <span className="text-neutral-500 text-sm">{s.rank}</span>
                )}
              </div>

              {/* Name */}
              <div className="flex-1 min-w-0">
                <div className="font-medium text-neutral-200">{s.name}</div>
                <div className="text-xs text-neutral-500 mt-0.5">
                  {'\u20AC'}{s.total_cost_eur?.toLocaleString() ?? s.unit_price_eur.toLocaleString()} total · {s.delivery_days}d · {s.country}
                </div>
              </div>

              {/* Score bar */}
              <div className="flex items-center gap-2 shrink-0">
                <div className="w-20 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                  <div className={clsx('h-full rounded-full',
                    s.score >= 70 ? 'bg-green-500' : s.score >= 50 ? 'bg-amber-500' : 'bg-red-500'
                  )} style={{ width: `${s.score}%` }} />
                </div>
                <span className="text-neutral-200 font-semibold text-sm w-8 text-right">{s.score}</span>
              </div>

              {/* Price */}
              <div className="text-neutral-300 text-sm shrink-0 w-24 text-right">
                {'\u20AC'}{s.unit_price_eur.toLocaleString()}
              </div>

              {/* Expand arrow */}
              <ChevronDown className={clsx(
                'w-4 h-4 text-neutral-500 shrink-0 transition-transform duration-200',
                isOpen && 'rotate-180'
              )} />
            </button>

            {/* Expanded details */}
            {isOpen && (
              <div className="flex gap-8 px-2 pb-4 pt-1 text-sm text-neutral-400 animate-slide-down">
                <div><span className="text-neutral-600 text-xs uppercase tracking-wide block mb-0.5">Country</span>{s.country}</div>
                <div><span className="text-neutral-600 text-xs uppercase tracking-wide block mb-0.5">Delivery</span>{s.delivery_days}d</div>
                <div><span className="text-neutral-600 text-xs uppercase tracking-wide block mb-0.5">ESG</span>
                  <span className={s.esg_score >= 80 ? 'text-green-400' : s.esg_score >= 60 ? 'text-amber-400' : 'text-red-400'}>
                    {s.esg_score}
                  </span>
                </div>
                <div><span className="text-neutral-600 text-xs uppercase tracking-wide block mb-0.5">Tier</span>{s.preferred_tier}</div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Counterfactual Section ────────────────────────────────────────────────────

function CounterfactualSection({ record }: { record: AuditRecord }) {
  const counterfactuals = record.scoring_result?.counterfactuals ?? record.fuzzy_trace?.counterfactuals ?? []
  if (counterfactuals.length === 0) return null

  return (
    <div className="card border border-blue-900/30">
      <h3 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider flex items-center gap-2">
        <Sparkles className="w-4 h-4 text-blue-400" />
        Counterfactual Explanations
        <Tooltip content="Shows what would need to change for a different supplier to win. Demonstrates contestability — a key EU AI Act requirement." icon />
      </h3>
      <div className="space-y-2">
        {counterfactuals.map((cf, i) => (
          <div key={i} className="text-sm p-3 bg-slate-800/50 rounded-lg border border-slate-700/50">
            <div className="font-medium text-slate-200 mb-1">
              {cf.supplier_name ?? cf.supplier_id}
              {cf.current_rank != null && <span className="text-slate-500 ml-2">#{cf.current_rank}</span>}
              {cf.gap_to_winner != null && typeof cf.gap_to_winner === 'number' && (
                <span className="text-slate-500 ml-2">({cf.gap_to_winner.toFixed(1)} pts behind)</span>
              )}
            </div>
            {cf.scenario && (
              <div className="text-slate-400 text-xs">{cf.scenario}</div>
            )}
            {cf.what_if && Array.isArray(cf.what_if) && cf.what_if.length > 0 && (
              <div className="mt-1 space-y-0.5">
                {cf.what_if.map((text, j) => (
                  <div key={j} className="text-xs text-blue-400">• {text}</div>
                ))}
              </div>
            )}
            {cf.hypothetical_rank != null && (
              <div className="text-xs text-blue-400 mt-1">
                Would move to rank #{cf.hypothetical_rank}
              </div>
            )}
            {cf.impact && (
              <div className={`text-xs mt-1 ${cf.impact === 'positive' ? 'text-green-400' : 'text-amber-400'}`}>
                Impact: {cf.impact}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function ScoreBar({ score }: { score: number }) {
  const color = score >= 70 ? 'bg-green-500' : score >= 50 ? 'bg-amber-500' : 'bg-red-500'
  return (
    <div className="flex items-center justify-end gap-2">
      <div className="w-16 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
        <div className={clsx('h-full rounded-full', color)} style={{ width: `${score}%` }} />
      </div>
      <span className="text-neutral-200 font-semibold w-10 text-right">{score}</span>
    </div>
  )
}
