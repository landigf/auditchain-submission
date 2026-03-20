import { useEffect, useState } from 'react'
import { ShieldAlert, Check, X, Loader2, Clock, AlertCircle, MessageSquare, Layers } from 'lucide-react'
import { approveDecision } from '../api/client'
import type { AuditRecord, PolicyResults } from '../types'

interface Props {
  recordId: string
  questions: string[]
  deadline: string
  escalationReason: string | null
  policyResults?: PolicyResults
  onResult: (record: AuditRecord) => void
  onExpired: () => void
}

export function ApprovalCard({ recordId, questions, deadline, escalationReason, policyResults, onResult, onExpired }: Props) {
  const [notes, setNotes] = useState('')
  const [rejectReason, setRejectReason] = useState('')
  const [showReject, setShowReject] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [secondsLeft, setSecondsLeft] = useState(() =>
    Math.max(0, Math.floor((new Date(deadline).getTime() - Date.now()) / 1000))
  )

  useEffect(() => {
    const interval = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) { clearInterval(interval); onExpired(); return 0 }
        return s - 1
      })
    }, 1000)
    return () => clearInterval(interval)
  }, [onExpired])

  async function handleApprove() {
    if (loading) return
    setLoading(true)
    setError(null)
    try {
      const result = await approveDecision(recordId, 'approve', notes || undefined)
      onResult(result)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      setError(`Failed to approve: ${msg}`)
    } finally {
      setLoading(false)
    }
  }

  async function handleReject() {
    if (loading) return
    if (!rejectReason.trim()) { setError('Please provide a reason for rejection.'); return }
    setLoading(true)
    setError(null)
    try {
      const result = await approveDecision(recordId, 'reject', rejectReason)
      onResult(result)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      setError(`Failed to reject: ${msg}`)
    } finally {
      setLoading(false)
    }
  }

  const hours = Math.floor(secondsLeft / 3600)
  const mins = Math.floor((secondsLeft % 3600) / 60)
  const secs = secondsLeft % 60
  const urgency = secondsLeft < 3600  // < 1 hour
  const expired = secondsLeft === 0

  return (
    <div className="card border border-amber-800/40 space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-amber-400" />
            <h2 className="font-semibold text-white">
              Approval Required
              {(() => {
                const approver = policyResults?.fuzzy_threshold?.approver
                  ?? policyResults?.escalations?.[0]?.escalate_to
                return approver ? ` — ${approver}` : ''
              })()}
            </h2>
          </div>
          {policyResults?.fuzzy_threshold && (
            <div className="flex items-center gap-2 mt-1.5 ml-7">
              <span className="text-xs px-2 py-0.5 rounded border border-amber-800 bg-amber-950 text-amber-400 font-medium flex items-center gap-1">
                <Layers className="w-3 h-3" />
                {policyResults.fuzzy_threshold.primary_tier.replace('tier', 'Tier ')}
              </span>
              <span className="text-xs text-slate-400">
                Min {policyResults.fuzzy_threshold.min_quotes} quotes required
              </span>
              {policyResults.fuzzy_threshold.is_borderline && (
                <span className="text-xs text-amber-400 italic">
                  {policyResults.fuzzy_threshold.proximity_warning ?? 'Borderline tier classification'}
                </span>
              )}
            </div>
          )}
        </div>
        <div className={`flex items-center gap-1.5 text-sm font-mono px-3 py-1 rounded-lg border ${
          expired
            ? 'bg-red-950/50 border-red-800 text-red-400'
            : urgency
            ? 'bg-red-950/30 border-red-900 text-red-400 animate-pulse'
            : 'bg-amber-950/30 border-amber-900 text-amber-400'
        }`}>
          <Clock className="w-3.5 h-3.5" />
          {expired ? 'Expired' : `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`}
        </div>
      </div>

      {expired ? (
        <div className="text-sm text-red-400 bg-red-950/30 border border-red-800/30 rounded-lg p-4">
          This approval request has expired. The decision has been marked as abandoned.
        </div>
      ) : (
        <>
          {/* Escalation reason */}
          {escalationReason && (
            <div className="text-sm text-amber-300 bg-amber-950/30 border border-amber-800/30 rounded-lg p-4">
              <div className="font-semibold text-amber-400 mb-1 flex items-center gap-1.5">
                <AlertCircle className="w-4 h-4" />
                Escalation Reason
              </div>
              {escalationReason}
            </div>
          )}

          {/* Approval questions */}
          <div className="space-y-3">
            <div className="flex items-center gap-1.5 text-sm text-slate-400">
              <MessageSquare className="w-4 h-4" />
              Review the following before approving:
            </div>
            {questions.map((q, i) => (
              <div key={i} className="text-sm text-slate-300 bg-slate-800/50 rounded-lg px-4 py-3 border border-slate-700/50">
                {q}
              </div>
            ))}
          </div>

          {/* Notes input */}
          <div>
            <label className="block text-sm text-slate-400 mb-1.5">Approval notes (optional)</label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Any conditions or notes for this approval..."
              className="w-full bg-slate-800 border border-slate-700 rounded-xl px-4 py-2.5
                         text-slate-100 placeholder-slate-500 text-sm
                         focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 transition-colors"
            />
          </div>

          {/* Reject reason (shown on demand) */}
          {showReject && (
            <div>
              <label className="block text-sm text-red-400 mb-1.5">Reason for rejection *</label>
              <input
                type="text"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleReject()}
                placeholder="Why is this decision being rejected?"
                className="w-full bg-slate-800 border border-red-800/50 rounded-xl px-4 py-2.5
                           text-slate-100 placeholder-slate-500 text-sm
                           focus:outline-none focus:border-red-500 focus:ring-1 focus:ring-red-500 transition-colors"
              />
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 text-sm text-red-400 bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              {error}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex gap-3">
            <button
              onClick={handleApprove}
              disabled={loading}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl
                         bg-green-600 hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed
                         text-white font-semibold text-sm transition-colors"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              Approve
            </button>
            {!showReject ? (
              <button
                onClick={() => setShowReject(true)}
                disabled={loading}
                className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl
                           border border-red-800/50 text-red-400 hover:bg-red-950/30
                           disabled:opacity-50 disabled:cursor-not-allowed
                           font-semibold text-sm transition-colors"
              >
                <X className="w-4 h-4" />
                Reject
              </button>
            ) : (
              <button
                onClick={handleReject}
                disabled={loading}
                className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl
                           bg-red-600 hover:bg-red-500 disabled:opacity-50 disabled:cursor-not-allowed
                           text-white font-semibold text-sm transition-colors"
              >
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <X className="w-4 h-4" />}
                Confirm Rejection
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
