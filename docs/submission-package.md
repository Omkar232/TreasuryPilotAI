# Hackathon submission package

**Status: draft prepared; not submitted.** This document provides truthful copy and a remaining-deliverables checklist for the Airwallex Agentic Banking Hackathon.

## Submission details

- **Project:** TreasuryPilot AI
- **Tagline:** Resolve the payment. Protect the cash.
- **Primary track:** Payment Ops Incident Commander (Starter Kit 03)
- **Secondary track:** Adaptive Treasury Controller (Starter Kit 01)
- **Built with:** Python, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, HTTPX, React, TypeScript, Vite, TanStack Query, Docker, and Playwright.
- **Repository:** Not published. The local source is committed and ready to push over the authenticated GitHub SSH identity; creating a new public repository still requires GitHub API authorization, which is not configured in this workspace.
- **Source archive:** `submission/TreasuryPilotAI-source.zip` is prepared for upload; it excludes local credentials, databases, dependency folders, build output, and the separate video.
- **Demo video:** `submission/TreasuryPilotAI-demo.webm` is a captioned 3-minute-32-second local walkthrough recorded with Playwright. It uses no Airwallex credentials or external API calls. It is not yet published.
- **Hosted demo:** Not deployed. Run the local demo at `http://localhost:5173` after starting Docker Compose, or follow the [demo script](demo-script.md) for local setup.
- **Entrant:** Omkar Mali (solo participant; entrant reports registration is complete).

## Project description

TreasuryPilot AI helps finance operators investigate supplier payments that appear delayed, avoid duplicate payouts, and understand the effect of obligations on available cash. It records the supplier's statement separately from Airwallex evidence, retrieves transfer and balance data through a backend-only sandbox adapter, and maps provider states into a fail-closed incident model. Uncertain, stale, or incomplete evidence blocks recovery.

Its deterministic treasury planner uses decimal arithmetic, keeps currencies separate, orders obligations by a versioned policy, enforces reserve floors, and holds actions when a balance, source account, or per-obligation fee is unknown. AI interpretation is optional and cannot change transfer evidence, calculate payment eligibility, or authorize an operation. The application records incident and policy changes for audit.

The local walkthrough demonstrates an unresolved transfer being blocked and provides a separate synthetic recovery scenario for the reserve, duplicate, approval-binding, and verification concepts. Synthetic states and amounts are labeled and never presented as bank data.

**Current limitation:** A fresh Airwallex sandbox login check on October 25 returned HTTP 403 (`unauthorized`), so the balance request was not attempted and no live read or write has been verified. Transfer creation and user-facing approvals are not exposed. Live plans hold actions while fee impact is unknown. The demo therefore shows the safe blocked workflow and a clearly labeled simulation, not a completed Airwallex payment.

## Setup and demo

- Setup and test commands: [README](../README.md)
- Reproducible 3–5 minute local walkthrough and backup scenario: [demo script](demo-script.md)
- Integration evidence and current API behavior: [official API research](official-api-research.md)
- Verified test runs and environment status: [testing record](testing.md)
- Deployment configuration and remaining hosted steps: [deployment notes](deployment.md)
- Requirement-by-requirement gate status: [acceptance status](acceptance-status.md)

The local Compose stack is a working demo environment; it is not a publicly reachable demo URL. The synthetic walkthrough makes no Airwallex calls. The normal local dashboard may attempt read-only sandbox calls when credentials are configured, so keep the Airwallex 403 limitation visible in the recording.

## Final submission checklist

The official organizer page asks for a working demo, a video walkthrough under five minutes, and a repository link with setup instructions. As checked October 25, 2026, the HackerEarth listing gives the build phase as October 27–November 13, registration close as **October 25, 2026 at 18:29 UTC**, and submission deadline as **November 13, 2026 at 18:29 UTC**. Registration closes tonight in India; confirm the entrant is registered in HackerEarth. Recheck the live pages before submitting because dates and form fields may change.

- [x] Project title, tracks, truthful description, technology list, setup instructions, and backup demo script prepared.
- [x] Source archive prepared without local secrets or generated state.
- [x] Captioned walkthrough recorded in the local `submission/` directory; publish it to a video host and add the public URL to this package and the README.
- [x] Local backend/frontend workflow and synthetic fallback verified; see [acceptance status](acceptance-status.md).
- [x] Entrant reports that Omkar Mali is registered as a solo participant.
- [x] Entrant reports organizer confirmation that pre-build-phase work is eligible. The current listing says the build phase starts October 27, 2026, while the user-provided specification says October 25; retain the organizer confirmation for the submission in case the form asks.
- [ ] Create a public repository and add its URL to this document and the README.
- [x] Record a walkthrough under five minutes. [ ] Publish it and include its URL in this document and the README.
- [ ] Decide whether to provision a hosted demo; if so, deploy only after reviewing the Render plans, production secrets, and access controls, then verify its public health check.
- [ ] Recheck the official submission form and deadline, then submit through HackerEarth.

## Official event references

- [Organizer landing page](https://airwallex.hackerearth.com/)
- [HackerEarth event listing](https://www.hackerearth.com/community/challenges/hackathon/agentic-banking-hackathon/?register=true)
- [Airwallex developer guide](https://airwallexdev.com/guide)

Event details last checked October 25, 2026. The organizer landing page lists applications closing October 25 and the build period beginning October 27; the entrant reports the organizer confirmed pre-build work is eligible. The public pages reviewed did not expose a complete, separate rules document; verify eligibility, submission fields, and account-specific requirements in the registered HackerEarth workspace. The [organizer timeline and FAQ](https://airwallex.hackerearth.com/) state solo entries are allowed and require a working demo, under-five-minute video, and repository link with setup instructions.
