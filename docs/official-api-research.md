# Official Airwallex API research

API research and a credential-only sandbox login check were repeated on **2026-10-25** against the linked official references. API behavior and hackathon rules can change; re-check before final submission. Backend-only sandbox credentials are present in the ignored local `.env` and Compose backend, but login again returned HTTP 403 (provider code `unauthorized`). The balance request was not attempted because authentication failed. No successful read or write was verified. Playwright explicitly clears Airwallex variables to prevent external calls.

## Hackathon

- Official landing page: <https://airwallex.hackerearth.com/>; event listing: <https://www.hackerearth.com/community/challenges/hackathon/agentic-banking-hackathon/?register=true>
- The [organizer landing page](https://airwallex.hackerearth.com/) lists the event as **October 25–November 19, 2026**, applications closing October 25, and the official build period beginning October 27. The [HackerEarth listing](https://www.hackerearth.com/community/challenges/hackathon/agentic-banking-hackathon/?register=true) confirms the build phase as October 27–November 13, registration close as October 25 at 18:29 UTC, and project submission deadline as November 13 at 18:29 UTC (11:59 PM IST). The user-provided project specification listed a different build start (October 25); this discrepancy is recorded, and the entrant reports the organizer confirmed that pre-build work is eligible. The organizer FAQ allows solo entries and requests a working demo, a video walkthrough under five minutes, and a repository link with setup instructions. The [builder guide](https://airwallexdev.com/guide) recommends synthetic data for kits 1–8 and says the demo should show a financial action or escalation; synthetic amounts must be presented as synthetic. The public pages reviewed did not expose additional detailed rules.

## Sandbox authentication

- Docs: <https://www.airwallex.com/docs/api/authentication/api_access_token>, <https://www.airwallex.com/docs/developer-tools/sandbox-environment>
- `POST https://api.sandbox.airwallex.com/api/v1/authentication/login`
- Headers: `x-client-id`, `x-api-key`; no request body required. Docs example returns HTTP 201 with `token` and `expires_at`. Use `Authorization: Bearer <token>` for subsequent requests. Token lifetime is 30 minutes; honor exact `expires_at`.
- For scoped API keys linked to multiple accounts, Airwallex documents optional `x-login-as` to select an account; without it, account-level permissions may be omitted or unavailable. TreasuryPilot now accepts this as backend-only `AIRWALLEX_LOGIN_AS`.
- Permissions: API key scope/account configuration must permit each product operation. A fresh direct authentication check on October 25 returned HTTP 403 with provider code `unauthorized`, so the access token could not be obtained. The account/API key permissions or sandbox credential pairing need correction. No balance, transfer, or beneficiary API call succeeded.
- Airwallex's [current builder guide](https://airwallexdev.com/guide) troubleshooting checklist for setup failures says to check whether the business is marked “In review,” whether the API key has the required scopes, whether sandbox developer login completed, and whether a funded Global Account exists. These are account-side checks; TreasuryPilot has not changed them.
- The adapter validates that `expires_at` is timezone-aware and still in the future before using the token; it does not cache tokens. These checks have HTTPX mock tests only.
- Sandbox base URL confirmed. Production base URL is separate; this project defaults to sandbox.

## Current balance retrieval

- Docs: <https://www.airwallex.com/docs/api/core_resources/balances/current>
- `GET /api/v1/balances/current`
- Auth: bearer token. Optional `account_type` query (`cash`, `yield`, `credit`); optional `x-on-behalf-of` for connected subaccounts.
- Response is an array of balance objects with `account_type`, `available_amount`, `currency`, `pending_amount`, `prepayment_amount`, `reserved_amount`, `total_amount`.
- Treat `available_amount` as the spendable figure; do not add pending/reserved balances. Errors documented include 401, 403, 429, 500. Sandbox support is indicated by the docs' sandbox URL.
- Actual call: **blocked before balance retrieval** because sandbox authentication returned HTTP 403 (`permission`). No balance data was returned.

## Transfer listing

- Docs: <https://www.airwallex.com/docs/api/payouts/transfers>
- `GET /api/v1/transfers`
- Auth: bearer token; `x-on-behalf-of` applies to connected accounts.
- Query supports `request_id`, `page_size`, `page_after`, and `page_before`, as well as date, status, currency and reference filters. The current API response exposes cursor pagination fields; older API documentation describes a `page` parameter and a 30-day default. TreasuryPilot uses the documented `request_id` filter and does not depend on a complete historical list scan.
- Response includes `items` and transfer records with identifiers, status, amounts/currencies, timestamps, beneficiary references and failure information where present. Preserve raw response fields needed for audit.
- Listing is a reconciliation aid but the docs do not establish it as an atomic idempotency guarantee. Reconcile before another write after a timeout.
- Actual call: **not tested**.

## Transfer retrieval

- Docs: <https://www.airwallex.com/docs/api/payouts/transfers>
- `GET /api/v1/transfers/{id}`; path `id` is the Airwallex transfer ID. Auth: bearer token; connected accounts require `x-on-behalf-of`.
- Response includes `status`, `transfer_amount`, `transfer_currency`, `amount_payer_pays`, fee fields, `created_at`, beneficiary fields/ID, and failure details as applicable. Persist actual status and decision-relevant fields; the adapter drops free-text failure messages and retains only validated structured failure/reason codes to avoid storing unbounded sensitive text.
- Common documented errors include invalid/missing transfer and auth/rate/service errors; exact responses vary by operation.
- Actual call: **not tested**.

## Transfer creation

- Docs: <https://www.airwallex.com/docs/api/payouts/transfers>
- `POST /api/v1/transfers/create`; authenticated JSON request.
- Required documented fields include `reason`, `reference`, `request_id`, `transfer_currency`, and corridor-specific beneficiary or `beneficiary_id`; `transfer_amount`, `transfer_method`, source and payer details are required depending on route/schema. The endpoint has dynamic corridor-specific schema; do not construct a generic beneficiary payload without schema validation.
- Response is a transfer object. Validate response structure and persist the external transfer ID/status; HTTP success alone is not proof of intended settlement.
- `request_id` must be unique across the prior 7 days. Docs specifically say it can be used to look up an uncertain payout via list endpoint. This is useful reconciliation support but does not replace the local durable operation lock and human escalation when association is ambiguous.
- Fees are calculated by Airwallex and returned (`fee_currency`, `fee_amount`, `amount_payer_pays`, `amount_beneficiary_receives`). Unknown fee or FX impact blocks recovery in TreasuryPilot.
- The current builder guide's Kit 3 scenario says to budget for both SWIFT fees when the original and replacement are SWIFT transfers. The same guide gives a fixed EUR 12.85 SWIFT fee and no LOCAL fee in its Kit 1 scenario. These are guide-level scenario assumptions, not verified account quotes; this implementation continues to use live transfer fee evidence when available and blocks on unknown fees.
- The backend client now has a single-attempt create method that requires a `request_id`, validates the returned transfer identity/status, and classifies timeouts, 408/409/429, server errors, or unreadable success responses as `outcome_unknown`. It does not retry. There is no application route calling this method: durable operation creation, duplicate checks, exact-action approval, policy evaluation, and post-create verification must be completed first.
- Actual call: **not tested**. Write operation remains unreachable from the application until sandbox credentials and beneficiary corridor/schema are verified.

## Transfer validation and fee certainty

- Docs: <https://www.airwallex.com/docs/payouts/transfers/validate-a-transfer> and <https://www.airwallex.com/docs/api/payouts/transfers>
- `POST /api/v1/transfers/validate` checks a candidate request's schema and returns `200 OK` when no validation errors exist; invalid fields return an error structure. The documented response does not provide the transfer fee or `amount_payer_pays`.
- Airwallex documents `fee_amount`, `fee_currency`, and `amount_payer_pays` on a created transfer response. Therefore the direct transfer validation endpoint cannot establish the exact fee before transfer creation.
- The adapter exposes one non-mutating validation request for future workflow use. It has not been tested against a real account.

### Batch quote alternative

- The official [Batch Transfers API](https://www.airwallex.com/docs/api/payouts/batch_transfers) documents a separate pre-submit quote route: create a batch, add its transfer items, then call `POST /api/v1/batch_transfers/{id}/quote` while the batch is in `DRAFTING` or `IN_APPROVAL`. The returned batch quote summary can include `fee_amount`, `fee_currency`, `amount_payer_pays`, `amount_beneficiary_receives`, source/transfer currencies, and quote expiry. For a pure transfer, `client_rate` and `currency_pair` are null.
- This makes fee certainty before submission a documented possibility through the batch workflow. It does not mean the direct transfer validation endpoint returns a quote, and it does not establish that this account can create or quote a batch. Batch creation and adding items are external writes that create/modify a draft resource; a quote may expire, and the resulting fee, currency pair, amounts, validity, and expiry must be checked against the exact approved action before any submission.
- The batch item schema requires a request ID, currencies, transfer method, reason, reference, amount, and either a beneficiary object or existing `beneficiary_id`. Its payer schema accepts a `payer_id` instead of a payer object; the documented payer object includes required entity type and address fields. TreasuryPilot has neither payer-contact lookup nor a configured payer ID, and the available sandbox account has not verified which payer form or fields its corridor requires.
- One-item batch behavior, payer configuration, beneficiary corridor/schema compatibility, account permissions, cleanup/deletion behavior, and the required funding source are **unverified** for this account. The current app has no batch adapter or route. The direct transfer route remains absent, and unknown fees continue to block the current live recovery workflow until a tested quote path is implemented and bound to the durable operation, fresh balance/reserve check, and approval.
- Actual call: **not tested**. No batch was created, quoted, submitted, or deleted.

## Transfer status model and sandbox simulation

- Status docs: <https://www.airwallex.com/docs/payouts/transfers/create-a-transfer/transfer-statuses>, <https://www.airwallex.com/docs/developer-tools/sandbox-environment/payouts/simulate-transfer-status-transition>, <https://www.airwallex.com/docs/api/payouts/transfers>
- Sandbox-only `POST /api/v1/simulation/transfers/{TRANSFER_ID}/transition`, bearer token, JSON body includes `next_status`; `failure_type` is required for `FAILED` scenarios. Documented targets include `PROCESSING`, `FAILED`, `SENT`, `PAID`, `CANCELLED`; only valid next transitions are accepted. `PAID` simulation requires API version 2024-04-30 or later.
- Current transfer statuses include `IN_APPROVAL`, `APPROVAL_RECALLED`, `APPROVAL_REJECTED`, `APPROVAL_BLOCKED`, `SCHEDULED`, `OVERDUE`, `PROCESSING`, `SENT`, `PAID`, `FAILED`, and `CANCELLED`. `SENT` means sent from Airwallex, not recipient receipt. Even `PAID` can later become `FAILED`; continue monitoring. Failed transfers auto-cancel in seconds, so a read may encounter `CANCELLED`; only map that to failure when failure details are present. A cancellation alone is not failure evidence. TreasuryPilot maps `OVERDUE` and `IN_APPROVAL` to nonterminal processing; other unrecognized statuses fail closed to unknown.
- Simulation operation is not enabled in production and has not been called against this account.

## Beneficiaries

- Docs: <https://www.airwallex.com/docs/api/payouts/beneficiaries/create>
- Retrieval: `GET /api/v1/beneficiaries/{id}`; listing: `GET /api/v1/beneficiaries` with `page_num`/`page_size` and filters. Auth: bearer token. The developer guide confirms the ID path and that the response has the beneficiary details shape returned by beneficiary creation: <https://www.airwallex.com/docs/payouts/beneficiaries/retrieve-beneficiaries>.
- Creation: `POST /api/v1/beneficiaries/create`; body requires `beneficiary` object and `transfer_methods` array (`LOCAL` or `SWIFT`). Specific beneficiary fields depend on corridor, entity type, account type, currency and payment method. This page does not show `request_id` as a required field for beneficiary creation.
- Dynamic schema: `POST /api/v1/beneficiary_api_schemas/generate` with country, currency, transfer method, entity type and applicable clearing system. Response describes required field paths and validation rules. The separate `POST /api/v1/beneficiary_form_schemas/generate` provides UI-oriented schema. `POST /api/v1/beneficiaries/validate` validates a proposed beneficiary. These operations must precede a corridor-specific create flow.
- Implemented read-only adapter: retrieves a beneficiary ID from the most recent verified transfer evidence, calls the ID endpoint, confirms the returned ID, and persists an audited summary limited to nickname, entity/payment types, bank country, account currency, and transfer methods. Account holder, account/routing identifiers, addresses, and other beneficiary PII are omitted. The dashboard labels the returned information as retrieved data; retrieval does not prove ownership or transfer eligibility.
- Actual call: **not tested**. HTTPX tests cover path encoding, ID matching, evidence provenance, audit logging, and PII omission. Beneficiary creation is not implemented.
- TreasuryPilot's initial slice does not create beneficiaries. Beneficiary creation is **not implemented** and must require an explicitly authorized, separately reviewed workflow.

## Deposit simulation

- Official developer guide: <https://airwallexdev.com/guide>; official sandbox docs: <https://www.airwallex.com/docs/developer-tools/sandbox-environment>
- `POST /api/v1/simulation/deposit/create` is described in the developer guide for sandbox funding. Example fields include `global_account_id`, `amount`, optional `payer_name`; guide notes response can say `PENDING` while balance posts immediately, so balances must be re-read.
- Exact API reference schema and account prerequisites have not been verified for this account. Not included in the MVP; no deposit call made.

## FX

- Not included in the MVP. No FX rate, quote, conversion or fee assumptions are made. Multi-currency balances are kept separate.

## Request identifiers / idempotency

- Transfer creation docs: `request_id` is unique for seven days and duplicate requests are rejected. Docs state the request ID can help retrieve a payout when create outcome is uncertain, using `GET /api/v1/transfers?request_id=...`.
- This is endpoint-level duplicate handling, not a universal guarantee for every API operation. TreasuryPilot will also keep a durable local operation record and block a new financial write while its outcome is unknown.

## Tested against actual account

**None.** Backend-only sandbox credentials are configured in the local Compose backend, but the October 25 login attempt returned HTTP 403 (`unauthorized`), so no authenticated API operation succeeded. PostgreSQL 16 is running locally and has been verified through the readiness endpoint. The app can run with explicitly synthetic demo fixtures; demo values are not Airwallex data and cannot validate API behavior.
