import { useEffect, useState } from 'react'
import { MessageSquare, Send, Loader2, AlertCircle, Clock } from 'lucide-react'
import { clarifyRequest } from '../api/client'
import type { AuditRecord, ApprovalResponse, ClarificationResponse } from '../types'
import { isClarification, isApproval } from '../types'

interface Props {
  recordId: string
  questions: string[]
  deadline: string
  onResult: (record: AuditRecord) => void
  onClarification?: (response: ClarificationResponse) => void
  onApproval?: (response: ApprovalResponse) => void
  onExpired: () => void
}

export function ClarificationCard({ recordId, questions, deadline, onResult, onClarification, onApproval, onExpired }: Props) {
  const [answers, setAnswers] = useState<string[]>(questions.map(() => ''))
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

  // Map question text to answer field key
  const QUESTION_KEYS: Record<string, string> = {
    'budget': 'budget_eur',
    'quantity': 'quantity',
    'delivery': 'deadline_days',
    'when': 'deadline_days',
    'deadline': 'deadline_days',
    'describe': 'item_description',
    'what': 'item_description',
  }
  function questionToKey(q: string): string {
    const lower = q.toLowerCase()
    for (const [kw, key] of Object.entries(QUESTION_KEYS)) {
      if (lower.includes(kw)) return key
    }
    return `answer_${questions.indexOf(q)}`
  }

  async function handleSubmit() {
    if (loading) return
    const anyEmpty = answers.some((a) => !a.trim())
    if (anyEmpty) { setError('Please answer all questions.'); return }
    setLoading(true)
    setError(null)
    try {
      const answersMap: Record<string, unknown> = {}
      questions.forEach((q, i) => {
        const key = questionToKey(q)
        const raw = answers[i].trim()
        // Try numeric parse for budget/quantity/deadline
        if (['budget_eur', 'quantity', 'deadline_days'].includes(key)) {
          const num = parseFloat(raw.replace(/[€$,\s]/g, ''))
          answersMap[key] = isNaN(num) ? raw : num
        } else {
          answersMap[key] = raw
        }
      })
      const result = await clarifyRequest(recordId, answersMap)
      if (isClarification(result)) {
        onClarification?.(result)
      } else if (isApproval(result)) {
        onApproval?.(result)
      } else {
        onResult(result as AuditRecord)
      }
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      const msg = detail || (e instanceof Error ? e.message : 'Unknown error')
      setError(`Failed to submit: ${msg}`)
    } finally {
      setLoading(false)
    }
  }

  const hours = Math.floor(secondsLeft / 3600)
  const mins = Math.floor((secondsLeft % 3600) / 60)
  const secs = secondsLeft % 60
  const urgency = secondsLeft < 300   // < 5 min
  const expired = secondsLeft === 0

  return (
    <div className="card border border-amber-800/40 space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-2">
          <MessageSquare className="w-5 h-5 text-amber-400" />
          <h2 className="font-semibold text-white">Clarification Required</h2>
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
          This request has expired. Please submit a new request with all required information.
        </div>
      ) : (
        <>
          <p className="text-sm text-slate-400">
            Some information is missing from your request. Please answer the following questions to continue.
          </p>

          <div className="space-y-4">
            {questions.map((q, i) => (
              <div key={i}>
                <label className="block text-sm text-slate-300 mb-1.5 font-medium">{q}</label>
                <input
                  type="text"
                  value={answers[i]}
                  onChange={(e) => setAnswers((prev) => prev.map((a, j) => j === i ? e.target.value : a))}
                  onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
                  placeholder={
                    q.toLowerCase().includes('budget') ? 'e.g. €50,000' :
                    q.toLowerCase().includes('quantit') ? 'e.g. 50' :
                    q.toLowerCase().includes('deadline') || q.toLowerCase().includes('deliver') || q.toLowerCase().includes('when') ? 'e.g. 14 business days' :
                    'Your answer...'
                  }
                  className="w-full bg-slate-800 border border-slate-700 rounded-xl px-4 py-2.5
                             text-slate-100 placeholder-slate-500 text-sm
                             focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 transition-colors"
                />
              </div>
            ))}
          </div>

          {error && (
            <div className="flex items-center gap-2 text-sm text-red-400 bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              {error}
            </div>
          )}

          <button
            onClick={handleSubmit}
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl
                       bg-amber-500 hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed
                       text-slate-900 font-semibold text-sm transition-colors"
          >
            {loading ? <><Loader2 className="w-4 h-4 animate-spin" />Processing…</> : <><Send className="w-4 h-4" />Submit Answers</>}
          </button>
        </>
      )}
    </div>
  )
}
