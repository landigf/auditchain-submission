import { Clock, Bot, Cpu } from 'lucide-react'
import type { PipelineStep } from '../types'

interface Props {
  steps: PipelineStep[]
}

const STEP_LABELS: Record<string, string> = {
  phase1_validation: 'Phase 1 Validation',
  parse: 'LLM Parse',
  validate: 'Field Validation',
  policy_check: 'Policy Engine',
  filter_suppliers: 'Supplier Filter',
  score: 'Supplier Scoring',
  decide: 'Decision Logic',
  narrative: 'LLM Narrative',
  risk_score: 'Risk Score',
  ais: 'AIS Computation',
  persist: 'Persist Record',
}

export function PipelineTrace({ steps }: Props) {
  const totalMs = steps.reduce((sum, s) => sum + s.ms, 0)
  const llmSteps = steps.filter(s => s.llm).length

  return (
    <div className="space-y-3">
      {/* Summary bar */}
      <div className="flex items-center gap-4 text-xs text-slate-400">
        <div className="flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5" />
          <span>{totalMs.toLocaleString()}ms total</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Bot className="w-3.5 h-3.5" />
          <span>{llmSteps} LLM call{llmSteps !== 1 ? 's' : ''}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Cpu className="w-3.5 h-3.5" />
          <span>{steps.length - llmSteps} deterministic</span>
        </div>
      </div>

      {/* Step table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-slate-500 border-b border-slate-700/50">
              <th className="text-left py-2 pr-4 font-medium">#</th>
              <th className="text-left py-2 pr-4 font-medium">Step</th>
              <th className="text-right py-2 pr-4 font-medium">Duration</th>
              <th className="text-center py-2 pr-4 font-medium">LLM</th>
              <th className="text-left py-2 font-medium">Output</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((step, i) => (
              <tr key={i} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                <td className="py-2 pr-4 text-slate-500 font-mono text-xs">{i + 1}</td>
                <td className="py-2 pr-4">
                  <span className="text-slate-200 font-medium">
                    {STEP_LABELS[step.step] ?? step.step}
                  </span>
                </td>
                <td className="py-2 pr-4 text-right">
                  <DurationBadge ms={step.ms} />
                </td>
                <td className="py-2 pr-4 text-center">
                  {step.llm ? (
                    <span className="inline-flex items-center gap-1 text-xs text-purple-400 bg-purple-950/40 border border-purple-800/40 rounded px-1.5 py-0.5">
                      <Bot className="w-3 h-3" /> LLM
                    </span>
                  ) : (
                    <span className="text-xs text-slate-600">—</span>
                  )}
                </td>
                <td className="py-2 text-slate-400 text-xs max-w-xs truncate">
                  {step.summary}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-slate-600/50">
              <td colSpan={2} className="py-2 pr-4 text-slate-300 font-semibold text-xs">
                Total ({steps.length} steps)
              </td>
              <td className="py-2 pr-4 text-right">
                <span className="text-white font-bold text-sm">{totalMs.toLocaleString()}ms</span>
              </td>
              <td colSpan={2} />
            </tr>
          </tfoot>
        </table>
      </div>

      {/* Visual timeline bar */}
      <div className="flex h-2 rounded-full overflow-hidden bg-slate-800">
        {steps.map((step, i) => {
          const pct = totalMs > 0 ? (step.ms / totalMs) * 100 : 0
          if (pct < 0.5) return null // skip tiny steps
          return (
            <div
              key={i}
              className={`h-full ${step.llm ? 'bg-purple-500' : 'bg-brand-500'}`}
              style={{ width: `${pct}%` }}
              title={`${STEP_LABELS[step.step] ?? step.step}: ${step.ms}ms`}
            />
          )
        })}
      </div>
      <div className="flex gap-4 text-xs text-slate-500">
        <div className="flex items-center gap-1.5">
          <div className="w-2.5 h-2.5 rounded-sm bg-purple-500" />
          LLM calls
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2.5 h-2.5 rounded-sm bg-brand-500" />
          Deterministic
        </div>
      </div>
    </div>
  )
}

function DurationBadge({ ms }: { ms: number }) {
  const color = ms > 1000 ? 'text-amber-400' : ms > 100 ? 'text-slate-300' : 'text-green-400'
  const display = ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
  return <span className={`font-mono text-xs ${color}`}>{display}</span>
}
