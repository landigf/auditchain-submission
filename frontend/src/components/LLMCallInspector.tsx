import { useState, useEffect } from 'react'
import { Bot, Clock, ChevronDown, ChevronRight, Hash, Zap } from 'lucide-react'
import { getLLMCalls, type LLMCallLog } from '../api/client'

interface Props {
  recordId: string
}

export function LLMCallInspector({ recordId }: Props) {
  const [calls, setCalls] = useState<LLMCallLog[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getLLMCalls(recordId)
      .then(setCalls)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [recordId])

  if (loading) return <div className="text-sm text-slate-500">Loading LLM calls...</div>
  if (error) return <div className="text-sm text-red-400">Error: {error}</div>
  if (calls.length === 0) return <div className="text-sm text-slate-500">No LLM calls recorded</div>

  return (
    <div className="space-y-3">
      <div className="text-xs text-slate-400">
        {calls.length} LLM call{calls.length !== 1 ? 's' : ''} logged for EU AI Act Art.13 compliance
      </div>
      {calls.map((call) => (
        <LLMCallCard key={call.id} call={call} />
      ))}
    </div>
  )
}

function LLMCallCard({ call }: { call: LLMCallLog }) {
  const [expandSystem, setExpandSystem] = useState(false)
  const [expandUser, setExpandUser] = useState(false)
  const [expandResult, setExpandResult] = useState(false)

  const totalTokens = (call.input_tokens ?? 0) + (call.output_tokens ?? 0)

  return (
    <div className="bg-slate-800/50 rounded-lg border border-slate-700/50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          <Bot className="w-4 h-4 text-purple-400" />
          <span className="text-sm font-semibold text-slate-200 uppercase">{call.call_type}</span>
          <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300 font-mono">
            {call.model}
          </span>
          <span className="text-xs text-slate-500">temp={call.temperature}</span>
        </div>
        <div className="flex items-center gap-4 text-xs text-slate-400">
          {call.latency_ms != null && (
            <div className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {call.latency_ms >= 1000 ? `${(call.latency_ms / 1000).toFixed(1)}s` : `${call.latency_ms}ms`}
            </div>
          )}
          {totalTokens > 0 && (
            <div className="flex items-center gap-1">
              <Hash className="w-3 h-3" />
              {call.input_tokens?.toLocaleString()} in / {call.output_tokens?.toLocaleString()} out
            </div>
          )}
          <div className="flex items-center gap-1">
            <Zap className="w-3 h-3" />
            {call.parse_method}
          </div>
        </div>
      </div>

      {/* Collapsible sections */}
      <div className="border-t border-slate-700/50">
        <CollapsibleSection
          label="System Prompt"
          expanded={expandSystem}
          onToggle={() => setExpandSystem(!expandSystem)}
          content={call.system_prompt}
        />
        <CollapsibleSection
          label="User Message"
          expanded={expandUser}
          onToggle={() => setExpandUser(!expandUser)}
          content={call.user_message}
        />
        <CollapsibleSection
          label="Extracted Result"
          expanded={expandResult}
          onToggle={() => setExpandResult(!expandResult)}
          content={call.extracted_result ?? '(none)'}
          isJson
        />
      </div>
    </div>
  )
}

function CollapsibleSection({
  label,
  expanded,
  onToggle,
  content,
  isJson,
}: {
  label: string
  expanded: boolean
  onToggle: () => void
  content: string
  isJson?: boolean
}) {
  let displayContent = content
  if (isJson && expanded) {
    try {
      displayContent = JSON.stringify(JSON.parse(content), null, 2)
    } catch {
      // not valid JSON, show as-is
    }
  }

  return (
    <div className="border-t border-slate-800/50">
      <button
        onClick={onToggle}
        className="flex items-center gap-2 w-full px-4 py-2 text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-800/30 transition-colors"
      >
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        {label}
        <span className="text-slate-600 ml-1">({content.length} chars)</span>
      </button>
      {expanded && (
        <pre className="px-4 pb-3 text-xs text-slate-300 whitespace-pre-wrap break-words max-h-60 overflow-y-auto font-mono bg-slate-900/50 mx-3 mb-3 rounded-lg p-3">
          {displayContent}
        </pre>
      )}
    </div>
  )
}
