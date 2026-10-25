"""Durable, fail-closed lifecycle for a future transfer-create route.

This module deliberately is not mounted as an API route. A caller must pass all
policy and approval gates before it can invoke the Airwallex write adapter.
"""
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Approval, AgentExecution, AuditEvent, IdempotencyRecord, IncidentEvidence, PaymentIncident, TreasuryPolicy
from app.services.airwallex.client import AirwallexError, safe_transfer_summary


IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
ACTIVE_STATUSES = {
    "PREPARED", "SUBMITTING", "OUTCOME_UNKNOWN", "RECONCILING", "VERIFYING",
    "CREATED_VERIFIED", "ESCALATED",
}
MAX_ORIGINAL_EVIDENCE_AGE = timedelta(minutes=5)
APPROVAL_LIFETIME = timedelta(minutes=5)


class OperationConflict(Exception):
    """The operation key, incident state, or external evidence conflicts."""


def _json_value(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Transfer parameters must contain finite decimals")
        return format(value, "f")
    if isinstance(value, float):
        raise ValueError("Binary floating point is not accepted in transfer parameters")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError("Unsupported transfer parameter type")


def _canonical(value) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parameters_hash(parameters: dict) -> str:
    return hashlib.sha256(_canonical(parameters).encode("utf-8")).hexdigest()


def _validate_request(request: dict) -> dict:
    if not isinstance(request, dict):
        raise ValueError("Transfer request must be an object")
    if "beneficiary" in request:
        raise ValueError("Direct beneficiary data is not supported in this operation store")
    allowed = {
        "beneficiary_id", "transfer_amount", "transfer_currency", "transfer_method",
        "reason", "reference", "source_currency", "fee_paid_by", "transfer_date",
        "lock_rate_on_create", "remarks",
    }
    if set(request) - allowed:
        raise ValueError("Transfer request contains unsupported fields")
    required = ("beneficiary_id", "transfer_amount", "transfer_currency", "transfer_method",
                "reason", "reference", "source_currency")
    if any(not isinstance(request.get(key), str) or not request[key].strip() for key in required if key != "transfer_amount"):
        raise ValueError("Transfer request is missing a required non-empty string")
    if isinstance(request.get("transfer_amount"), float):
        raise ValueError("Binary floating point is not accepted for transfer amount")
    try:
        amount = Decimal(str(request.get("transfer_amount")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Transfer amount must be a positive decimal") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Transfer amount must be a positive finite decimal")
    if not re.fullmatch(r"[A-Z]{3}", request["transfer_currency"]):
        raise ValueError("Transfer currency must be a three-letter uppercase code")
    if request["transfer_method"] not in {"LOCAL", "SWIFT"}:
        raise ValueError("Only documented LOCAL and SWIFT transfer methods are supported")
    if not re.fullmatch(r"[A-Z]{3}", request["source_currency"]):
        raise ValueError("Source currency must be a three-letter uppercase code")
    if request["source_currency"] != request["transfer_currency"]:
        raise ValueError("Cross-currency recovery is not supported by this operation store")
    if request.get("fee_paid_by") not in (None, "PAYER", "BENEFICIARY"):
        raise ValueError("fee_paid_by must be PAYER or BENEFICIARY")
    normalized = dict(request)
    normalized["transfer_amount"] = format(amount, "f")
    return _json_value(normalized)


def _audit(db: Session, actor: str, event_type: str, execution: AgentExecution, details: dict | None = None):
    db.add(AuditEvent(actor=actor, event_type=event_type, incident_id=execution.incident_id,
        external_transfer_id=execution.external_transfer_id, new_state=execution.status,
        details={"execution_id": execution.id, "request_id": execution.request_id, **(details or {})}))


def _set_status(db: Session, idem: IdempotencyRecord, execution: AgentExecution, status: str):
    execution.status = status
    idem.status = status


def _matches_request(candidate: dict, execution: AgentExecution) -> bool:
    request = execution.request_parameters
    if candidate.get("request_id") != execution.request_id:
        return False
    for field in ("transfer_currency", "source_currency", "transfer_method", "beneficiary_id", "reason", "reference", "fee_paid_by"):
        if field in request and candidate.get(field) != request[field]:
            return False
    try:
        actual_amount = Decimal(str(candidate.get("transfer_amount")))
        expected_amount = Decimal(str(request.get("transfer_amount")))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return actual_amount.is_finite() and actual_amount == expected_amount


def _load_idempotency(db: Session, execution: AgentExecution) -> IdempotencyRecord:
    operation_key = execution.request_parameters.get("_operation_key")
    if not isinstance(operation_key, str):
        raise OperationConflict("Durable operation has no idempotency key")
    record = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.operation_key == operation_key,
        IdempotencyRecord.incident_id == execution.incident_id,
    ).with_for_update())
    if record is None:
        raise OperationConflict("Durable idempotency record is missing")
    return record


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _approval_context(db: Session, execution: AgentExecution, now: datetime,
                      expected_policy_version: int | None = None) -> tuple[PaymentIncident, IncidentEvidence, TreasuryPolicy, str, str]:
    incident = db.scalar(select(PaymentIncident).where(
        PaymentIncident.id == execution.incident_id).with_for_update())
    if incident is None or incident.simulated or incident.source != "airwallex" or incident.state != "FAILED_CONFIRMED":
        raise OperationConflict("A verified Airwallex failure is required for approval")
    original = db.scalar(select(IncidentEvidence).where(
        IncidentEvidence.incident_id == incident.id,
        IncidentEvidence.kind == "transfer_retrieval",
        IncidentEvidence.source == "airwallex",
    ).order_by(IncidentEvidence.captured_at.desc()).limit(1).with_for_update())
    if original is None:
        raise OperationConflict("Verified original transfer evidence is unavailable")
    captured_at = _utc(original.captured_at)
    age = now - captured_at
    if age < timedelta(seconds=-30) or age > MAX_ORIGINAL_EVIDENCE_AGE:
        raise OperationConflict("Fresh Airwallex evidence is required for approval")
    parameters = execution.request_parameters
    try:
        original_amount = Decimal(str(original.payload.get("transfer_amount")))
        incident_amount = Decimal(str(incident.amount))
        requested_amount = Decimal(str(parameters.get("transfer_amount")))
    except (InvalidOperation, TypeError, ValueError):
        raise OperationConflict("Verified transfer amount is unavailable for approval")
    if (original.payload.get("id") != incident.original_transfer_id
            or original.payload.get("status") not in {"FAILED", "CANCELLED"}
            or (original.payload.get("status") == "CANCELLED" and not original.payload.get("failure"))
            or original.payload.get("transfer_currency") != incident.currency
            or original.payload.get("transfer_currency") != parameters.get("transfer_currency")
            or original.payload.get("source_currency") != parameters.get("source_currency")
            or original.payload.get("transfer_method") != parameters.get("transfer_method")
            or original.payload.get("beneficiary_id") != parameters.get("beneficiary_id")
            or not original_amount.is_finite() or original_amount != incident_amount
            or original_amount != requested_amount):
        raise OperationConflict("The prepared action no longer matches the verified failed transfer")
    policy = db.scalar(select(TreasuryPolicy).where(
        TreasuryPolicy.currency == incident.currency).with_for_update())
    if policy is None:
        raise OperationConflict("A current treasury policy is required for approval")
    if expected_policy_version is not None and policy.version != expected_policy_version:
        raise OperationConflict("Treasury policy changed after approval was requested")
    action_hash = _parameters_hash(execution.request_parameters)
    evidence_version = _parameters_hash({"captured_at": captured_at.isoformat(), "payload": original.payload})
    return incident, original, policy, action_hash, evidence_version


def request_transfer_approval(db: Session, execution_id: str, actor: str,
                              now: datetime | None = None) -> Approval:
    """Request time-limited dual-control approval for one exact prepared operation."""
    now = _utc(now or datetime.now(timezone.utc))
    execution = db.scalar(select(AgentExecution).where(
        AgentExecution.id == execution_id).with_for_update())
    if execution is None or execution.operation != "TRANSFER_CREATE" or execution.status != "PREPARED":
        raise OperationConflict("Only a prepared transfer operation can request approval")
    if actor != "operator":
        raise OperationConflict("Only an operator can request approval")
    incident, _, policy, action_hash, evidence_version = _approval_context(db, execution, now)
    existing = db.scalar(select(Approval).where(
        Approval.operation_id == execution.id,
        Approval.status == "PENDING").order_by(Approval.expires_at.desc()).limit(1).with_for_update())
    if existing is not None and _utc(existing.expires_at) > now:
        if (existing.action_hash != action_hash or existing.evidence_version != evidence_version
                or existing.policy_version != policy.version):
            existing.status = "STALE"
            db.commit()
            raise OperationConflict("Pending approval no longer matches the prepared action or current evidence")
        return existing
    if existing is not None:
        existing.status = "EXPIRED"
    approval = Approval(id=str(uuid4()), incident_id=incident.id, operation_id=execution.id,
        action_hash=action_hash, counterparty=incident.supplier,
        amount=Decimal(execution.request_parameters["transfer_amount"]),
        currency=execution.request_parameters["transfer_currency"],
        evidence_version=evidence_version, status="PENDING", expires_at=now + APPROVAL_LIFETIME,
        requested_by=actor, policy_version=policy.version)
    db.add(approval)
    db.add(AuditEvent(actor=actor, event_type="TRANSFER_APPROVAL_REQUESTED",
        incident_id=incident.id, external_transfer_id=incident.original_transfer_id,
        approval_id=approval.id, policy_version=policy.version,
        details={"execution_id": execution.id, "action_hash": action_hash,
            "evidence_version": evidence_version, "expires_at": approval.expires_at.isoformat()}))
    db.commit()
    db.refresh(approval)
    return approval


def approve_transfer_action(db: Session, approval_id: str, actor: str,
                            now: datetime | None = None) -> Approval:
    """Approve only if the exact request, evidence, policy, and maker-checker roles still match."""
    now = _utc(now or datetime.now(timezone.utc))
    approval_ref = db.scalar(select(Approval).where(Approval.id == approval_id))
    if approval_ref is None:
        raise OperationConflict("Approval does not exist")
    # Lock order is operation → approval everywhere to avoid deadlocks with submission.
    execution = db.scalar(select(AgentExecution).where(
        AgentExecution.id == approval_ref.operation_id).with_for_update())
    approval = db.scalar(select(Approval).where(
        Approval.id == approval_id).with_for_update())
    if approval is None:
        raise OperationConflict("Approval does not exist")
    if approval.operation_id != approval_ref.operation_id:
        raise OperationConflict("Approval binding changed while it was being processed")
    if approval.status != "PENDING":
        raise OperationConflict("Approval is no longer pending")
    if actor != "admin" or actor == approval.requested_by:
        raise OperationConflict("A separate policy administrator must approve this action")
    if _utc(approval.expires_at) <= now:
        approval.status = "EXPIRED"
        db.commit()
        raise OperationConflict("Approval request has expired")
    if execution is None or execution.status != "PREPARED":
        approval.status = "STALE"
        db.commit()
        raise OperationConflict("Prepared transfer operation is no longer available")
    try:
        _, _, _, action_hash, evidence_version = _approval_context(
            db, execution, now, expected_policy_version=approval.policy_version)
    except OperationConflict:
        approval.status = "STALE"
        db.commit()
        raise
    if (approval.incident_id != execution.incident_id
            or approval.action_hash != action_hash
            or approval.evidence_version != evidence_version):
        approval.status = "STALE"
        db.commit()
        raise OperationConflict("Approval no longer matches the prepared action or evidence")
    approval.status = "APPROVED"
    approval.approved_by = actor
    approval.approved_at = now
    db.add(AuditEvent(actor=actor, event_type="TRANSFER_ACTION_APPROVED",
        incident_id=approval.incident_id, approval_id=approval.id,
        policy_version=approval.policy_version,
        details={"execution_id": execution.id, "action_hash": approval.action_hash,
            "evidence_version": approval.evidence_version, "expires_at": approval.expires_at.isoformat()}))
    db.commit()
    db.refresh(approval)
    return approval


def _consume_current_approval(db: Session, execution: AgentExecution, actor: str, now: datetime) -> Approval:
    approval = db.scalar(select(Approval).where(
        Approval.operation_id == execution.id,
        Approval.status == "APPROVED").order_by(Approval.approved_at.desc()).limit(1).with_for_update())
    if approval is None or approval.approved_by is None or approval.approved_by == approval.requested_by:
        raise OperationConflict("A separate policy administrator approval is required before submission")
    if actor != approval.requested_by:
        raise OperationConflict("Only the requesting operator can submit this approved operation")
    if _utc(approval.expires_at) <= now:
        approval.status = "EXPIRED"
        db.commit()
        raise OperationConflict("Approval expired before submission")
    try:
        _, _, _, action_hash, evidence_version = _approval_context(
            db, execution, now, expected_policy_version=approval.policy_version)
    except OperationConflict:
        approval.status = "STALE"
        db.commit()
        raise
    if (approval.incident_id != execution.incident_id or approval.action_hash != action_hash
            or approval.evidence_version != evidence_version):
        approval.status = "STALE"
        db.commit()
        raise OperationConflict("Approval no longer matches the prepared action or evidence")
    return approval


def prepare_transfer_create(db: Session, incident_id: str, operation_key: str,
                            request: dict, actor: str) -> AgentExecution:
    """Commit a stable Airwallex request ID before any external write is attempted."""
    if not isinstance(operation_key, str) or not IDEMPOTENCY_KEY.fullmatch(operation_key):
        raise ValueError("Idempotency key must be 1–80 safe characters")
    parameters = _validate_request(request)
    parameters_hash = _parameters_hash(parameters)
    stored_key = f"{incident_id}:{operation_key}"
    if len(stored_key) > 128:
        raise ValueError("Idempotency key is too long")

    existing = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.operation_key == stored_key).with_for_update())
    if existing is not None:
        if existing.parameters_hash != parameters_hash:
            raise OperationConflict("Idempotency key was already used with different transfer parameters")
        executions = db.scalars(select(AgentExecution).where(
            AgentExecution.incident_id == incident_id,
            AgentExecution.operation == "TRANSFER_CREATE")).all()
        execution = next((item for item in executions
            if item.request_parameters.get("_operation_key") == stored_key), None)
        if execution is None:
            raise OperationConflict("Idempotency record has no matching durable execution")
        return execution

    incident = db.scalar(select(PaymentIncident).where(PaymentIncident.id == incident_id).with_for_update())
    if incident is None:
        raise OperationConflict("Incident does not exist")
    if incident.simulated or incident.source != "airwallex" or incident.state != "FAILED_CONFIRMED":
        raise OperationConflict("A verified Airwallex failure is required before preparing recovery")

    original = db.scalar(select(IncidentEvidence).where(
        IncidentEvidence.incident_id == incident_id,
        IncidentEvidence.kind == "transfer_retrieval",
        IncidentEvidence.source == "airwallex",
    ).order_by(IncidentEvidence.captured_at.desc()).limit(1))
    evidence = original.payload if original is not None else {}
    captured_at = original.captured_at if original is not None else None
    if captured_at is None:
        raise OperationConflict("Verified original transfer evidence is unavailable")
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    evidence_age = now - captured_at.astimezone(timezone.utc)
    if evidence_age < timedelta(seconds=-30) or evidence_age > MAX_ORIGINAL_EVIDENCE_AGE:
        raise OperationConflict("Fresh Airwallex evidence is required before preparing recovery")
    try:
        original_amount = Decimal(str(evidence.get("transfer_amount")))
        incident_amount = Decimal(str(incident.amount))
        requested_amount = Decimal(str(parameters["transfer_amount"]))
    except (InvalidOperation, TypeError, ValueError):
        raise OperationConflict("Verified original transfer amount is unavailable")
    if (evidence.get("id") != incident.original_transfer_id
            or evidence.get("status") not in {"FAILED", "CANCELLED"}
            or (evidence.get("status") == "CANCELLED" and not evidence.get("failure"))
            or evidence.get("transfer_currency") != incident.currency
            or evidence.get("transfer_currency") != parameters["transfer_currency"]
            or not original_amount.is_finite()
            or original_amount != incident_amount
            or original_amount != requested_amount
            or evidence.get("beneficiary_id") != parameters["beneficiary_id"]
            or evidence.get("transfer_method") != parameters["transfer_method"]
            or evidence.get("source_currency") != parameters["source_currency"]):
        raise OperationConflict("Recovery must match fresh verified original transfer evidence")

    active = db.scalar(select(AgentExecution).where(
        AgentExecution.incident_id == incident_id,
        AgentExecution.operation == "TRANSFER_CREATE",
        AgentExecution.status.in_(ACTIVE_STATUSES),
    ).with_for_update())
    if active is not None:
        raise OperationConflict("An existing transfer operation must be resolved before another is prepared")

    reserved = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.original_transfer_id == incident.original_transfer_id).with_for_update())
    if reserved is not None:
        raise OperationConflict("A recovery attempt is already reserved for this original transfer")

    request_id = str(uuid4())
    parameters["request_id"] = request_id
    parameters["_operation_key"] = stored_key
    execution = AgentExecution(id=str(uuid4()), incident_id=incident_id,
        operation="TRANSFER_CREATE", status="PREPARED", request_id=request_id,
        request_parameters=parameters)
    idem = IdempotencyRecord(operation_key=stored_key, incident_id=incident_id,
        original_transfer_id=incident.original_transfer_id,
        status="PREPARED", parameters_hash=parameters_hash)
    db.add(execution)
    db.add(idem)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise OperationConflict("Another request prepared this recovery concurrently") from exc
    _audit(db, actor, "TRANSFER_CREATE_PREPARED", execution,
        {"parameters_hash": parameters_hash})
    db.commit()
    db.refresh(execution)
    return execution


def mark_submitting(db: Session, execution_id: str, actor: str) -> AgentExecution:
    execution = db.scalar(select(AgentExecution).where(AgentExecution.id == execution_id).with_for_update())
    if execution is None or execution.status != "PREPARED":
        raise OperationConflict("Only a prepared transfer operation can be submitted")
    approval = _consume_current_approval(db, execution, actor, datetime.now(timezone.utc))
    idem = _load_idempotency(db, execution)
    # Commit before network I/O. A crash from this point onward is treated as ambiguous.
    _set_status(db, idem, execution, "SUBMITTING")
    approval.status = "CONSUMED"
    _audit(db, actor, "TRANSFER_CREATE_SUBMITTING", execution,
        {"approval_id": approval.id, "action_hash": approval.action_hash})
    db.commit()
    db.refresh(execution)
    return execution


def _record_candidate(db: Session, execution: AgentExecution, idem: IdempotencyRecord,
                      candidate: dict, actor: str, event_type: str) -> AgentExecution:
    if not isinstance(candidate.get("id"), str) or not isinstance(candidate.get("status"), str) or not _matches_request(candidate, execution):
        _set_status(db, idem, execution, "ESCALATED")
        _audit(db, actor, "TRANSFER_CREATE_RECONCILIATION_CONFLICT", execution,
            {"reason": "External transfer did not match the durable request"})
        db.commit()
        return execution
    execution.external_transfer_id = candidate["id"]
    execution.response_summary = safe_transfer_summary(candidate)
    _set_status(db, idem, execution, "VERIFYING")
    _audit(db, actor, event_type, execution, {"external_status": candidate["status"]})
    db.commit()
    db.refresh(execution)
    return execution


def record_create_response(db: Session, execution_id: str, response: dict, actor: str) -> AgentExecution:
    execution = db.scalar(select(AgentExecution).where(AgentExecution.id == execution_id).with_for_update())
    if execution is None or execution.status != "SUBMITTING":
        raise OperationConflict("Transfer operation is not awaiting a create response")
    idem = _load_idempotency(db, execution)
    return _record_candidate(db, execution, idem, response, actor, "TRANSFER_CREATE_RESPONSE_RECEIVED")


def mark_outcome_unknown(db: Session, execution_id: str, actor: str) -> AgentExecution:
    execution = db.scalar(select(AgentExecution).where(AgentExecution.id == execution_id).with_for_update())
    if execution is None or execution.status != "SUBMITTING":
        raise OperationConflict("Only an in-flight transfer operation can become outcome unknown")
    idem = _load_idempotency(db, execution)
    _set_status(db, idem, execution, "OUTCOME_UNKNOWN")
    _audit(db, actor, "TRANSFER_CREATE_OUTCOME_UNKNOWN", execution)
    db.commit()
    db.refresh(execution)
    return execution


def reconcile_transfer_create(db: Session, execution_id: str, client, actor: str) -> AgentExecution:
    """Use the same external request ID; never submit another create request."""
    execution = db.scalar(select(AgentExecution).where(AgentExecution.id == execution_id).with_for_update())
    if execution is None or execution.status not in {"SUBMITTING", "OUTCOME_UNKNOWN", "RECONCILING"}:
        raise OperationConflict("Only an unresolved transfer operation can be reconciled")
    idem = _load_idempotency(db, execution)
    _set_status(db, idem, execution, "RECONCILING")
    _audit(db, actor, "TRANSFER_CREATE_RECONCILIATION_STARTED", execution)
    db.commit()
    try:
        candidates = client.list_transfers_by_request_id(execution.request_id)
    except AirwallexError as exc:
        db.refresh(execution)
        idem = _load_idempotency(db, execution)
        _set_status(db, idem, execution, "OUTCOME_UNKNOWN")
        _audit(db, actor, "TRANSFER_CREATE_RECONCILIATION_FAILED", execution,
            {"error_category": exc.category})
        db.commit()
        return execution

    db.refresh(execution)
    idem = _load_idempotency(db, execution)
    if len(candidates) == 0:
        _set_status(db, idem, execution, "OUTCOME_UNKNOWN")
        _audit(db, actor, "TRANSFER_CREATE_RECONCILIATION_EMPTY", execution,
            {"retry_permitted": False})
        db.commit()
        return execution
    if len(candidates) != 1:
        _set_status(db, idem, execution, "ESCALATED")
        _audit(db, actor, "TRANSFER_CREATE_RECONCILIATION_CONFLICT", execution,
            {"candidate_count": len(candidates)})
        db.commit()
        return execution
    return _record_candidate(db, execution, idem, candidates[0], actor,
        "TRANSFER_CREATE_RECONCILED")


def verify_created_transfer(db: Session, execution_id: str, transfer: dict, actor: str) -> AgentExecution:
    execution = db.scalar(select(AgentExecution).where(AgentExecution.id == execution_id).with_for_update())
    if execution is None or execution.status != "VERIFYING":
        raise OperationConflict("Transfer operation is not awaiting external verification")
    idem = _load_idempotency(db, execution)
    if transfer.get("id") != execution.external_transfer_id or not _matches_request(transfer, execution):
        _set_status(db, idem, execution, "ESCALATED")
        _audit(db, actor, "TRANSFER_CREATE_VERIFICATION_FAILED", execution,
            {"reason": "Retrieved transfer did not match the approved request"})
        db.commit()
        return execution
    execution.response_summary = safe_transfer_summary(transfer)
    _set_status(db, idem, execution, "CREATED_VERIFIED")
    _audit(db, actor, "TRANSFER_CREATED_VERIFIED", execution,
        {"external_status": transfer["status"], "settlement_confirmed": False})
    db.commit()
    db.refresh(execution)
    return execution
