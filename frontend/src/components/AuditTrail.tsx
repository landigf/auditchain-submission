import { FileJson, FileText, ArrowLeft, Clock, Bot } from 'lucide-react'
import type { AuditRecord } from '../types'
import { decisionType } from '../types'
import { AISBadge } from './AISBadge'
import { DecisionBadge } from './DecisionBadge'
import { PipelineTrace } from './PipelineTrace'
import { LLMCallInspector } from './LLMCallInspector'
import { exportAuditJsonUrl, exportAuditPdfUrl } from '../api/client'

interface Props {
  record: AuditRecord
  onBack: () => void
}

export function AuditTrail({ record, onBack }: Props) {
  const { structured_request: req, policy_results, ais, decision } = record

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="card">
        <div className="flex items-start justify-between gap-4">
          <div>
            <button onClick={onBack} className="flex items-center gap-1 text-sm text-neutral-400 hover:text-white mb-3 transition-colors">
              <ArrowLeft className="w-4 h-4" /> Back to decision
            </button>
            <h2 className="text-lg font-bold text-white mb-1">Audit Trail</h2>
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <Clock className="w-3.5 h-3.5" />
              {new Date(record.created_at).toLocaleString()}
              <span>•</span>
              <Bot className="w-3.5 h-3.5" />
              Agent v{record.agent_version}
              <span>•</span>
              <span className="font-mono">{record.record_id.slice(0, 8)}</span>
            </div>
          </div>
          <AISBadge ais={ais} />
        </div>

        {/* Export buttons */}
        <div className="flex gap-3 mt-4">
          <a
            href={exportAuditPdfUrl(record.record_id)}
            download
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700
                       text-slate-300 text-sm border border-slate-700 transition-colors"
          >
            <FileText className="w-4 h-4 text-red-400" />
            Export PDF
          </a>
          <a
            href={exportAuditJsonUrl(record.record_id)}
            download
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700
                       text-slate-300 text-sm border border-slate-700 transition-colors"
          >
            <FileJson className="w-4 h-4 text-blue-400" />
            Export JSON
          </a>
        </div>
      </div>

      {/* Step 1: Request parsing */}
      <AuditStep step={1} title="Request Parsing" status="done">
        <div className="grid grid-cols-2 gap-3 text-sm">
          {[
            ['Item', req.item_description],
            ['Category', req.category],
            ['Quantity', req.quantity?.toString() ?? '—'],
            ['Deadline', req.deadline_days ? `${req.deadline_days} business days` : '—'],
            ['Budget', req.budget_eur ? `€${req.budget_eur.toLocaleString()}` : '—'],
            ['Preferred Supplier', req.preferred_supplier_name ?? 'None specified'],
            ['Department', req.requester_department ?? '—'],
          ].map(([label, val]) => (
            <div key={label}>
              <div className="text-slate-500 text-xs">{label}</div>
              <div className="text-slate-200">{val}</div>
            </div>
          ))}
        </div>
        {req.ambiguities.length > 0 && (
          <div className="mt-3 text-xs text-amber-400 bg-amber-950/30 rounded-lg p-3 space-y-1">
            <div className="font-semibold">Ambiguities detected:</div>
            {req.ambiguities.map((a, i) => <div key={i}>• {a}</div>)}
          </div>
        )}
      </AuditStep>

      {/* Step 2: Policy check */}
      <AuditStep step={2} title="Policy Rules Engine"
        status={policy_results.violations.length > 0 ? 'blocked'
               : policy_results.escalations.length > 0 ? 'escalated' : 'done'}>
        <div className="space-y-2">
          {(() => {
            const triggered = [...policy_results.violations, ...policy_results.escalations, ...policy_results.warnings]
            const TOTAL_RULES = 30
            return (
              <>
                <div className="text-xs text-slate-400 mb-2 px-2 py-1.5 bg-slate-800/30 rounded-lg">
                  {triggered.length === 0
                    ? `✓ All ${TOTAL_RULES} procurement policy rules checked — 0 violations, 0 warnings, 0 escalations`
                    : `${triggered.length} of ${TOTAL_RULES} rules triggered: ${policy_results.violations.length} violation(s), ${policy_results.escalations.length} escalation(s), ${policy_results.warnings.length} warning(s)`
                  }
                </div>
                {triggered.map((r) => (
                  <div key={r.rule_id} className="flex items-start gap-3 text-sm p-2 rounded-lg bg-slate-800/50">
                    <div className="font-mono text-xs text-slate-500 mt-0.5 w-8 shrink-0">{r.rule_id}</div>
                    <div className="flex-1">
                      <div className="text-slate-200 font-medium">{r.rule_name}</div>
                      <div className="text-slate-400 text-xs">{r.description}</div>
                      {r.detail && <div className="text-xs mt-1 text-slate-300 italic">{r.detail}</div>}
                    </div>
                    <StatusPill action={r.action} />
                  </div>
                ))}
              </>
            )
          })()}
        </div>
      </AuditStep>

      {/* Step 3: Supplier scoring */}
      <AuditStep step={3} title="Supplier Evaluation" status="done">
        <div className="text-xs text-slate-500 mb-3">
          {record.supplier_results.total_found} suppliers found •{' '}
          {record.supplier_results.total_eligible} eligible •{' '}
          {record.supplier_results.disqualified.length} disqualified
        </div>
        {record.scoring_result?.scored?.slice(0, 5).map((s) => (
          <div key={s.id} className="mb-3 p-3 bg-slate-800/50 rounded-lg">
            <div className="flex items-center justify-between mb-2">
              <div className="font-medium text-slate-200">#{s.rank} {s.name}</div>
              <div className="text-lg font-bold text-slate-100">{s.score}</div>
            </div>
            <div className="grid grid-cols-4 gap-2 text-xs">
              {Object.entries(s.score_breakdown)
                .filter(([k, v]) => k !== 'weights_used' && k !== 'historical_note' && typeof v === 'number')
                .map(([key, val]) => {
                  const numVal = val as number
                  const isBonus = key === 'historical_bonus'
                  if (isBonus && numVal === 0) return null
                  const maxVal = isBonus ? 10 : 100
                  return (
                  <div key={key} title={isBonus ? (s.score_breakdown as Record<string, unknown>).historical_note as string ?? '' : ''}>
                    <div className="text-slate-500 mb-1">{key.replace('_score', '').replace('_normalized', '').replace(/_/g, ' ')}</div>
                    <div className="h-1 bg-slate-700 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full ${isBonus ? 'bg-blue-500' : 'bg-brand-500'}`} style={{ width: `${(numVal / maxVal) * 100}%` }} />
                    </div>
                    <div className="text-slate-400 mt-1">{isBonus ? `+${numVal}` : numVal}</div>
                  </div>
                )})}
            </div>
          </div>
        ))}
      </AuditStep>

      {/* Step 4: Decision */}
      <AuditStep step={4} title="Decision" status={decisionType(decision) === 'approved' ? 'done' : decisionType(decision) === 'escalated' ? 'escalated' : 'blocked'}>
        <div className="flex items-center gap-3 mb-3">
          <DecisionBadge type={decisionType(decision)} size="lg" />
          {decision.confidence != null && (
            <span className="text-slate-400 text-sm">Confidence: {Math.round(decision.confidence * 100)}%</span>
          )}
        </div>
        {decision.reasoning_narrative && (
          <div className="text-sm text-slate-300 bg-slate-800/50 rounded-lg p-4 leading-relaxed italic">
            "{decision.reasoning_narrative}"
          </div>
        )}
      </AuditStep>

      {/* Step 5: AIS */}
      <AuditStep step={5} title="Audit Intelligence Score" status="done">
        <AISBadge ais={ais} large />
      </AuditStep>

      {/* Step 6: Pipeline Execution Trace */}
      {record.pipeline_trace && record.pipeline_trace.length > 0 && (
        <AuditStep step={6} title="Pipeline Execution Trace" status="done">
          <PipelineTrace steps={record.pipeline_trace} />
        </AuditStep>
      )}

      {/* Step 7: LLM Call Audit Log */}
      <AuditStep step={7} title="LLM Call Audit Log (EU AI Act Art.13)" status="done">
        <LLMCallInspector recordId={record.record_id} />
      </AuditStep>
    </div>
  )
}

function AuditStep({ step, title, status, children }: {
  step: number; title: string; status: 'done' | 'blocked' | 'escalated'
  children: React.ReactNode
}) {
  const statusColor = status === 'done' ? 'border-green-800/50' : status === 'blocked' ? 'border-red-800/50' : 'border-amber-800/50'
  return (
    <div className={`card border ${statusColor}`}>
      <div className="flex items-center gap-3 mb-4">
        <div className="w-7 h-7 rounded-full bg-neutral-800 flex items-center justify-center text-xs font-bold text-neutral-400">
          {step}
        </div>
        <h3 className="font-semibold text-white">{title}</h3>
      </div>
      {children}
    </div>
  )
}

function StatusPill({ action }: { action: string }) {
  const config: Record<string, string> = {
    block: 'bg-red-950 text-red-400 border-red-800',
    escalate: 'bg-amber-950 text-amber-400 border-amber-800',
    warn: 'bg-yellow-950 text-yellow-400 border-yellow-800',
    pass: 'bg-green-950 text-green-400 border-green-800',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded border font-medium ${config[action] ?? config.pass}`}>
      {action.toUpperCase()}
    </span>
  )
}
