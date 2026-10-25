# Demo script (3–5 minutes)

1. Point out that no live Airwallex balance is available; the integration is configured but unverified because sandbox authentication returned HTTP 403 (`permission`).
2. Open a supplier incident and distinguish the operator's claim from retrieved Airwallex evidence.
3. Evaluate recovery without verified transfer evidence: the system blocks replacement.
4. Open Treasury Planning and select **Show simulated plan**. Point out the explicit synthetic-data label, USD 12,000 example balance, USD 3,000 reserve floor, synthetic USD 0 fee assumption, priority ordering, and deferral of the lower-priority USD 2,500 obligation. Nothing is written to Airwallex.
5. Record an obligation from the operator interface. Show its `operator_entered` provenance and the note that it is not verified against Airwallex. The record is persisted and audited; it does not start a transfer.
6. Save a currency reserve policy. Show the policy version and audit event. Sandbox credentials are configured, but authentication currently returns HTTP 403 (`permission`); the live-plan request therefore fails closed and must not be presented as live balance data.
7. Select **Run recovery demo**. Walk through the synthetic original in `PROCESSING` and the blocked replacement, the clearly marked fixture failure transition, the USD reserve calculation and deferred low-priority obligation, approval bound to the amount/counterparty/original/evidence/policy hash, and the simulated creation and verification stages. Point out that this endpoint is stateless, does not create an incident or audit record, and calls no Airwallex API.
8. Close by stating that the simulated zero fee and all transfer states are fixture values. The live recovery still requires verified sandbox access, fee certainty or a documented conservative fee policy, a real approval workflow, and post-write retrieval.

This is a backup synthetic demo, not a sandbox transaction. It does not create, transition, or verify an Airwallex transfer and must never be presented as bank-side evidence.
