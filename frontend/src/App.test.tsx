import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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
      '/api/approvals': [],
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
    expect(await screen.findByText('No persisted approval requests')).toBeInTheDocument()
    expect(await screen.findByText(/Airwallex balance read failed/)).toBeInTheDocument()
    expect(await screen.findByText('Airwallex permission: forbidden')).toBeInTheDocument()
    expect(screen.getByText(/No USD reserve policy/)).toBeInTheDocument()
    expect(screen.getByText(/No EUR reserve policy/)).toBeInTheDocument()
    expect(screen.getByText('incident created')).toBeInTheDocument()
    expect(screen.queryByText(/USD 1,200 available/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', {name:'Approval center'}))
    expect(await screen.findByText('Read-only approval history')).toBeInTheDocument()
    expect(screen.getByText('No persisted approval records')).toBeInTheDocument()
    expect(screen.queryByRole('button', {name:/approve/i})).not.toBeInTheDocument()
  })

  it('shows persisted approval details without exposing action controls', async () => {
    sessionStorage.setItem('tp-token', 'operator-test-token')
    const approval = { id:'approval-12345678', action:'REPLACE_TRANSFER', incident_id:'incident-12345678',
      original_transfer_id:'tr-original-123', operation_id:'operation-1', operation_status:'PREPARED',
      counterparty:'Northwind Parts', amount:'1250.50', currency:'USD', status:'PENDING',
      expires_at:new Date(Date.now()+60_000).toISOString(), requested_by:'operator', approved_by:null,
      approved_at:null, policy_version:4 }
    const fixtures:Record<string, unknown> = {
      '/api/integrations/airwallex/status': { configured:false, status:'disconnected', note:'Airwallex is disconnected.' },
      '/api/incidents':[], '/api/treasury/obligations':[], '/api/treasury/policies':[],
      '/api/audit?limit=20':[], '/api/approvals':[approval],
    }
    vi.stubGlobal('fetch', vi.fn(async (input:RequestInfo | URL) => {
      const url=String(input)
      const key=Object.keys(fixtures).find(path=>url.endsWith(path))
      return new Response(JSON.stringify(key?fixtures[key]:{detail:'Not found'}),
        {status:key?200:404,headers:{'Content-Type':'application/json'}})
    }))
    renderApp()
    fireEvent.click(await screen.findByRole('link',{name:'Approval center'}))
    expect(await screen.findByText('REPLACE TRANSFER')).toBeInTheDocument()
    expect(screen.getByText(/Northwind Parts/)).toBeInTheDocument()
    expect(screen.getByText(/USD 1,250\.5/)).toBeInTheDocument()
    expect(screen.getByText('PENDING')).toBeInTheDocument()
    expect(screen.getByText(/Original tr-original-123/)).toBeInTheDocument()
    expect(screen.getByText('v4')).toBeInTheDocument()
    expect(screen.queryByRole('button',{name:/approve/i})).not.toBeInTheDocument()
  })

  it('filters and sorts the incident queue without changing the saved records', async () => {
    sessionStorage.setItem('tp-token', 'operator-test-token')
    const incident = (id:string,supplier:string,created_at:string) => ({ id,supplier,original_transfer_id:`tr-${id}`,amount:'10.00',currency:'USD',supplier_claim:'Delayed payment',state:'NEW',external_status:null,source:'unverified',simulated:false,created_at })
    const fixtures:Record<string, unknown> = {
      '/api/integrations/airwallex/status': { configured:false, status:'disconnected', note:'Airwallex is disconnected.' },
      '/api/incidents': [
        incident('old','Zen Systems','2026-10-20T10:00:00Z'),
        incident('middle','Acme Supply','2026-10-22T10:00:00Z'),
        incident('new','Beacon Parts','2026-10-24T10:00:00Z'),
      ],
      '/api/treasury/obligations':[], '/api/treasury/policies':[], '/api/audit?limit=20':[], '/api/approvals':[],
    }
    vi.stubGlobal('fetch', vi.fn(async (input:RequestInfo | URL) => {
      const url=String(input)
      const key=Object.keys(fixtures).find(path=>url.endsWith(path))
      return new Response(JSON.stringify(key?fixtures[key]:{detail:'Not found'}),
        {status:key?200:404,headers:{'Content-Type':'application/json'}})
    }))
    renderApp()
    fireEvent.click(await screen.findByRole('link',{name:'Payment incidents'}))
    const suppliers=()=>screen.getAllByRole('row').slice(1).map(row=>row.querySelector('.supplier-cell b')?.textContent)
    expect(await screen.findByText('Beacon Parts')).toBeInTheDocument()
    expect(suppliers()).toEqual(['Beacon Parts','Acme Supply','Zen Systems'])
    fireEvent.change(screen.getByRole('combobox',{name:'Sort incidents'}),{target:{value:'supplier'}})
    expect(suppliers()).toEqual(['Acme Supply','Beacon Parts','Zen Systems'])
    fireEvent.change(screen.getByRole('textbox',{name:'Filter incidents'}),{target:{value:'tr-old'}})
    expect(suppliers()).toEqual(['Zen Systems'])
    expect(screen.getByText('1 of 3 incidents')).toBeInTheDocument()
  })

  it('shows backend-only Airwallex and AI status in settings without exposing credentials', async () => {
    sessionStorage.setItem('tp-token', 'operator-test-token')
    const fixtures:Record<string, unknown> = {
      '/api/integrations/airwallex/status': { configured:true,status:'configured_not_verified',note:'Credentials are configured on the backend; access is not verified.',client_id:'PRIVATE_CLIENT_SENTINEL',api_key:'PRIVATE_API_SENTINEL' },
      '/api/integrations/ai/status': { configured:false,status:'deterministic_fallback',provider:null,note:'AI interpretation is unavailable; deterministic checks remain active.',api_key:'PRIVATE_AI_SENTINEL' },
      '/api/incidents':[], '/api/treasury/obligations':[], '/api/treasury/policies':[],
      '/api/audit?limit=20':[], '/api/approvals':[],
    }
    vi.stubGlobal('fetch', vi.fn(async (input:RequestInfo | URL) => {
      const url=String(input)
      const key=Object.keys(fixtures).find(path=>url.endsWith(path))
      return new Response(JSON.stringify(key?fixtures[key]:{detail:'Not found'}),
        {status:key?200:404,headers:{'Content-Type':'application/json'}})
    }))
    renderApp()
    fireEvent.click(await screen.findByRole('link',{name:'Settings'}))
    expect(await screen.findByRole('heading',{name:'Integration health'})).toBeInTheDocument()
    expect(screen.getAllByText('Airwallex sandbox')).toHaveLength(2)
    expect(await screen.findByText('Configured · not verified')).toBeInTheDocument()
    expect(screen.getByText('AI interpretation')).toBeInTheDocument()
    expect(await screen.findByText('Deterministic fallback')).toBeInTheDocument()
    expect(screen.getByText(/deterministic checks remain active/)).toBeInTheDocument()
    expect(screen.queryByText(/PRIVATE_(CLIENT|API|AI)_SENTINEL/)).not.toBeInTheDocument()
  })
})

afterEach(() => {
  cleanup()
  sessionStorage.clear()
  vi.unstubAllGlobals()
})
