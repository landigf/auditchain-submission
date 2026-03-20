// ── Core domain types matching backend API responses ─────────────────────────

export type DecisionType = 'approved' | 'escalated' | 'rejected' | 'clarification_needed'
export type RecordState = 'completed' | 'clarification_needed' | 'awaiting_approval' | 'abandoned' | 'processing'
export type AISGrade = 'Audit-Ready' | 'Review Recommended' | 'Manual Review Required' | 'Incomplete'

export interface RequesterContext {
  company: string
  department: string
  spending_authority_eur: number
}

export interface StructuredRequest {
  item_description: string
  category: string
  quantity: number | null
  unit: string | null
  deadline_days: number | null
  budget_eur: number | null
  preferred_supplier_id: string | null
  preferred_supplier_name: string | null
  requester_department: string | null
  special_requirements: string[]
  ambiguities: string[]
  missing_fields?: string[]
}

export interface PolicyResult {
  rule_id: string
  rule_name: string
  description: string
  triggered: boolean
  action: 'block' | 'warn' | 'escalate' | 'pass'
  detail: string
  escalate_to: string | null
}

export interface FuzzyThreshold {
  primary_tier: string
  memberships: Record<string, number>
  is_borderline: boolean
  borderline_tiers: string[]
  proximity_warning: string | null
  approver: string
  min_quotes: number
  recommendation?: string
}

export interface PolicyResults {
  violations: PolicyResult[]
  warnings: PolicyResult[]
  escalations: PolicyResult[]
  all_clear: boolean
  fuzzy_threshold?: FuzzyThreshold
}

export interface Supplier {
  id: string
  name: string
  category: string
  unit_price_eur: number
  min_quantity: number
  delivery_days: number
  compliance_status: 'approved' | 'blocked' | 'under_review'
  esg_score: number
  preferred_tier: 'preferred' | 'approved' | 'spot'
  contract_status: 'active' | 'expired' | 'none'
  country: string
  eu_based: boolean
  notes: string | null
  disqualified?: boolean
  disqualification_reasons?: string[]
}

export interface ScoredSupplier extends Supplier {
  score: number
  score_breakdown: {
    price_score: number
    delivery_score: number
    compliance_score: number
    esg_score_normalized: number
    historical_bonus?: number
    historical_note?: string
    weights_used: Record<string, number>
  }
  total_cost_eur: number
  within_budget: boolean
  rank: number
  volume_discount_note?: string
  unit_price_eur_volume?: number
  fuzzy_score?: number
  fuzzy_linguistic?: string
}

export interface SupplierResults {
  candidates: Supplier[]
  disqualified: Supplier[]
  total_found: number
  total_eligible: number
  infeasibility?: {
    infeasible: boolean
    reason: string
    min_cost_eur: number
    max_affordable_qty: number
  } | null
}

export interface Counterfactual {
  supplier_id: string
  supplier_name?: string
  current_rank?: number
  current_score?: number
  gap_to_winner?: number
  scenario?: string
  hypothetical_rank?: number
  impact?: string
  what_if?: string[]
}

export interface SensitivityResult {
  ranking_stable: boolean
  flips?: Array<{ criterion: string; direction: string; new_winner: string }>
  rank_switches?: Array<{ from: string; to: string; criterion: string }>
  stable_top_3?: boolean
  stability_score?: number
}

export interface ScoringResult {
  scored: ScoredSupplier[]
  scoring_warnings: Array<{ supplier_id: string; rule_id: string; detail: string }>
  counterfactuals?: Counterfactual[]
  sensitivity?: SensitivityResult
}

export interface Decision {
  // From GET /decision/{id}
  type?: DecisionType
  // From POST /submit (same record, different field)
  decision_type?: DecisionType
  recommended_supplier_id: string | null
  recommended_supplier_name: string | null
  estimated_total_eur: number | null
  confidence: number | null
  reasoning_narrative: string | null
  escalation_reason: string | null
  escalated_to?: string | null
  rejection_reason: string | null
  // Full object — present in submit response
  recommended_supplier?: ScoredSupplier | null
  alternatives?: ScoredSupplier[]
}

export interface AISComponents {
  request_completeness: number
  policy_coverage: number
  traceability: number
  supplier_justification: number
  decision_correctness: number
}

export interface AIS {
  score: number
  grade: AISGrade
  components: AISComponents
  eu_ai_act_article_13_compliant: boolean
  flags?: string[]
}

export interface RiskScore {
  score: number
  approach: 'linear' | 'fuzzy'
  inputs: Record<string, number | string | null>
  breakdown: Record<string, number>
  memberships?: Record<string, Record<string, number>>
  rules_fired?: string[]
}

export interface PipelineStep {
  step: string
  ms: number
  llm: boolean
  summary: string
}

export interface FuzzyTrace {
  risk: RiskScore | null
  threshold: FuzzyThreshold | null
  sensitivity: SensitivityResult | null
  counterfactuals: Counterfactual[] | null
  confidence_gate: { confidence: number; confidence_label: string; uncertainty_signals: string[] } | null
}

export interface AuditRecord {
  record_id: string
  created_at: string
  state: RecordState
  agent_version: string
  raw_request: string
  structured_request: StructuredRequest
  policy_results: PolicyResults
  supplier_results: SupplierResults
  scoring_result: ScoringResult
  decision: Decision
  ais: AIS
  risk_score?: RiskScore | number | null
  pipeline_trace?: PipelineStep[]
  fuzzy_trace?: FuzzyTrace
}

// ── Clarification flow ────────────────────────────────────────────────────────

export interface ClarificationResponse {
  record_id: string
  state: 'clarification_needed'
  questions: string[]
  clarification_deadline: string
  timeout_hours: number
}

// ── Approval flow (escalated decisions) ──────────────────────────────────────

export interface ApprovalResponse {
  record_id: string
  state: 'awaiting_approval'
  decision: Decision
  approval_questions: string[]
  approval_deadline: string
  timeout_hours: number
  structured_request: StructuredRequest
  policy_results: PolicyResults
  supplier_results: SupplierResults
  scoring_result: ScoringResult
  ais: AIS
  risk_score: RiskScore
  pipeline_trace: PipelineStep[]
}

export type SubmitResponse = AuditRecord | ClarificationResponse | ApprovalResponse

export function isClarification(r: SubmitResponse): r is ClarificationResponse {
  return r.state === 'clarification_needed' && 'questions' in r
}

export function isApproval(r: SubmitResponse): r is ApprovalResponse {
  return r.state === 'awaiting_approval' && 'approval_questions' in r
}

// ── History ───────────────────────────────────────────────────────────────────

export interface HistoryItem {
  record_id: string
  created_at: string
  state: RecordState
  raw_request: string
  decision_type: DecisionType
  recommended_supplier_name: string | null
  estimated_total_eur: number | null
  ais_score: number
  ais_grade: AISGrade
  risk_score?: number | null
}

// ── History Stats ─────────────────────────────────────────────────────────────

export interface HistoryStats {
  total: number
  approved: number
  escalated: number
  rejected: number
  escalation_rate: number
  approved_rate: number
  avg_ais: number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Normalise the decision type across both response shapes */
export function decisionType(d: Decision | undefined | null): DecisionType {
  if (!d) return 'escalated'
  return (d.type ?? d.decision_type ?? 'escalated') as DecisionType
}
