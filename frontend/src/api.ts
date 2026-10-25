export type Incident = { id: string; supplier: string; original_transfer_id: string; amount: string; currency: string; supplier_claim: string; state: string; external_status: string | null; source: string; simulated: boolean; created_at: string; evidence?: { kind: string; source: string; payload: Record<string,unknown>; captured_at: string }[] }
export type IncidentRecommendation = { incident_id: string; verified_facts: Record<string,unknown>; unverified_claims: string[]; ai_interpretation: { summary: string; uncertainty_flags: string[]; next_step: string } | null; ai_status: string; proposed_action: string; next_step: string; financial_action_authorized: false; note: string }
const API_URL = (import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://localhost:8000' : '')).replace(/\/$/, '')
export function token() { return sessionStorage.getItem('tp-token') || '' }
export function setToken(value: string) { sessionStorage.setItem('tp-token', value) }
export function clearToken() { sessionStorage.removeItem('tp-token') }
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(token() ? { Authorization: `Bearer ${token()}` } : {}), ...init.headers } })
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `Request failed (${response.status})`) }
  return response.json()
}
