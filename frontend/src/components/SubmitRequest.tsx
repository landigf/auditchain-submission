import { useState } from 'react'
import { Send, Loader2 } from 'lucide-react'
import { submitRequest } from '../api/client'
import type { AuditRecord, ClarificationResponse, ApprovalResponse, RequesterContext } from '../types'
import { isClarification, isApproval } from '../types'

const DEMO_SCENARIOS = [
  {
    label: '🇬🇧 IT Equipment',
    text: 'I need 20 ergonomic office chairs for our Zurich office. Budget €30,000. Need them within 14 business days.',
  },
  {
    label: '🇩🇪 Deutsch',
    text: 'Wir brauchen 50 Laptops für unser Büro in Berlin. Budget beträgt €45.000, Lieferung innerhalb von 10 Werktagen bitte.',
  },
  {
    label: '🇫🇷 Français',
    text: 'Nous avons besoin de 15 écrans 4K et 15 stations d\'accueil pour notre équipe à Genève. Budget de €12.000, livraison sous 3 semaines.',
  },
  {
    label: '🇮🇹 Italiano',
    text: 'Servono 200 smartphone aziendali per il reparto vendite a Milano. Budget €80.000, consegna entro 7 giorni lavorativi.',
  },
  {
    label: '🇪🇸 Español',
    text: 'Necesitamos un servicio de consultoría en ciberseguridad para nuestra oficina de Madrid. Presupuesto €150.000, plazo de 30 días.',
  },
]

const REQUESTERS: Array<RequesterContext & { label: string }> = [
  { label: 'No budget', company: '', department: '', spending_authority_eur: 0 },
  { label: 'UBS IT · €25k', company: 'UBS', department: 'IT', spending_authority_eur: 25000 },
  { label: 'Nestlé · €100k', company: 'Nestlé', department: 'Procurement', spending_authority_eur: 100000 },
  { label: 'Swiss Post · €50k', company: 'Swiss Post', department: 'Operations', spending_authority_eur: 50000 },
  { label: 'ABB · €75k', company: 'ABB', department: 'Engineering', spending_authority_eur: 75000 },
  { label: 'Roche · €200k', company: 'Roche', department: 'Research', spending_authority_eur: 200000 },
]

interface Props {
  onResult: (record: AuditRecord) => void
  onClarification: (response: ClarificationResponse) => void
  onApproval: (response: ApprovalResponse) => void
}

export function SubmitRequest({ onResult, onClarification, onApproval }: Props) {
  const [text, setText] = useState('')
  const [requesterIdx, setRequesterIdx] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit() {
    if (!text.trim() || loading) return
    setLoading(true)
    setError(null)
    try {
      const requester = REQUESTERS[requesterIdx]
      const ctx = requester.spending_authority_eur > 0 ? requester : undefined
      const result = await submitRequest(text, ctx)
      if (isClarification(result)) {
        onClarification(result)
      } else if (isApproval(result)) {
        onApproval(result)
      } else {
        onResult(result as AuditRecord)
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      setError(`Agent error: ${msg}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h2 className="text-3xl font-semibold text-white mb-6">Sourcing Request</h2>

      <div className="space-y-3">
        <div className="relative">
          <textarea
            className="w-full h-32 bg-neutral-900 border border-neutral-800 rounded-xl px-4 py-3 pb-14
                       text-white placeholder-neutral-500 resize-none pr-4
                       focus:outline-none focus:border-neutral-600
                       transition-colors text-base"
            placeholder='e.g. "Need 200 laptops for Zurich office by end of April. Budget €150,000."'
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit() }}
          />
          <button
            onClick={handleSubmit}
            disabled={!text.trim() || loading}
            className="absolute bottom-4 right-4 flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-500 hover:bg-brand-600
                       disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold text-sm transition-colors"
          >
            {loading ? (
              <><Loader2 className="w-4 h-4 animate-spin" />Processing…</>
            ) : (
              <><Send className="w-4 h-4" />Submit</>
            )}
          </button>
        </div>

        <div className="flex flex-wrap gap-2">
          <select
            value={requesterIdx}
            onChange={(e) => setRequesterIdx(Number(e.target.value))}
            className="appearance-none text-xs px-3 py-1.5 rounded-lg bg-neutral-900 text-neutral-400
                       border border-neutral-800 focus:outline-none transition-colors cursor-pointer"
          >
            {REQUESTERS.map((r, i) => (
              <option key={i} value={i}>{r.label}</option>
            ))}
          </select>

          <div className="w-px bg-neutral-800 self-stretch" />

          {DEMO_SCENARIOS.map((s) => (
            <button
              key={s.label}
              onClick={() => setText(s.text)}
              className="text-xs px-3 py-1.5 rounded-lg bg-neutral-900 hover:bg-neutral-800 text-neutral-400 border border-neutral-800 transition-colors"
            >
              {s.label}
            </button>
          ))}
        </div>

        {error && (
          <div className="text-sm text-red-400 bg-red-950/50 border border-red-800/50 rounded-lg px-4 py-2">
            {error}
          </div>
        )}
      </div>
    </div>
  )
}
