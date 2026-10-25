from uuid import uuid4
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.security import require_operator, require_policy_admin
from app.db.database import get_db
from app.models import AuditEvent, IncidentEvidence, Obligation, PaymentIncident, TreasuryPlan, TreasuryPolicy
from app.schemas import (EvaluationView, IncidentCreate, IncidentDetailView, IncidentView,
    ObligationCreate, ObligationView, TreasuryPlanView, TreasuryPolicyUpdate, TreasuryPolicyView)
from app.services.airwallex import AirwallexClient, AirwallexError
from app.services.airwallex.client import balance_snapshot, safe_beneficiary_summary, safe_transfer_summary
from app.services.ai.advisor import AIUnavailable, Advisor
from app.services.incidents.states import IncidentState, normalize_transfer_evidence, transition
from app.services.incidents.demo_scenario import run_demo_recovery_scenario
from app.services.treasury.engine import ObligationInput, plan_obligations

router = APIRouter(prefix="/api", dependencies=[Depends(require_operator)])

@router.get("/integrations/airwallex/status")
def integration_status():
    credentials_present = bool(settings.airwallex_client_id and settings.airwallex_api_key)
    if credentials_present and not settings.airwallex_sandbox_url_valid:
        status, note = "invalid_configuration", "Only the documented Airwallex sandbox host is allowed."
    elif settings.airwallex_configured:
        status, note = "configured_not_verified", "Credentials are backend-only; connection has not been verified."
    else:
        status, note = "disconnected", "Airwallex sandbox credentials are not configured."
    return {"environment": "sandbox", "configured": settings.airwallex_configured,
        "status": status,
        "last_successful_balance_refresh": None,
        "note": note}

@router.get("/integrations/ai/status")
def ai_integration_status():
    if settings.ai_configured:
        return {"provider": "openai", "configured": True,
            "status": "configured_not_verified", "note": "AI access is backend-only and is not used for financial authorization."}
    return {"provider": settings.ai_provider.lower() or None, "configured": False,
        "status": "deterministic_fallback", "note": "AI interpretation is unavailable; verified incident facts and policy checks remain available."}

@router.get("/treasury/balances")
def unavailable_balances():
    if not settings.airwallex_configured:
        raise HTTPException(status_code=503, detail="Live balances unavailable: Airwallex sandbox credentials are not configured")
    try:
        external = AirwallexClient().get_balances()
    except AirwallexError as exc:
        raise HTTPException(status_code=502, detail=f"Airwallex {exc.category}: {exc}") from exc
    captured_at, balances = balance_snapshot(external)
    return {"source": "airwallex", "captured_at": captured_at, "balances": balances}

@router.get("/treasury/obligations", response_model=list[ObligationView])
def list_obligations(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "limit must be 1–200 and offset must be nonnegative")
    return list(db.scalars(select(Obligation).where(Obligation.status == "OPEN")
        .order_by(Obligation.due_at.is_(None), Obligation.due_at, Obligation.id)
        .offset(offset).limit(limit)))

@router.post("/treasury/obligations", response_model=ObligationView, status_code=201)
def create_obligation(body: ObligationCreate, db: Session = Depends(get_db),
    actor: str = Depends(require_operator)):
    obligation = Obligation(**body.model_dump(), source="operator_entered", simulated=False)
    db.add(obligation)
    db.flush()
    db.add(AuditEvent(actor=actor, event_type="OBLIGATION_RECORDED",
        details={"obligation_id": obligation.id, "source": "operator_entered",
            "currency": obligation.currency, "amount": str(obligation.amount), "priority": obligation.priority}))
    db.commit()
    db.refresh(obligation)
    return obligation

@router.get("/treasury/policies", response_model=list[TreasuryPolicyView])
def list_treasury_policies(db: Session = Depends(get_db)):
    return list(db.scalars(select(TreasuryPolicy).order_by(TreasuryPolicy.currency)))

@router.put("/treasury/policies/{currency}", response_model=TreasuryPolicyView)
def update_treasury_policy(body: TreasuryPolicyUpdate,
    currency: str = Path(pattern=r"^[A-Z]{3}$"), db: Session = Depends(get_db),
    actor: str = Depends(require_policy_admin)):
    policy = db.scalar(select(TreasuryPolicy).where(TreasuryPolicy.currency == currency).with_for_update())
    previous = None if policy is None else {"reserve_floor": str(policy.reserve_floor),
        "approval_threshold": str(policy.approval_threshold), "priority_order": policy.priority_order,
        "version": policy.version}
    if policy is None:
        policy = TreasuryPolicy(currency=currency, reserve_floor=body.reserve_floor,
            approval_threshold=body.approval_threshold, priority_order=body.priority_order, version=1)
        db.add(policy)
    else:
        policy.reserve_floor = body.reserve_floor
        policy.approval_threshold = body.approval_threshold
        policy.priority_order = body.priority_order
        policy.version += 1
    db.add(AuditEvent(actor=actor, event_type="TREASURY_POLICY_UPDATED",
        policy_version=policy.version, details={"currency": currency, "previous": previous,
            "new": {"reserve_floor": str(policy.reserve_floor),
                "approval_threshold": str(policy.approval_threshold),
                "priority_order": policy.priority_order, "version": policy.version}}))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Treasury policy changed concurrently; reload and retry") from exc
    db.refresh(policy)
    return policy

@router.post("/treasury/plans", response_model=TreasuryPlanView)
def calculate_treasury_plan(db: Session = Depends(get_db), actor: str = Depends(require_operator)):
    if not settings.airwallex_configured:
        raise HTTPException(503, "A live treasury plan requires configured Airwallex sandbox credentials")
    try:
        external = AirwallexClient().get_balances()
    except AirwallexError as exc:
        raise HTTPException(502, f"Airwallex {exc.category}: {exc}") from exc
    captured_at, balance_items = balance_snapshot(external)
    obligations = list(db.scalars(select(Obligation).where(Obligation.status == "OPEN").order_by(Obligation.id)))
    policies = {policy.currency: policy for policy in db.scalars(select(TreasuryPolicy))}
    balances_by_currency: dict[str, list[dict]] = {}
    for item in balance_items:
        balances_by_currency.setdefault(item["currency"], []).append(item)
    obligations_by_currency: dict[str, list[Obligation]] = {}
    for obligation in obligations:
        obligations_by_currency.setdefault(obligation.currency, []).append(obligation)

    currency_plans = []
    for currency in sorted(obligations_by_currency):
        rows = obligations_by_currency[currency]
        accounts = balances_by_currency.get(currency, [])
        policy = policies.get(currency)
        reasons: list[str] = []
        entries = []
        available = Decimal(accounts[0]["available_amount"]) if len(accounts) == 1 else None
        if not accounts:
            reasons.append("No live balance is available for this obligation currency")
        elif len(accounts) > 1:
            reasons.append("Multiple Airwallex balance accounts share this currency; source account selection is unresolved")
        if policy is None:
            reasons.append("No treasury reserve policy is configured for this currency")
        if accounts and len(accounts) == 1 and policy is not None:
            inputs = [ObligationInput(supplier=row.supplier, amount=row.amount, currency=row.currency,
                priority=row.priority, due_at=row.due_at) for row in rows]
            try:
                plan = plan_obligations(available=available, currency=currency,
                    reserve_floor=policy.reserve_floor, obligations=inputs,
                    data_as_of=captured_at, now=captured_at,
                    priority_order=tuple(policy.priority_order))
            except ValueError as exc:
                reasons.append(str(exc))
            else:
                entries = [{"obligation_id": row.id, "supplier": row.supplier,
                    "amount": row.amount, "currency": row.currency, "priority": row.priority,
                    "source": row.source, "action": "PLAN_TO_PAY" if entry.decision.eligible else "HOLD",
                    "approval_required": row.amount >= policy.approval_threshold,
                    "projected_balance_after_payment": entry.decision.projected_balance,
                    "reasons": list(entry.decision.reasons)} for row, entry in zip(rows, plan.entries)]
        status = "AMBIGUOUS_ACCOUNT" if len(accounts) > 1 else "NO_BALANCE" if not accounts else "BLOCKED" if reasons or any(e["action"] == "HOLD" for e in entries) else "PLANNED"
        if status == "BLOCKED" and not reasons and entries:
            reasons.append("One or more obligations failed deterministic policy checks")
        currency_plans.append({"currency": currency, "status": status,
            "available_amount": available,
            "reserve_floor": policy.reserve_floor if policy else None,
            "approval_threshold": policy.approval_threshold if policy else None,
            "policy_version": policy.version if policy else None,
            "reasons": reasons, "entries": entries})

    overall_status = "NO_OPEN_OBLIGATIONS" if not obligations else "BLOCKED" if any(item["status"] != "PLANNED" for item in currency_plans) else "PLANNED"
    plan_id = str(uuid4())
    serialized_currency_plans = [{**item,
        "available_amount": str(item["available_amount"]) if item["available_amount"] is not None else None,
        "reserve_floor": str(item["reserve_floor"]) if item["reserve_floor"] is not None else None,
        "approval_threshold": str(item["approval_threshold"]) if item["approval_threshold"] is not None else None,
        "entries": [{**entry, "amount": str(entry["amount"]),
            "projected_balance_after_payment": str(entry["projected_balance_after_payment"])
                if entry["projected_balance_after_payment"] is not None else None}
            for entry in item["entries"]]} for item in currency_plans]
    result = {"currency_plans": currency_plans,
        "assumptions": ["Balances are a fresh Airwallex read and are kept separate by currency.",
            "Only one balance account per currency can be planned until source-account selection is configured.",
            "Obligations are operator-entered and not verified against an invoice or payables system.",
            "An account-verified, action-bound, unexpired per-obligation fee quote is not available; affected actions remain on hold.",
            "This calculation never authorizes or initiates a transfer."]}
    result["currency_plans"] = serialized_currency_plans
    record = TreasuryPlan(id=plan_id, source="airwallex", status=overall_status,
        data_as_of=captured_at, balance_snapshot=balance_items, result=result)
    db.add(record)
    db.add(AuditEvent(actor=actor, event_type="TREASURY_PLAN_CALCULATED",
        new_state=overall_status, details={"plan_id": plan_id, "source": "airwallex",
            "currencies": [item["currency"] for item in currency_plans]}))
    db.commit()
    db.refresh(record)
    return {"id": record.id, "source": record.source, "status": record.status,
        "data_as_of": record.data_as_of, "balance_snapshot": record.balance_snapshot,
        "currency_plans": record.result["currency_plans"],
        "assumptions": record.result["assumptions"], "created_at": record.created_at}

@router.get("/treasury/plans/{plan_id}", response_model=TreasuryPlanView)
def get_treasury_plan(plan_id: str, db: Session = Depends(get_db)):
    record = db.get(TreasuryPlan, plan_id)
    if record is None:
        raise HTTPException(404, "Treasury plan not found")
    return {"id": record.id, "source": record.source, "status": record.status,
        "data_as_of": record.data_as_of, "balance_snapshot": record.balance_snapshot,
        "currency_plans": record.result["currency_plans"],
        "assumptions": record.result["assumptions"], "created_at": record.created_at}

@router.get("/demo/treasury-plan")
def simulated_treasury_plan():
    """Return a deterministic, synthetic plan. This never reads or writes Airwallex."""
    captured_at = datetime.now(timezone.utc)
    # These zero fees belong only to the clearly labeled synthetic fixture.
    obligations = [
        ObligationInput("Critical supplier", Decimal("4500.00"), "USD", "CRITICAL", fee=Decimal("0.00")),
        ObligationInput("Shipping partner", Decimal("3000.00"), "USD", "HIGH", fee=Decimal("0.00")),
        ObligationInput("Lower-priority supplier", Decimal("2500.00"), "USD", "LOW", fee=Decimal("0.00")),
    ]
    plan = plan_obligations(available=Decimal("12000.00"), currency="USD",
        reserve_floor=Decimal("3000.00"), obligations=obligations,
        data_as_of=captured_at, now=captured_at)
    return {"simulated": True, "source": "synthetic_demo_fixture", "captured_at": captured_at,
        "currency": plan.currency, "available": str(plan.available),
        "reserve_floor": str(plan.reserve_floor), "remaining_balance": str(plan.remaining_balance),
        "fee_assumption": "USD 0.00 synthetic fixture only; not a live fee quote",
        "entries": [{"supplier": entry.obligation.supplier,
            "amount": str(entry.obligation.amount), "currency": entry.obligation.currency,
            "priority": entry.obligation.priority, "action": entry.action,
            "balance_after_plan": str(entry.balance_after_plan),
            "reasons": list(entry.decision.reasons)} for entry in plan.entries],
        "assumptions": list(plan.assumptions) + [
            "All amounts and obligations are synthetic demo values, not Airwallex account data.",
            "The fee value is synthetic and must not be used to authorize a real transfer."]}

@router.get("/demo/recovery-scenario")
def simulated_recovery_scenario():
    """Return a complete, stateless synthetic walkthrough; never call Airwallex."""
    return run_demo_recovery_scenario()

@router.get("/incidents", response_model=list[IncidentView])
def list_incidents(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    if not 1 <= limit <= 100 or offset < 0: raise HTTPException(422, "limit must be 1–100 and offset must be nonnegative")
    return list(db.scalars(select(PaymentIncident).order_by(PaymentIncident.created_at.desc()).offset(offset).limit(limit)))

@router.post("/incidents", response_model=IncidentView, status_code=201)
def create_incident(body: IncidentCreate, db: Session = Depends(get_db),
    actor: str = Depends(require_operator)):
    incident = PaymentIncident(id=str(uuid4()), **body.model_dump(), state=IncidentState.NEW.value, source="user_claim")
    db.add(incident)
    db.add(AuditEvent(actor=actor, event_type="INCIDENT_CREATED", incident_id=incident.id,
        new_state=incident.state, details={"claim_source": "user_supplied"}))
    db.commit(); db.refresh(incident)
    return incident

@router.get("/incidents/{incident_id}", response_model=IncidentDetailView)
def get_incident(incident_id: str, db: Session = Depends(get_db)):
    incident = db.get(PaymentIncident, incident_id)
    if not incident: raise HTTPException(404, "Incident not found")
    return incident

@router.post("/incidents/{incident_id}/recommendation")
def incident_recommendation(incident_id: str, db: Session = Depends(get_db),
    actor: str = Depends(require_operator)):
    incident = db.get(PaymentIncident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    evidence = sorted((item for item in incident.evidence if item.kind == "transfer_retrieval"),
        key=lambda item: item.captured_at, reverse=True)
    latest = evidence[0] if evidence else None
    verified_facts = {
        "transfer_id": incident.original_transfer_id,
        "state": incident.state,
        "external_status": incident.external_status,
        "evidence_source": latest.source if latest else None,
        "evidence_captured_at": latest.captured_at.isoformat() if latest else None,
        "transfer_evidence": latest.payload if latest and latest.source == "airwallex" else None,
    }
    interpretation = None
    provider_status = "deterministic_fallback"
    try:
        interpretation = Advisor().interpret({
            "verified_facts": verified_facts,
            "supplier_claim_untrusted": incident.supplier_claim,
            "safety_boundary": "No action is authorized. Do not infer receipt or advise a retry.",
        }).model_dump()
        provider_status = "available"
    except AIUnavailable:
        pass
    next_step = ("Review the verified Airwallex evidence and run deterministic recovery checks."
        if latest and latest.source == "airwallex" else
        "Retrieve current Airwallex transfer evidence before considering recovery.")
    db.add(AuditEvent(actor=actor, event_type="INCIDENT_RECOMMENDATION_GENERATED",
        incident_id=incident.id, external_transfer_id=incident.original_transfer_id,
        new_state=incident.state, details={"provider_status": provider_status,
            "evidence_source": latest.source if latest else None,
            "financial_action_authorized": False}))
    db.commit()
    return {"incident_id": incident.id,
        "verified_facts": verified_facts,
        "unverified_claims": [incident.supplier_claim],
        "ai_interpretation": interpretation,
        "ai_status": provider_status,
        "proposed_action": "INVESTIGATE" if not latest or latest.source != "airwallex" else "EVALUATE_RECOVERY",
        "next_step": interpretation["next_step"] if interpretation else next_step,
        "financial_action_authorized": False,
        "note": "AI text is interpretation only. State, balances, fees, eligibility, approval, and execution are determined by backend checks."}

@router.post("/incidents/{incident_id}/investigate")
def investigate(incident_id: str, db: Session = Depends(get_db),
    actor: str = Depends(require_operator)):
    incident = db.get(PaymentIncident, incident_id)
    if not incident: raise HTTPException(404, "Incident not found")
    previous = incident.state
    try:
        incident.state = transition(IncidentState(previous), IncidentState.INVESTIGATING).value
    except (ValueError, KeyError) as exc:
        raise HTTPException(409, "Incident cannot be investigated from its current state") from exc
    verified = False
    note = "Transfer details were not retrieved. No replacement is permitted."
    payload = {"result": "external_transfer_not_retrieved", "reason": "sandbox_credentials_unavailable"}
    source = "application"
    if settings.airwallex_configured:
        try:
            transfer = AirwallexClient().get_transfer(incident.original_transfer_id)
            if transfer["id"] != incident.original_transfer_id:
                raise AirwallexError("invalid_response", "Airwallex returned a different transfer identifier")
            payload = safe_transfer_summary(transfer)
            source = "airwallex"
            verified = True
            incident.external_status = transfer["status"]
            incident.source = "airwallex"
            incident.state = normalize_transfer_evidence(transfer["status"], transfer.get("failure")).value
            note = f"Airwallex returned transfer status {transfer['status']}. A status does not independently prove supplier receipt."
        except AirwallexError as exc:
            payload = {"result": "external_transfer_not_retrieved", "error_category": exc.category}
            incident.external_status = None
            incident.source = "unverified"
            incident.state = IncidentState.OUTCOME_UNKNOWN.value
            note = f"Transfer could not be verified ({exc.category}). No replacement is permitted."
    else:
        incident.external_status = None
        incident.source = "unverified"
        incident.state = IncidentState.OUTCOME_UNKNOWN.value
    db.add(IncidentEvidence(incident_id=incident.id, kind="transfer_retrieval", source=source, payload=payload))
    db.add(AuditEvent(actor=actor, event_type="INVESTIGATION_BLOCKED", incident_id=incident.id,
        external_transfer_id=incident.original_transfer_id, previous_state=previous,
        new_state=incident.state, details={"verified_external_evidence": verified}))
    db.commit()
    facts = [payload] if verified else []
    return {"incident_id": incident.id, "state": incident.state, "verified_facts": facts,
        "unverified_claims": [incident.supplier_claim], "note": note}

@router.post("/incidents/{incident_id}/refresh-beneficiary")
def refresh_incident_beneficiary(incident_id: str, db: Session = Depends(get_db),
    actor: str = Depends(require_operator)):
    incident = db.get(PaymentIncident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    evidence = [item for item in incident.evidence
        if item.kind == "transfer_retrieval" and item.source == "airwallex"]
    latest = max(evidence, key=lambda item: item.captured_at, default=None)
    beneficiary_id = latest.payload.get("beneficiary_id") if latest and isinstance(latest.payload, dict) else None
    if not isinstance(beneficiary_id, str) or not beneficiary_id:
        raise HTTPException(409, "A verified transfer with a saved beneficiary is required first")
    if not settings.airwallex_configured:
        raise HTTPException(503, "Live beneficiary data unavailable: Airwallex sandbox credentials are not configured")
    try:
        beneficiary = AirwallexClient().get_beneficiary(beneficiary_id)
        if beneficiary["id"] != beneficiary_id:
            raise AirwallexError("invalid_response", "Airwallex returned a different beneficiary identifier")
        summary = safe_beneficiary_summary(beneficiary)
    except AirwallexError as exc:
        raise HTTPException(502, f"Airwallex {exc.category}: {exc}") from exc
    captured_at = datetime.now(timezone.utc)
    db.add(IncidentEvidence(incident_id=incident.id, kind="beneficiary_retrieval",
        source="airwallex", payload=summary, captured_at=captured_at))
    db.add(AuditEvent(actor=actor, event_type="BENEFICIARY_RETRIEVED",
        incident_id=incident.id, external_transfer_id=incident.original_transfer_id,
        new_state=incident.state, details={"beneficiary_id": beneficiary_id,
            "source": "airwallex", "pii_omitted": True}))
    db.commit()
    return {"incident_id": incident.id, "beneficiary": summary,
        "source": "airwallex", "captured_at": captured_at,
        "note": "Saved beneficiary details were retrieved. Bank account identifiers are omitted; retrieval does not establish beneficiary ownership or payment eligibility."}

@router.post("/incidents/{incident_id}/evaluate-recovery", response_model=EvaluationView)
def evaluate_recovery(incident_id: str, db: Session = Depends(get_db),
    actor: str = Depends(require_operator)):
    incident = db.get(PaymentIncident, incident_id)
    if not incident: raise HTTPException(404, "Incident not found")
    failure_verified = incident.state == IncidentState.FAILED_CONFIRMED.value and incident.source == "airwallex"
    reasons = [] if failure_verified else ["Original transfer failure has not been verified from Airwallex"]
    reasons.extend(["Fresh live balance has not been checked", "Transfer fees have not been verified",
                    "Related transfers and unresolved operations have not been reconciled",
                    "Reserve policy and action-bound approval have not been evaluated"])
    result = EvaluationView(incident_id=incident.id, proposed_action="REPLACE_TRANSFER" if failure_verified else "INVESTIGATE",
        eligible=False, duplicate_risk="UNRESOLVED", reasons=reasons,
        note="This endpoint only evaluates; it never executes a transfer.")
    db.add(AuditEvent(actor=actor, event_type="RECOVERY_EVALUATED", incident_id=incident.id,
        external_transfer_id=incident.original_transfer_id, new_state=incident.state,
        details={"proposed_action": result.proposed_action, "eligible": result.eligible,
            "duplicate_risk": result.duplicate_risk, "reasons": result.reasons}))
    db.commit()
    return result

@router.get("/audit")
def audit(limit: int = 100, db: Session = Depends(get_db)):
    if not 1 <= limit <= 500: raise HTTPException(422, "limit must be between 1 and 500")
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [{"actor": e.actor, "timestamp": e.created_at.replace(tzinfo=timezone.utc) if e.created_at.tzinfo is None else e.created_at.astimezone(timezone.utc), "event_type": e.event_type,
        "incident_id": e.incident_id, "external_transfer_id": e.external_transfer_id,
        "previous_state": e.previous_state, "new_state": e.new_state,
        "policy_version": e.policy_version, "approval_reference": e.approval_id, "details": e.details} for e in events]
