from decimal import Decimal
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.database import Base
from app.models import AgentExecution, Approval, AuditEvent, IdempotencyRecord, IncidentEvidence, PaymentIncident, TreasuryPolicy
from app.services.airwallex.operations import (
    OperationConflict,
    approve_transfer_action,
    mark_outcome_unknown,
    mark_submitting,
    prepare_transfer_create,
    reconcile_transfer_create,
    request_transfer_approval,
    verify_created_transfer,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(PaymentIncident(id="incident-1", supplier="Example Ltd",
            original_transfer_id="tr_original", amount=Decimal("125.00"), currency="USD",
            supplier_claim="Supplier reports delay", state="FAILED_CONFIRMED", source="airwallex"))
        session.add(IncidentEvidence(incident_id="incident-1", kind="transfer_retrieval", source="airwallex",
            payload={"id": "tr_original", "status": "FAILED", "transfer_amount": "125.00",
                "transfer_currency": "USD", "source_currency": "USD", "transfer_method": "LOCAL",
                "beneficiary_id": "beneficiary_123", "failure": {"code": "90101"}}))
        session.add(TreasuryPolicy(currency="USD", reserve_floor=Decimal("0"),
            approval_threshold=Decimal("0"), priority_order=["CRITICAL", "HIGH", "NORMAL", "LOW"], version=1))
        session.commit()
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def request_payload():
    return {
        "beneficiary_id": "beneficiary_123",
        "transfer_amount": Decimal("125.00"),
        "transfer_currency": "USD",
        "transfer_method": "LOCAL",
        "reason": "supplier_payment",
        "reference": "INV-123",
        "source_currency": "USD",
    }


def matching_transfer(execution, transfer_id="tr_recovery"):
    request = execution.request_parameters
    return {
        "id": transfer_id,
        "status": "SCHEDULED",
        "request_id": execution.request_id,
        "beneficiary_id": request["beneficiary_id"],
        "transfer_amount": request["transfer_amount"],
        "transfer_currency": request["transfer_currency"],
        "source_currency": request["source_currency"],
        "transfer_method": request["transfer_method"],
        "reason": request["reason"],
        "reference": request["reference"],
        "amount_payer_pays": "128.00",
        "fee_amount": "3.00",
        "fee_currency": "USD",
    }


def approve_prepared(db, execution):
    approval = request_transfer_approval(db, execution.id, "operator")
    return approve_transfer_action(db, approval.id, "admin")


def test_prepare_persists_stable_request_and_idempotent_replay(db, request_payload):
    first = prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    replay = prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    assert first.id == replay.id
    assert first.request_id == replay.request_id
    assert first.status == "PREPARED"
    assert first.request_parameters["request_id"] == first.request_id
    assert db.scalar(select(IdempotencyRecord)).status == "PREPARED"
    assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "TRANSFER_CREATE_PREPARED")) is not None


def test_submitting_requires_separate_action_bound_approval(db, request_payload):
    execution = prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    with pytest.raises(OperationConflict, match="separate policy administrator approval"):
        mark_submitting(db, execution.id, "operator")
    approval = request_transfer_approval(db, execution.id, "operator")
    assert approval.status == "PENDING"
    assert approval.requested_by == "operator"
    assert approval.policy_version == 1
    assert approval.action_hash == sha256(
        json.dumps(execution.request_parameters, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(OperationConflict, match="separate policy administrator"):
        approve_transfer_action(db, approval.id, "operator")
    approved = approve_transfer_action(db, approval.id, "admin")
    assert approved.status == "APPROVED"
    assert approved.approved_by == "admin"
    with pytest.raises(OperationConflict, match="requesting operator"):
        mark_submitting(db, execution.id, "admin")
    submitted = mark_submitting(db, execution.id, "operator")
    assert submitted.status == "SUBMITTING"
    assert db.get(Approval, approval.id).status == "CONSUMED"


def test_approval_expires_and_cannot_be_reused(db, request_payload):
    now = datetime.now(timezone.utc)
    execution = prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    approval = request_transfer_approval(db, execution.id, "operator", now=now)
    with pytest.raises(OperationConflict, match="expired"):
        approve_transfer_action(db, approval.id, "admin", now=now + timedelta(minutes=6))
    assert db.get(Approval, approval.id).status == "EXPIRED"


def test_approval_stales_when_original_evidence_changes(db, request_payload):
    execution = prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    approval = request_transfer_approval(db, execution.id, "operator")
    original = db.scalar(select(IncidentEvidence).where(IncidentEvidence.incident_id == "incident-1"))
    original.payload = {**original.payload, "failure": {"code": "different_failure"}}
    db.commit()
    with pytest.raises(OperationConflict, match="no longer matches"):
        approve_transfer_action(db, approval.id, "admin")
    assert db.get(Approval, approval.id).status == "STALE"


def test_changed_action_or_policy_invalidates_approval(db, request_payload):
    execution = prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    approval = approve_prepared(db, execution)
    execution.request_parameters = {**execution.request_parameters, "transfer_amount": "124.99"}
    db.commit()
    with pytest.raises(OperationConflict, match="no longer matches"):
        mark_submitting(db, execution.id, "operator")
    assert db.get(Approval, approval.id).status == "STALE"

    db2_engine = create_engine("sqlite://")
    Base.metadata.create_all(db2_engine)
    with Session(db2_engine) as db2:
        db2.add(PaymentIncident(id="incident-2", supplier="Example Ltd", original_transfer_id="tr_original",
            amount=Decimal("125.00"), currency="USD", supplier_claim="delay",
            state="FAILED_CONFIRMED", source="airwallex"))
        db2.add(IncidentEvidence(incident_id="incident-2", kind="transfer_retrieval", source="airwallex",
            payload={"id": "tr_original", "status": "FAILED", "transfer_amount": "125.00",
                "transfer_currency": "USD", "source_currency": "USD", "transfer_method": "LOCAL",
                "beneficiary_id": "beneficiary_123", "failure": {"code": "90101"}}))
        db2.add(TreasuryPolicy(currency="USD", reserve_floor=Decimal("0"), approval_threshold=Decimal("0"),
            priority_order=["CRITICAL", "HIGH", "NORMAL", "LOW"], version=1))
        db2.commit()
        op = prepare_transfer_create(db2, "incident-2", "attempt-one", request_payload, "operator")
        pending = request_transfer_approval(db2, op.id, "operator")
        policy = db2.scalar(select(TreasuryPolicy).where(TreasuryPolicy.currency == "USD"))
        policy.version += 1
        db2.commit()
        with pytest.raises(OperationConflict, match="policy changed"):
            approve_transfer_action(db2, pending.id, "admin")
        assert db2.get(Approval, pending.id).status == "STALE"
    Base.metadata.drop_all(db2_engine)
    db2_engine.dispose()


def test_prepare_rejects_key_reuse_with_changed_action(db, request_payload):
    prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    changed = {**request_payload, "transfer_amount": Decimal("126.00")}
    with pytest.raises(OperationConflict, match="different transfer parameters"):
        prepare_transfer_create(db, "incident-1", "attempt-one", changed, "operator")


def test_prepare_blocks_second_active_operation_for_incident(db, request_payload):
    prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    with pytest.raises(OperationConflict, match="must be resolved"):
        prepare_transfer_create(db, "incident-1", "attempt-two", request_payload, "operator")


def test_prepare_requires_verified_airwallex_failure(db, request_payload):
    incident = db.get(PaymentIncident, "incident-1")
    incident.source = "user_claim"
    db.commit()
    with pytest.raises(OperationConflict, match="verified Airwallex failure"):
        prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")


def test_prepare_requires_fresh_original_evidence(db, request_payload):
    original = db.scalar(select(IncidentEvidence))
    original.captured_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    db.commit()
    with pytest.raises(OperationConflict, match="Fresh Airwallex evidence"):
        prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")


def test_only_one_recovery_reservation_can_exist_for_an_original_transfer(db, request_payload):
    prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    db.add(PaymentIncident(id="incident-duplicate", supplier="Example Ltd",
        original_transfer_id="tr_original", amount=Decimal("125.00"), currency="USD",
        supplier_claim="Another incident", state="FAILED_CONFIRMED", source="airwallex"))
    db.add(IncidentEvidence(incident_id="incident-duplicate", kind="transfer_retrieval", source="airwallex",
        payload={"id": "tr_original", "status": "FAILED", "transfer_amount": "125.00",
            "transfer_currency": "USD", "source_currency": "USD", "transfer_method": "LOCAL",
            "beneficiary_id": "beneficiary_123", "failure": {"code": "90101"}}))
    db.commit()
    with pytest.raises(OperationConflict, match="already reserved for this original transfer"):
        prepare_transfer_create(db, "incident-duplicate", "attempt-two", request_payload, "operator")
    assert db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.original_transfer_id == "tr_original")).incident_id == "incident-1"


def test_prepare_binds_amount_to_original_verified_transfer(db, request_payload):
    incident = db.get(PaymentIncident, "incident-1")
    incident.amount = Decimal("126.00")
    db.commit()
    with pytest.raises(OperationConflict, match="match fresh verified original"):
        prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")


def test_empty_reconciliation_keeps_unknown_and_never_authorizes_retry(db, request_payload):
    execution = prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    approve_prepared(db, execution)
    mark_submitting(db, execution.id, "operator")
    mark_outcome_unknown(db, execution.id, "operator")
    class EmptyResults:
        def __init__(self): self.request_ids = []
        def list_transfers_by_request_id(self, request_id):
            self.request_ids.append(request_id)
            return []
    client = EmptyResults()
    result = reconcile_transfer_create(db, execution.id, client, "operator")
    assert result.status == "OUTCOME_UNKNOWN"
    assert client.request_ids == [execution.request_id]
    event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "TRANSFER_CREATE_RECONCILIATION_EMPTY"))
    assert event.details["retry_permitted"] is False


def test_reconciliation_then_retrieval_verifies_resource_not_settlement(db, request_payload):
    execution = prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    approve_prepared(db, execution)
    mark_submitting(db, execution.id, "operator")
    mark_outcome_unknown(db, execution.id, "operator")
    transfer = matching_transfer(execution)
    class FoundResult:
        def list_transfers_by_request_id(self, request_id):
            assert request_id == execution.request_id
            return [transfer]
    result = reconcile_transfer_create(db, execution.id, FoundResult(), "operator")
    assert result.status == "VERIFYING"
    assert result.external_transfer_id == "tr_recovery"
    verified = verify_created_transfer(db, execution.id, transfer, "operator")
    assert verified.status == "CREATED_VERIFIED"
    assert verified.response_summary["fee_amount"] == "3.00"
    assert db.scalar(select(IdempotencyRecord)).status == "CREATED_VERIFIED"
    event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "TRANSFER_CREATED_VERIFIED"))
    assert event.details["settlement_confirmed"] is False


def test_multiple_or_mismatched_external_results_escalate(db, request_payload):
    execution = prepare_transfer_create(db, "incident-1", "attempt-one", request_payload, "operator")
    approve_prepared(db, execution)
    mark_submitting(db, execution.id, "operator")
    mark_outcome_unknown(db, execution.id, "operator")
    candidate = matching_transfer(execution)
    class DuplicateResults:
        def list_transfers_by_request_id(self, request_id): return [candidate, candidate]
    assert reconcile_transfer_create(db, execution.id, DuplicateResults(), "operator").status == "ESCALATED"

    db2_engine = create_engine("sqlite://")
    Base.metadata.create_all(db2_engine)
    with Session(db2_engine) as db2:
        db2.add(PaymentIncident(id="incident-2", supplier="Example Ltd", original_transfer_id="tr_original",
            amount=Decimal("125.00"), currency="USD", supplier_claim="delay",
            state="FAILED_CONFIRMED", source="airwallex"))
        db2.add(IncidentEvidence(incident_id="incident-2", kind="transfer_retrieval", source="airwallex",
            payload={"id": "tr_original", "status": "FAILED", "transfer_amount": "125.00",
                "transfer_currency": "USD", "source_currency": "USD", "transfer_method": "LOCAL",
                "beneficiary_id": "beneficiary_123", "failure": {"code": "90101"}}))
        db2.add(TreasuryPolicy(currency="USD", reserve_floor=Decimal("0"), approval_threshold=Decimal("0"),
            priority_order=["CRITICAL", "HIGH", "NORMAL", "LOW"], version=1))
        db2.commit()
        operation = prepare_transfer_create(db2, "incident-2", "attempt-one", request_payload, "operator")
        approve_prepared(db2, operation)
        mark_submitting(db2, operation.id, "operator")
        mark_outcome_unknown(db2, operation.id, "operator")
        mismatched = matching_transfer(operation)
        mismatched["transfer_amount"] = "999.00"
        class MismatchedResult:
            def list_transfers_by_request_id(self, request_id): return [mismatched]
        assert reconcile_transfer_create(db2, operation.id, MismatchedResult(), "operator").status == "ESCALATED"
    Base.metadata.drop_all(db2_engine)
    db2_engine.dispose()


@pytest.mark.parametrize("payload", [
    {"beneficiary": {"bank_details": {"account_number": "sensitive"}}},
    {"beneficiary_id": "b", "transfer_amount": 125.0, "transfer_currency": "USD", "reason": "x", "reference": "y"},
])
def test_operation_store_rejects_pii_payloads_and_binary_float(db, payload):
    with pytest.raises(ValueError):
        prepare_transfer_create(db, "incident-1", "attempt-one", payload, "operator")
