from enum import StrEnum

class IncidentState(StrEnum):
    NEW = "NEW"
    INVESTIGATING = "INVESTIGATING"
    PROCESSING = "PROCESSING"
    FAILED_CONFIRMED = "FAILED_CONFIRMED"
    COMPLETED_CONFIRMED = "COMPLETED_CONFIRMED"
    CANCELLED_CONFIRMED = "CANCELLED_CONFIRMED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RECOVERY_ELIGIBLE = "RECOVERY_ELIGIBLE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"

AIRWALLEX_STATUS_MAP = {
    "PROCESSING": IncidentState.PROCESSING, "FAILED": IncidentState.FAILED_CONFIRMED,
    # SENT means Airwallex dispatched it, not that the recipient received it.
    "SENT": IncidentState.PROCESSING, "PAID": IncidentState.COMPLETED_CONFIRMED,
    "CANCELLED": IncidentState.OUTCOME_UNKNOWN, "SCHEDULED": IncidentState.PROCESSING,
    "OVERDUE": IncidentState.PROCESSING, "IN_APPROVAL": IncidentState.PROCESSING,
    "REQUIRES_FUNDING": IncidentState.PROCESSING,
    "REQUIRES_FUNDING_CONFIRMATION": IncidentState.PROCESSING,
}

def normalize_transfer_status(raw_status: str | None) -> IncidentState:
    if not raw_status: return IncidentState.OUTCOME_UNKNOWN
    return AIRWALLEX_STATUS_MAP.get(raw_status.upper(), IncidentState.OUTCOME_UNKNOWN)

def normalize_transfer_evidence(raw_status: str | None, failure: dict | None = None) -> IncidentState:
    status = (raw_status or "").upper()
    code = failure.get("code") if isinstance(failure, dict) else None
    details = failure.get("details") if isinstance(failure, dict) else None
    reason_codes = details.get("reason_codes") if isinstance(details, dict) else None
    has_code = isinstance(code, (str, int)) and not isinstance(code, bool) and bool(str(code).strip())
    has_reason_code = isinstance(reason_codes, list) and any(
        isinstance(value, (str, int)) and not isinstance(value, bool) and bool(str(value).strip())
        for value in reason_codes)
    has_structured_failure = has_code or has_reason_code
    if status == "CANCELLED" and has_structured_failure:
        return IncidentState.FAILED_CONFIRMED
    return normalize_transfer_status(status)

VALID_TRANSITIONS = {
    IncidentState.NEW: {IncidentState.INVESTIGATING, IncidentState.ESCALATED},
    IncidentState.INVESTIGATING: {IncidentState.PROCESSING, IncidentState.FAILED_CONFIRMED, IncidentState.COMPLETED_CONFIRMED, IncidentState.CANCELLED_CONFIRMED, IncidentState.OUTCOME_UNKNOWN, IncidentState.ESCALATED},
    IncidentState.OUTCOME_UNKNOWN: {IncidentState.INVESTIGATING, IncidentState.ESCALATED},
    IncidentState.PROCESSING: {IncidentState.INVESTIGATING, IncidentState.COMPLETED_CONFIRMED, IncidentState.FAILED_CONFIRMED, IncidentState.ESCALATED},
    IncidentState.FAILED_CONFIRMED: {IncidentState.RECOVERY_ELIGIBLE, IncidentState.INVESTIGATING, IncidentState.ESCALATED},
    IncidentState.RECOVERY_ELIGIBLE: {IncidentState.AWAITING_APPROVAL, IncidentState.ESCALATED},
    IncidentState.AWAITING_APPROVAL: {IncidentState.EXECUTING, IncidentState.ESCALATED},
    IncidentState.EXECUTING: {IncidentState.VERIFYING, IncidentState.OUTCOME_UNKNOWN, IncidentState.ESCALATED},
    IncidentState.VERIFYING: {IncidentState.RESOLVED, IncidentState.OUTCOME_UNKNOWN, IncidentState.ESCALATED},
    IncidentState.COMPLETED_CONFIRMED: {IncidentState.INVESTIGATING, IncidentState.RESOLVED, IncidentState.ESCALATED},
    IncidentState.CANCELLED_CONFIRMED: {IncidentState.INVESTIGATING, IncidentState.RESOLVED, IncidentState.ESCALATED},
    IncidentState.RESOLVED: set(), IncidentState.ESCALATED: {IncidentState.INVESTIGATING},
}

def transition(current: IncidentState, target: IncidentState) -> IncidentState:
    if current == target:
        return current
    if target not in VALID_TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid incident transition: {current.value} -> {target.value}")
    return target
