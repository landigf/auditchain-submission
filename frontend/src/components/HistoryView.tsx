import { useEffect, useState } from 'react'
import { History, ChevronRight, Loader2, BarChart3 } from 'lucide-react'
import { getHistory, getHistoryStats } from '../api/client'
import type { HistoryItem, HistoryStats } from '../types'
import { DecisionBadge } from './DecisionBadge'

interface Props {
  onSelect: (recordId: string) => void
  refreshKey: number
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleString('en-GB', { day: 'numeric', month: 'short' })
}

export function HistoryView({ onSelect, refreshKey }: Props) {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [stats, setStats] = useState<HistoryStats | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([getHistory(), getHistoryStats().catch(() => null)])
      .then(([history, st]) => { setItems(history); setStats(st) })
      .finally(() => setLoading(false))
  }, [refreshKey])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-neutral-500" />
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="text-center py-12">
        <History className="w-8 h-8 text-neutral-600 mx-auto mb-3" />
        <p className="text-neutral-500">No decisions yet. Submit a request to get started.</p>
      </div>
    )
  }

  return (
    <div>
      <h3 className="text-sm font-semibold text-neutral-400 mb-4 uppercase tracking-wider flex items-center gap-2">
        <History className="w-4 h-4" />
        Decision History
      </h3>

      {/* Dashboard stats */}
      {stats && stats.total > 0 && (
        <div className="grid grid-cols-4 gap-2 mb-4">
          {[
            { label: 'Total', value: stats.total, color: 'text-slate-100' },
            { label: 'Approved', value: `${Math.round(stats.approved_rate)}%`, color: 'text-green-400' },
            { label: 'Escalated', value: `${Math.round(stats.escalation_rate)}%`, color: 'text-amber-400' },
            { label: 'Avg AIS', value: Math.round(stats.avg_ais), color: 'text-brand-400' },
          ].map((s) => (
            <div key={s.label} className="bg-slate-800/60 rounded-lg px-2 py-2 text-center">
              <div className={`text-lg font-bold ${s.color}`}>{s.value}</div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wide">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-2">
        {items.map((item) => (
          <button
            key={item.record_id}
            onClick={() => onSelect(item.record_id)}
            className="w-full grid grid-cols-[11rem_1fr_auto] items-center gap-4 p-3 rounded-xl bg-neutral-800/50 hover:bg-neutral-800
                       border border-neutral-800 hover:border-neutral-700 transition-colors text-left group"
          >
            <div><DecisionBadge type={item.decision_type} /></div>
            <div className="min-w-0">
              <span className="text-neutral-200 text-sm truncate block">{item.raw_request}</span>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {item.ais_score === 0 && ['clarification_needed', 'awaiting_approval', 'processing'].includes(item.state) ? (
                <span className="text-xs font-bold text-neutral-400">Pending</span>
              ) : (
                <span className={`text-xs font-bold ${
                  item.ais_score >= 85 ? 'text-green-400' :
                  item.ais_score >= 65 ? 'text-amber-400' : 'text-red-400'
                }`}>
                  AIS {item.ais_score}
                </span>
              )}
              <span className="text-xs text-neutral-500">{formatDate(item.created_at)}</span>
              <ChevronRight className="w-4 h-4 text-neutral-600 group-hover:text-neutral-400 transition-colors" />
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
