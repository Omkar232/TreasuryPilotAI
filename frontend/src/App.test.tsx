import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><BrowserRouter><App/></BrowserRouter></QueryClientProvider>)
}

describe('TreasuryPilot shell', () => {
  it('requires an operator token before showing financial operations', () => {
    sessionStorage.clear()
    renderApp()
    expect(screen.getByText('Welcome back')).toBeInTheDocument()
    expect(screen.queryByText('Available balances')).not.toBeInTheDocument()
  })

  it('shows persisted obligations and audit data while failing closed on an unverified balance read', async () => {
    sessionStorage.setItem('tp-token', 'operator-test-token')
    const due = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString()
    const fixtures:Record<string, unknown> = {
      '/api/integrations/airwallex/status': { configured: true, status: 'configured_not_verified', note: 'Connection is not verified.' },
      '/api/treasury/balances': { detail: 'Airwallex permission: forbidden' },
      '/api/incidents': [{ id:'incident-1',supplier:'Northwind',original_transfer_id:'tr-1',amount:'9007199254740993.1234',currency:'USD',supplier_claim:'Payment has not arrived',state:'OUTCOME_UNKNOWN',external_status:null,source:'unverified',simulated:false,created_at:new Date().toISOString() }],
      '/api/treasury/obligations': [
        { id:1,supplier:'Northwind',amount:'1200',currency:'USD',due_at:due,priority:'CRITICAL',status:'OPEN',source:'operator_entered',simulated:false },
        { id:2,supplier:'Acme',amount:'400',currency:'EUR',due_at:null,priority:'NORMAL',status:'OPEN',source:'operator_entered',simulated:false },
        { id:3,supplier:'Small one',amount:'0.1',currency:'USD',due_at:null,priority:'LOW',status:'OPEN',source:'operator_entered',simulated:false },
        { id:4,supplier:'Small two',amount:'0.2',currency:'USD',due_at:null,priority:'LOW',status:'OPEN',source:'operator_entered',simulated:false },
        { id:5,supplier:'Precision record',amount:'9007199254740993.1234',currency:'USD',due_at:null,priority:'NORMAL',status:'OPEN',source:'operator_entered',simulated:false },
      ],
      '/api/treasury/policies': [],
      '/api/audit?limit=20': [{ actor:'operator',timestamp:new Date().toISOString(),event_type:'INCIDENT_CREATED',incident_id:'incident-1',external_transfer_id:'tr-1',new_state:'NEW',policy_version:null,approval_reference:null }],
    }
    vi.stubGlobal('fetch', vi.fn(async (input:RequestInfo | URL) => {
      const url = String(input)
      const key = Object.keys(fixtures).find(path=>url.endsWith(path))
      const body = key ? fixtures[key] : { detail:'Not found' }
      return new Response(JSON.stringify(body), { status:key === '/api/treasury/balances' ? 502 : key ? 200 : 404, headers:{'Content-Type':'application/json'} })
    }))

    renderApp()
    expect(await screen.findByText('5 records')).toBeInTheDocument()
    expect(await screen.findByText(/EUR 400.*USD 9,007,199,254,742,193\.4234/)).toBeInTheDocument()
    expect(await screen.findByText('USD 9,007,199,254,740,993.1234')).toBeInTheDocument()
    expect(screen.getByText('1 obligation')).toBeInTheDocument()
    expect(screen.getByText('Not enabled')).toBeInTheDocument()
    expect(await screen.findByText(/Airwallex balance read failed/)).toBeInTheDocument()
    expect(await screen.findByText('Airwallex permission: forbidden')).toBeInTheDocument()
    expect(screen.getByText(/No USD reserve policy/)).toBeInTheDocument()
    expect(screen.getByText(/No EUR reserve policy/)).toBeInTheDocument()
    expect(screen.getByText('incident created')).toBeInTheDocument()
    expect(screen.queryByText(/USD 1,200 available/)).not.toBeInTheDocument()
  })
})

afterEach(() => {
  cleanup()
  sessionStorage.clear()
  vi.unstubAllGlobals()
})
