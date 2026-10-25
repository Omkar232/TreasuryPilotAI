from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from app.services.incidents.states import IncidentState, normalize_transfer_evidence, normalize_transfer_status, transition
from app.services.treasury.engine import ObligationInput, evaluate_obligation, plan_obligations

def test_unknown_and_cancelled_status_are_not_recovery_eligible():
    assert normalize_transfer_status("CANCELLED") == IncidentState.OUTCOME_UNKNOWN
    assert normalize_transfer_evidence("CANCELLED", {"message": "free-text failure"}) == IncidentState.OUTCOME_UNKNOWN
    assert normalize_transfer_evidence("CANCELLED", {"code": "90101"}) == IncidentState.FAILED_CONFIRMED
    assert normalize_transfer_status("unrecognized") == IncidentState.OUTCOME_UNKNOWN
    assert normalize_transfer_status(None) == IncidentState.OUTCOME_UNKNOWN

def test_failed_status_maps_to_confirmed_failure():
    assert normalize_transfer_status("FAILED") == IncidentState.FAILED_CONFIRMED
    assert normalize_transfer_status("SENT") == IncidentState.PROCESSING
    assert normalize_transfer_status("PAID") == IncidentState.COMPLETED_CONFIRMED
    assert normalize_transfer_status("SCHEDULED") == IncidentState.PROCESSING
    assert normalize_transfer_status("OVERDUE") == IncidentState.PROCESSING
    assert normalize_transfer_status("IN_APPROVAL") == IncidentState.PROCESSING

def test_state_machine_rejects_processing_to_execution():
    with pytest.raises(ValueError):
        transition(IncidentState.PROCESSING, IncidentState.EXECUTING)

def test_reserve_floor_blocks_and_uses_decimal():
    now = datetime.now(timezone.utc)
    result = evaluate_obligation(available=Decimal("12000.00"), currency="USD",
        reserve_floor=Decimal("3000.00"), obligation=ObligationInput("Supplier", Decimal("4500.00"), "USD", "CRITICAL", fee=Decimal("0.00")),
        data_as_of=now, now=now)
    assert result.projected_balance == Decimal("7500.00")
    assert result.eligible
    blocked = evaluate_obligation(available=Decimal("7000"), currency="USD", reserve_floor=Decimal("3000"),
        obligation=ObligationInput("Supplier", Decimal("4500"), "USD", "CRITICAL", fee=Decimal("0")),
        data_as_of=now, now=now)
    assert not blocked.eligible
    assert "Action would breach the reserve floor" in blocked.reasons

def test_unknown_fee_currency_mismatch_and_stale_balance_block():
    old = datetime.now(timezone.utc) - timedelta(minutes=8)
    result = evaluate_obligation(available=Decimal("12000"), currency="USD", reserve_floor=Decimal("0"),
        obligation=ObligationInput("Supplier", Decimal("100"), "EUR", "NORMAL"), data_as_of=old)
    assert not result.eligible
    assert result.projected_balance is None
    assert len(result.reasons) == 3

def test_priority_plan_defers_lower_priority_to_preserve_reserve():
    now = datetime.now(timezone.utc)
    plan = plan_obligations(available=Decimal("12000.00"), currency="USD",
        reserve_floor=Decimal("3000.00"), data_as_of=now, now=now,
        obligations=[
            ObligationInput("Lower", Decimal("2500.00"), "USD", "LOW", fee=Decimal("0.00")),
            ObligationInput("Shipping", Decimal("3000.00"), "USD", "HIGH", fee=Decimal("0.00")),
            ObligationInput("Critical", Decimal("4500.00"), "USD", "CRITICAL", fee=Decimal("0.00"))])
    assert [(entry.obligation.supplier, entry.action) for entry in plan.entries] == [
        ("Critical", "PAY"), ("Shipping", "PAY"), ("Lower", "DEFER")]
    assert plan.remaining_balance == Decimal("4500.00")
    assert plan.entries[-1].balance_after_plan == Decimal("4500.00")

def test_priority_plan_rejects_unknown_priority():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="Unsupported obligation priorities"):
        plan_obligations(available=Decimal("100"), currency="USD", reserve_floor=Decimal("0"),
            data_as_of=now, now=now,
            obligations=[ObligationInput("Supplier", Decimal("10"), "USD", "URGENT")])

def test_priority_policy_controls_allocation_order():
    now = datetime.now(timezone.utc)
    plan = plan_obligations(available=Decimal("100"), currency="USD", reserve_floor=Decimal("0"),
        data_as_of=now, now=now,
        priority_order=("LOW", "NORMAL", "HIGH", "CRITICAL"), obligations=[
            ObligationInput("Critical", Decimal("80"), "USD", "CRITICAL", fee=Decimal("0")),
            ObligationInput("Low", Decimal("30"), "USD", "LOW", fee=Decimal("0"))])
    assert [(entry.obligation.supplier, entry.action) for entry in plan.entries] == [
        ("Low", "PAY"), ("Critical", "DEFER")]
    assert plan.remaining_balance == Decimal("70")

def test_plan_uses_each_obligations_own_fee_and_holds_only_unknown_quote():
    now = datetime.now(timezone.utc)
    plan = plan_obligations(available=Decimal("100"), currency="USD",
        reserve_floor=Decimal("10"), data_as_of=now, now=now, obligations=[
            ObligationInput("A", Decimal("30"), "USD", "CRITICAL", fee=Decimal("0.50")),
            ObligationInput("B", Decimal("30"), "USD", "HIGH", fee=Decimal("5.00")),
            ObligationInput("C", Decimal("5"), "USD", "NORMAL", fee=None),
        ])
    assert [(entry.obligation.supplier, entry.action) for entry in plan.entries] == [
        ("A", "PAY"), ("B", "PAY"), ("C", "DEFER")]
    assert [entry.decision.projected_balance for entry in plan.entries] == [
        Decimal("69.50"), Decimal("34.50"), None]
    assert plan.remaining_balance == Decimal("34.50")

def test_invalid_negative_cash_and_fee_cannot_be_eligible():
    now = datetime.now(timezone.utc)
    obligation = ObligationInput("Supplier", Decimal("10"), "USD", "NORMAL")
    for available, reserve, fee in [(Decimal("-1"), Decimal("0"), Decimal("0")),
                                    (Decimal("100"), Decimal("-1"), Decimal("0")),
                                    (Decimal("100"), Decimal("0"), Decimal("-1"))]:
        result = evaluate_obligation(available=available, currency="USD", reserve_floor=reserve,
            obligation=ObligationInput(obligation.supplier, obligation.amount, obligation.currency,
                obligation.priority, fee=fee), data_as_of=now, now=now)
        assert not result.eligible
