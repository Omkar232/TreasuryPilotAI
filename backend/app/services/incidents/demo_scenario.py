"""A deterministic, fully synthetic incident workflow for the hackathon demo.

This module never reads or writes Airwallex and does not mutate application records.
Every external-looking identifier and state below is demo fixture data only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

from app.services.treasury.engine import ObligationInput, plan_obligations


SCENARIO_ID = "treasurypilot-recovery-demo-v1"


def run_demo_recovery_scenario(*, now: datetime | None = None) -> dict:
    captured_at = now or datetime.now(timezone.utc)
    if captured_at.tzinfo is None:
        raise ValueError("Scenario timestamp must include a timezone")
    captured_at = captured_at.astimezone(timezone.utc)

    synthetic_fee = Decimal("0.00")
    obligations = [
        ObligationInput("Northwind Components", Decimal("4500.00"), "USD", "CRITICAL", fee=synthetic_fee),
        ObligationInput("Harbor Shipping", Decimal("3000.00"), "USD", "HIGH", fee=synthetic_fee),
        ObligationInput("Lumen Supplies", Decimal("2500.00"), "USD", "LOW", fee=synthetic_fee),
    ]
    available = Decimal("12000.00")
    reserve_floor = Decimal("3000.00")
    plan = plan_obligations(
        available=available,
        currency="USD",
        reserve_floor=reserve_floor,
        obligations=obligations,
        data_as_of=captured_at,
        now=captured_at,
    )

    action = {
        "type": "REPLACE_TRANSFER",
        "original_transfer_id": "demo-original-transfer-4500",
        "counterparty": "Northwind Components",
        "amount": "4500.00",
        "currency": "USD",
        "beneficiary_id": "demo-beneficiary-001",
        "source_account_id": "demo-usd-account-001",
        "transfer_method": "LOCAL",
        "evidence_version": "synthetic-failed-transfer-v1",
        "policy_version": 1,
    }
    action_hash = sha256(json.dumps(action, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    approval_expires = captured_at + timedelta(minutes=5)

    return {
        "scenario_id": SCENARIO_ID,
        "simulated": True,
        "source": "synthetic_demo_fixture",
        "external_api_called": False,
        "persisted_to_incidents": False,
        "captured_at": captured_at,
        "disclosure": (
            "Every transfer, balance, status transition, approval, execution, and verification in this "
            "scenario is synthetic. No Airwallex API was called and no financial operation occurred."
        ),
        "steps": [
            {
                "name": "investigate_original",
                "source": "synthetic_demo_fixture",
                "original_transfer_id": action["original_transfer_id"],
                "status": "PROCESSING",
                "decision": "BLOCK_REPLACEMENT",
                "reason": "An in-progress original transfer does not permit a replacement.",
            },
            {
                "name": "confirm_failure",
                "source": "synthetic_demo_fixture",
                "transition": "PROCESSING → FAILED",
                "evidence_version": action["evidence_version"],
                "decision": "SIMULATED_FAILURE_CONFIRMED",
                "reason": "This is a fixture transition, not an Airwallex status or retrieved response.",
            },
            {
                "name": "evaluate_treasury",
                "source": "synthetic_demo_fixture",
                "status": "ELIGIBLE_IN_SIMULATION",
                "available": str(available),
                "reserve_floor": str(reserve_floor),
                "synthetic_fee": str(synthetic_fee),
                "policy_version": 1,
                "approval_threshold": "4000.00",
                "plan": [
                    {
                        "supplier": entry.obligation.supplier,
                        "amount": str(entry.obligation.amount),
                        "priority": entry.obligation.priority,
                        "action": entry.action,
                        "projected_balance": str(entry.balance_after_plan),
                        "reasons": list(entry.decision.reasons),
                    }
                    for entry in plan.entries
                ],
                "remaining_balance": str(plan.remaining_balance),
                "reason": "The fee is assumed to be USD 0.00 for this synthetic example only.",
            },
            {
                "name": "approve_exact_action",
                "source": "synthetic_demo_fixture",
                "status": "SIMULATED_APPROVED",
                "approval_id": "demo-approval-001",
                "approved_by": "demo-reviewer",
                "action_hash": action_hash,
                "bound_action": action,
                "expires_at": approval_expires,
            },
            {
                "name": "create_replacement",
                "source": "synthetic_demo_fixture",
                "status": "SIMULATED_CREATED",
                "request_id": "demo-request-001",
                "transfer_id": "demo-replacement-transfer-001",
                "approval_id": "demo-approval-001",
                "action_hash": action_hash,
                "external_api_called": False,
            },
            {
                "name": "verify_replacement",
                "source": "synthetic_demo_fixture",
                "status": "SIMULATED_VERIFIED",
                "transfer_id": "demo-replacement-transfer-001",
                "external_api_called": False,
                "reason": "The fixture verifies its own synthetic result; it is not bank-side evidence.",
            },
        ],
        "assumptions": [
            "USD 12,000.00 available balance, USD 3,000.00 reserve, and all obligations are synthetic.",
            "The USD 0.00 fee is a synthetic assumption, not a fee quote or safe live-transfer estimate.",
            "The initial processing status, later failure, approval, replacement, and verification are fixtures.",
            "A real recovery remains blocked until Airwallex evidence, balance freshness, fees, policy, approval, and reconciliation are verified.",
        ],
    }
