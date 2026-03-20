import axios from 'axios'
import type { AuditRecord, HistoryItem, HistoryStats, SubmitResponse, RequesterContext, ApprovalResponse, ClarificationResponse } from '../types'

const BASE = import.meta.env.VITE_API_URL ?? ''
const api = axios.create({ baseURL: `${BASE}/api` })

export async function submitRequest(
  requestText: string,
  requesterContext?: RequesterContext,
): Promise<SubmitResponse> {
  const { data } = await api.post<SubmitResponse>('/submit', {
    request_text: requestText,
    requester_context: requesterContext ?? null,
  })
  return data
}

export async function clarifyRequest(
  recordId: string,
  answers: Record<string, unknown>,
): Promise<SubmitResponse> {
  const { data } = await api.post<SubmitResponse>(`/decision/${recordId}/clarify`, { answers })
  return data
}

export async function approveDecision(
  recordId: string,
  action: 'approve' | 'reject',
  reason?: string,
  responderName?: string,
): Promise<AuditRecord> {
  const { data } = await api.post<AuditRecord>(`/decision/${recordId}/approve`, {
    action,
    reason: reason ?? null,
    responder_name: responderName ?? null,
  })
  return data
}

export async function getDecision(recordId: string): Promise<AuditRecord> {
  const { data } = await api.get<AuditRecord>(`/decision/${recordId}`)
  return data
}

export async function getDecisionStatus(recordId: string) {
  const { data } = await api.get(`/decision/${recordId}/status`)
  return data
}

export async function getHistory(): Promise<HistoryItem[]> {
  const { data } = await api.get<HistoryItem[]>('/history')
  return data
}

export async function getHistoryStats(): Promise<HistoryStats> {
  const { data } = await api.get<HistoryStats>('/history/stats')
  return data
}

export function exportAuditJsonUrl(recordId: string) {
  return `${BASE}/api/decision/${recordId}/export/json`
}

export function exportAuditPdfUrl(recordId: string) {
  return `${BASE}/api/decision/${recordId}/export/pdf`
}

export async function getLLMProvider(): Promise<string> {
  const { data } = await api.get<{ provider: string }>('/admin/llm-provider')
  return data.provider
}

export async function setLLMProvider(provider: 'claude' | 'openai'): Promise<string> {
  const { data } = await api.post<{ provider: string }>(`/admin/llm-provider/${provider}`)
  return data.provider
}

export interface LLMCallLog {
  id: string
  call_type: string
  model: string
  temperature: number
  system_prompt: string
  user_message: string
  extracted_result: string | null
  input_tokens: number | null
  output_tokens: number | null
  latency_ms: number | null
  timestamp: string
  parse_method: string
}

export async function getLLMCalls(recordId: string): Promise<LLMCallLog[]> {
  const { data } = await api.get<LLMCallLog[]>(`/decision/${recordId}/llm-calls`)
  return data
}
