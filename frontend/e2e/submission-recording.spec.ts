import { expect, test } from '@playwright/test'

test.use({ video: 'on' })
test.setTimeout(270_000)

async function caption(page, text: string) {
  await page.evaluate((message) => {
    let banner = document.getElementById('submission-caption')
    if (!banner) {
      banner = document.createElement('div')
      banner.id = 'submission-caption'
      Object.assign(banner.style, {
        position: 'fixed', left: '4%', right: '4%', bottom: '16px', zIndex: '99999',
        padding: '12px 18px', borderRadius: '8px', background: 'rgba(18, 34, 27, .94)',
        color: '#fff', font: '600 17px/1.4 system-ui, sans-serif', textAlign: 'center',
        boxShadow: '0 4px 24px rgba(0,0,0,.2)',
      })
      document.body.appendChild(banner)
    }
    banner.textContent = message
  }, text)
  // Hold each readable caption long enough for the intended 3–5 minute walkthrough.
  await page.waitForTimeout(14_000)
}

test('record a captioned submission walkthrough without external API calls', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/')
  await caption(page, 'TreasuryPilot AI · Payment Ops Incident Commander. Resolve the payment. Protect the cash.')

  await page.getByLabel('Operator token').fill('e2e-local-token')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('heading', { name: 'Good morning, operator' })).toBeVisible()
  await caption(page, 'The dashboard separates verified balances from unavailable bank data. Airwallex sandbox authentication returned 403 permission, so no live balance is shown.')

  await page.getByRole('button', { name: 'New incident' }).click()
  await page.getByLabel('Supplier name').fill('Demo Components Ltd')
  await page.getByLabel('Original transfer ID').fill('transfer-demo-001')
  await page.getByLabel('Amount').fill('4500.00')
  await page.getByLabel("Supplier's message").fill('Our payment has not arrived. Please send another one.')
  await page.getByRole('dialog').getByRole('button', { name: 'Create incident' }).click()
  await expect(page.getByRole('heading', { name: 'Demo Components Ltd' })).toBeVisible()
  await expect(page.getByText('Unverified claim')).toBeVisible()
  await caption(page, 'The supplier says the payment is missing. TreasuryPilot stores that message as an unverified claim, separate from bank-side evidence.')

  await page.getByRole('button', { name: 'Investigate transfer' }).click()
  await expect(page.getByText('Transfer details were not retrieved. No replacement is permitted.').first()).toBeVisible()
  await caption(page, 'The transfer lookup could not return verified details. The incident remains unresolved; the supplier statement cannot establish a bank transfer outcome.')

  await page.getByRole('button', { name: 'Evaluate recovery' }).click()
  await expect(page.getByText('Recovery blocked')).toBeVisible()
  await expect(page.getByText('Original transfer failure has not been verified from Airwallex')).toBeVisible()
  await caption(page, 'Recovery is blocked because failure is unverified. This fail-closed rule helps prevent paying the same supplier twice.')

  await page.getByRole('button', { name: 'Generate AI interpretation' }).click()
  await expect(page.getByText('AI unavailable · deterministic next step')).toBeVisible()
  await caption(page, 'No AI provider is configured. The application gives a deterministic next step and does not let AI change the evidence or authorize a financial action.')

  await page.getByRole('link', { name: 'Treasury planning' }).click()
  await expect(page.getByRole('button', { name: 'Calculate live plan' })).toBeDisabled()
  await page.getByRole('button', { name: 'Show simulated plan' }).click()
  await expect(page.getByText('SIMULATED EXAMPLE · NOT AIRWALLEX DATA')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Critical supplier' })).toBeVisible()
  await expect(page.getByText('Defer', { exact: true })).toBeVisible()
  await caption(page, 'Live planning is unavailable while the sandbox account rejects authentication. This separate fixture is clearly labeled and uses synthetic USD balances, obligations, priorities, and a zero-fee assumption.')

  await expect(page.getByText('USD 0.00 synthetic fixture only; not a live fee quote')).toBeVisible()
  await caption(page, 'The deterministic planner protects the reserve and defers a lower-priority obligation in this example. The synthetic zero fee is explanatory only and cannot authorize a real payment.')

  await page.getByRole('button', { name: 'Run recovery demo' }).click()
  await expect(page.getByRole('heading', { name: 'Payment incident recovery' })).toBeVisible()
  await expect(page.getByText('No real financial operation occurred')).toBeVisible()
  await expect(page.getByText('BLOCK_REPLACEMENT')).toBeVisible()
  await expect(page.getByText('SIMULATED_APPROVED')).toBeVisible()
  await expect(page.getByText('SIMULATED_CREATED')).toBeVisible()
  await expect(page.getByText('SIMULATED_VERIFIED')).toBeVisible()
  await expect(page.getByText(/No Airwallex API was called/)).toBeVisible()
  await caption(page, 'This recovery scenario is a stateless simulation. Its failure transition, approval binding, creation, and verification are fixture stages; no transfer was created and no Airwallex API was called.')

  await page.getByRole('button', { name: 'Record obligation' }).click()
  await page.getByRole('dialog').getByLabel('Supplier name').fill('User Entered Supplier')
  await page.getByRole('dialog').getByLabel('Amount').fill('1250.50')
  await page.getByRole('dialog').getByLabel('Priority').selectOption('HIGH')
  await page.getByRole('dialog').getByLabel('Due date').fill('2026-11-05')
  await page.getByRole('dialog').getByRole('button', { name: 'Record obligation' }).click()
  await expect(page.getByRole('cell', { name: 'User Entered Supplier' })).toBeVisible()
  await expect(page.getByText('operator entered')).toBeVisible()
  await expect(page.getByText('Not verified against Airwallex')).toBeVisible()
  await caption(page, 'Operators can record obligations for planning. Provenance stays visible: this supplier and amount were operator-entered and are not verified against Airwallex.')

  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByLabel('Reserve floor').fill('3000.00')
  await page.getByLabel('Approval threshold').fill('1000.00')
  await page.getByLabel('Priority order').fill('CRITICAL,HIGH,NORMAL,LOW')
  await page.getByRole('button', { name: 'Save policy' }).click()
  await expect(page.getByRole('status')).toContainText('USD reserve policy saved and audited.')
  await expect(page.getByText(/^USD · version \d+$/)).toBeVisible()
  await caption(page, 'The authorized operator can update a reserve policy. The saved version is visible and the change is written to the audit trail.')

  await page.getByRole('link', { name: 'Audit trail' }).click()
  await expect(page.getByText('treasury policy updated').first()).toBeVisible()
  await expect(page.getByText('incident recommendation generated').first()).toBeVisible()
  await expect(page.getByText(/Policy v\d+/).first()).toBeVisible()
  await caption(page, 'The audit view ties the incident recommendation and policy change to their actor, time, and policy version. It contains no authentication tokens.')

  await page.getByRole('link', { name: 'Approval center' }).click()
  await expect(page.getByRole('heading', { name: 'Approval center' })).toBeVisible()
  await expect(page.getByText('Read-only approval history')).toBeVisible()
  await expect(page.getByText('No persisted approval records')).toBeVisible()
  await expect(page.getByText(/does not create approval requests, approve actions, or execute transfers/)).toBeVisible()
  await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)
  await caption(page, 'The approval center is read-only. It shows saved action-bound records but has no request, approval, or transfer execution controls.')

  await caption(page, 'Demo summary: verified evidence and deterministic policy checks keep uncertainty from triggering a replacement. Synthetic stages are labeled. Live Airwallex recovery and post-write verification remain blocked and unimplemented.')
})
