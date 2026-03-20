import { useState, useEffect } from 'react'
import { Shield, Plus, Zap } from 'lucide-react'
import { SubmitRequest } from './components/SubmitRequest'
import { DecisionView } from './components/DecisionView'
import { AuditTrail } from './components/AuditTrail'
import { ClarificationCard } from './components/ClarificationCard'
import { ApprovalCard } from './components/ApprovalCard'
import { HistoryView } from './components/HistoryView'
import type { AuditRecord, ClarificationResponse, ApprovalResponse, ScoredSupplier } from './types'
import { getDecision, getLLMProvider, setLLMProvider } from './api/client'

type View = 'home' | 'clarification' | 'approval' | 'decision' | 'confirmed' | 'audit'

export default function App() {
  const [view, setView] = useState<View>('home')
  const [currentRecord, setCurrentRecord] = useState<AuditRecord | null>(null)
  const [escalation, setEscalation] = useState<ApprovalResponse | null>(null)
  const [confirmedSupplier, setConfirmedSupplier] = useState<ScoredSupplier | null>(null)
  const [clarification, setClarification] = useState<ClarificationResponse | null>(null)
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0)
  const [llmProvider, setLlmProvider] = useState<'claude' | 'openai'>('openai')

  useEffect(() => {
    getLLMProvider().then((p) => setLlmProvider(p as 'claude' | 'openai')).catch(() => {})
  }, [])

  async function toggleProvider() {
    const next = llmProvider === 'claude' ? 'openai' : 'claude'
    await setLLMProvider(next)
    setLlmProvider(next)
  }

  function handleResult(record: AuditRecord) {
    setCurrentRecord(record)
    setClarification(null)
    setEscalation(null)
    setView('decision')
    setHistoryRefreshKey((k) => k + 1)
  }

  function handleClarification(response: ClarificationResponse) {
    setClarification(response)
    setView('clarification')
  }

  function handleEscalation(response: ApprovalResponse) {
    setEscalation(response)
    setView('approval')
  }

  function handleConfirmOrder(supplier: ScoredSupplier) {
    setConfirmedSupplier(supplier)
    setView('confirmed')
  }

  async function handleSelectHistory(recordId: string) {
    const record = await getDecision(recordId)
    setCurrentRecord(record)
    setView('decision')
  }

  return (
    <div className="min-h-screen bg-black">
      {/* Top nav */}
      <nav className="border-b border-neutral-800 bg-black/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-4 h-14 flex items-center justify-between">
          <button onClick={() => setView('home')} className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
            <Shield className="w-6 h-6 text-brand-500" />
            <span className="font-bold text-white tracking-tight">AuditChain</span>
          </button>
          <div className="flex items-center gap-1">
            <NavBtn active={view === 'home'} onClick={() => setView('home')} icon={<Plus className="w-4 h-4" />} label="New Request" />
            <button
              onClick={toggleProvider}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ml-2 border ${
                llmProvider === 'claude'
                  ? 'border-orange-700/50 bg-orange-950/30 text-orange-400 hover:bg-orange-950/50'
                  : 'border-blue-700/50 bg-blue-950/30 text-blue-400 hover:bg-blue-950/50'
              }`}
              title={`Using ${llmProvider === 'claude' ? 'Claude (paid)' : 'GPT-4o (OpenAI)'}. Click to switch.`}
            >
              <Zap className="w-3 h-3" />
              {llmProvider === 'claude' ? 'Claude' : 'GPT-4o'}
            </button>
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main className={`max-w-3xl mx-auto px-4 pb-8 ${view === 'decision' || view === 'confirmed' ? 'pt-8' : 'pt-20'}`}>

        {/* ── HOME ── */}
        {view === 'home' && (
          <div className="space-y-20 animate-fade-up">
            <SubmitRequest onResult={handleResult} onClarification={handleClarification} onApproval={handleEscalation} />
            <HistoryView onSelect={handleSelectHistory} refreshKey={historyRefreshKey} />
          </div>
        )}

        {/* ── CLARIFICATION: questions before anything else ── */}
        {view === 'clarification' && clarification && (
          <div className="max-w-xl mx-auto">
            <ClarificationCard
              recordId={clarification.record_id}
              questions={clarification.questions}
              deadline={clarification.clarification_deadline}
              onResult={handleResult}
              onClarification={handleClarification}
              onApproval={handleEscalation}
              onExpired={() => setView('home')}
            />
          </div>
        )}

        {/* ── APPROVAL: client answers questions, then sees results ── */}
        {view === 'approval' && escalation && (
          <div className="max-w-xl mx-auto animate-fade-up">
            <ApprovalCard
              recordId={escalation.record_id}
              questions={escalation.approval_questions}
              deadline={escalation.approval_deadline}
              escalationReason={escalation.decision?.escalation_reason ?? null}
              policyResults={escalation.policy_results}
              onResult={handleResult}
              onExpired={() => setView('home')}
            />
          </div>
        )}

        {/* ── DECISION: approved → select supplier + confirm ── */}
        {view === 'decision' && currentRecord && (
          <div className="animate-fade-up">
            <DecisionView
              record={currentRecord}
              onViewAudit={() => setView('audit')}
              onBack={() => setView('home')}
              onConfirmOrder={handleConfirmOrder}
            />
          </div>
        )}

        {/* ── CONFIRMED: order confirmation page ── */}
        {view === 'confirmed' && confirmedSupplier && currentRecord && (
          <div className="max-w-xl mx-auto animate-fade-up">
            <OrderConfirmation
              record={currentRecord}
              supplier={confirmedSupplier}
              onBack={() => setView('home')}
            />
          </div>
        )}

        {/* ── AUDIT TRAIL ── */}
        {view === 'audit' && currentRecord && (
          <AuditTrail record={currentRecord} onBack={() => setView('decision')} />
        )}
      </main>
    </div>
  )
}

// ── Order Confirmation ────────────────────────────────────────────────────────

function OrderConfirmation({ record, supplier, onBack }: {
  record: AuditRecord; supplier: ScoredSupplier; onBack: () => void
}) {
  const qty = record.structured_request?.quantity ?? '?'
  const item = record.structured_request?.item_description ?? 'items'

  return (
    <div className="space-y-6">
      <div className="card border border-green-800/40 space-y-5">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-full bg-green-950/50 border border-green-800/50 flex items-center justify-center">
            <Shield className="w-6 h-6 text-green-400" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-white">Order Confirmed</h2>
            <p className="text-sm text-neutral-400">Your procurement order has been placed</p>
          </div>
        </div>

        <div className="bg-green-950/20 border border-green-800/30 rounded-lg p-4 space-y-3">
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <div className="text-neutral-500 text-xs uppercase tracking-wider mb-0.5">Supplier</div>
              <div className="text-white font-medium">{supplier.name}</div>
            </div>
            <div>
              <div className="text-neutral-500 text-xs uppercase tracking-wider mb-0.5">Total Cost</div>
              <div className="text-white font-medium">{'\u20AC'}{supplier.total_cost_eur?.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-neutral-500 text-xs uppercase tracking-wider mb-0.5">Quantity</div>
              <div className="text-white">{qty} {item}</div>
            </div>
            <div>
              <div className="text-neutral-500 text-xs uppercase tracking-wider mb-0.5">Delivery</div>
              <div className="text-white">{supplier.delivery_days} business days</div>
            </div>
            <div>
              <div className="text-neutral-500 text-xs uppercase tracking-wider mb-0.5">Unit Price</div>
              <div className="text-white">{'\u20AC'}{supplier.unit_price_eur?.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-neutral-500 text-xs uppercase tracking-wider mb-0.5">Country</div>
              <div className="text-white">{supplier.country}</div>
            </div>
          </div>
        </div>

        <div className="text-xs text-neutral-600 text-center">
          Order reference: {record.record_id.slice(0, 8)}
        </div>
      </div>

      <button
        onClick={onBack}
        className="w-full py-2.5 rounded-xl border border-neutral-700 text-neutral-400
                   hover:bg-neutral-800 hover:text-white transition-colors text-sm font-medium"
      >
        Back to Home
      </button>
    </div>
  )
}

function NavBtn({ active, onClick, icon, label }: {
  active: boolean; onClick: () => void; icon: React.ReactNode; label: string
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors ${
        'border border-transparent text-neutral-400 hover:border-neutral-700 hover:bg-neutral-800/50 hover:text-neutral-200'
      }`}
    >
      {icon}
      <span className="hidden sm:inline">{label}</span>
    </button>
  )
}
