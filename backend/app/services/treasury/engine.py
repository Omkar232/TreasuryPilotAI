from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

@dataclass(frozen=True)
class ObligationInput:
    supplier: str
    amount: Decimal
    currency: str
    priority: str
    due_at: datetime | None = None
    fee: Decimal | None = None

@dataclass(frozen=True)
class TreasuryDecision:
    eligible: bool
    currency: str
    available: Decimal
    reserve_floor: Decimal
    obligation_amount: Decimal
    projected_balance: Decimal | None
    reasons: tuple[str, ...]
    data_as_of: datetime
    assumptions: tuple[str, ...]

@dataclass(frozen=True)
class PlannedObligation:
    obligation: ObligationInput
    decision: TreasuryDecision
    action: str
    balance_after_plan: Decimal | None

@dataclass(frozen=True)
class TreasuryPlan:
    currency: str
    available: Decimal
    reserve_floor: Decimal
    remaining_balance: Decimal
    entries: tuple[PlannedObligation, ...]
    assumptions: tuple[str, ...]

PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}

def plan_obligations(*, available: Decimal, currency: str, reserve_floor: Decimal,
                     obligations: list[ObligationInput], data_as_of: datetime,
                     now: datetime | None = None,
                     priority_order: tuple[str, ...] = ("CRITICAL", "HIGH", "NORMAL", "LOW")) -> TreasuryPlan:
    """Allocate one currency in explicit priority order; never execute payments."""
    normalized_order = tuple(priority.upper() for priority in priority_order)
    if len(normalized_order) != len(PRIORITY_ORDER) or set(normalized_order) != set(PRIORITY_ORDER):
        raise ValueError("Priority order must contain CRITICAL, HIGH, NORMAL, and LOW exactly once")
    priority_ranks = {priority: rank for rank, priority in enumerate(normalized_order)}
    unknown = sorted({o.priority.upper() for o in obligations if o.priority.upper() not in priority_ranks})
    if unknown:
        raise ValueError(f"Unsupported obligation priorities: {', '.join(unknown)}")
    balance = available
    entries: list[PlannedObligation] = []
    def due_key(value: datetime | None):
        if value is None:
            return (1, datetime.max.replace(tzinfo=timezone.utc))
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return (0, normalized)
    ordered = sorted(obligations, key=lambda o: (priority_ranks[o.priority.upper()],
        due_key(o.due_at), o.supplier.casefold()))
    for obligation in ordered:
        decision = evaluate_obligation(available=balance, currency=currency,
            reserve_floor=reserve_floor, obligation=obligation, data_as_of=data_as_of,
            now=now)
        action = "PAY" if decision.eligible else "DEFER"
        if decision.eligible:
            assert decision.projected_balance is not None
            balance = decision.projected_balance
        entries.append(PlannedObligation(obligation, decision, action, balance))
    assumptions = (f"Obligations are considered in {' > '.join(normalized_order)} priority order, then due date.",
        "Each obligation uses only its own explicitly supplied fee; an unknown fee blocks that obligation.",
        "This calculation does not verify fee-quote source, action binding, or expiry; the caller must do so.",
        "Currencies are planned independently; no FX conversion or forecast inflows are included.",
        "A PAY result is a planning recommendation only and does not authorize or initiate a transfer.")
    return TreasuryPlan(currency, available, reserve_floor, balance, tuple(entries), assumptions)

def evaluate_obligation(*, available: Decimal, currency: str, reserve_floor: Decimal,
                        obligation: ObligationInput, data_as_of: datetime,
                        max_age_seconds: int = 300, now: datetime | None = None) -> TreasuryDecision:
    now = now or datetime.now(timezone.utc)
    fee = obligation.fee
    values = (available, reserve_floor, obligation.amount) + (() if fee is None else (fee,))
    if any(not value.is_finite() for value in values):
        raise ValueError("Financial inputs must be finite decimal values")
    reasons: list[str] = []
    if available < 0: reasons.append("Available balance cannot be negative")
    if reserve_floor < 0: reasons.append("Reserve floor cannot be negative")
    if fee is not None and fee < 0: reasons.append("Transfer fee cannot be negative")
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha() or currency.upper() != currency:
        reasons.append("Plan currency must be a three-letter uppercase code")
    if len(obligation.currency) != 3 or not obligation.currency.isascii() or not obligation.currency.isalpha() or obligation.currency.upper() != obligation.currency:
        reasons.append("Obligation currency must be a three-letter uppercase code")
    if currency != obligation.currency: reasons.append("Currency mismatch; no FX conversion is assumed")
    if fee is None: reasons.append("Transfer fee is unknown")
    if now.tzinfo is None or data_as_of.tzinfo is None:
        reasons.append("Balance timestamp must include a timezone")
        age = max_age_seconds + 1
    else:
        age = (now.astimezone(timezone.utc) - data_as_of.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > max_age_seconds: reasons.append("Balance data is stale or has an invalid timestamp")
    projected = None if fee is None else available - obligation.amount - fee
    if projected is not None and projected < 0: reasons.append("Insufficient available balance")
    if projected is not None and projected < reserve_floor: reasons.append("Action would breach the reserve floor")
    if obligation.amount <= 0: reasons.append("Obligation amount must be positive")
    return TreasuryDecision(not reasons, currency, available, reserve_floor, obligation.amount, projected,
        tuple(reasons), data_as_of, ("No cross-currency netting", "No forecast inflows included",
            "Fee must be supplied per obligation; quote provenance and expiry are not checked here"))
