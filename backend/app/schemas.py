from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field, field_validator

class IncidentCreate(BaseModel):
    supplier: str = Field(min_length=1, max_length=200)
    original_transfer_id: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    supplier_claim: str = Field(min_length=1, max_length=4000)

class IncidentView(BaseModel):
    id: str
    supplier: str
    original_transfer_id: str
    amount: Decimal
    currency: str
    supplier_claim: str
    state: str
    external_status: str | None
    source: str
    simulated: bool
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("created_at")
    @classmethod
    def utc_created_at(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class EvidenceView(BaseModel):
    kind: str
    source: str
    payload: dict
    captured_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("captured_at")
    @classmethod
    def utc_captured_at(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class IncidentDetailView(IncidentView):
    evidence: list[EvidenceView] = []

class EvaluationView(BaseModel):
    incident_id: str
    proposed_action: str
    eligible: bool
    duplicate_risk: str
    reasons: list[str]
    note: str

class ObligationCreate(BaseModel):
    supplier: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    due_at: datetime | None = None
    priority: Literal["CRITICAL", "HIGH", "NORMAL", "LOW"] = "NORMAL"

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class ObligationView(BaseModel):
    id: int
    supplier: str
    amount: Decimal
    currency: str
    due_at: datetime | None
    priority: str
    status: str
    source: str
    simulated: bool
    model_config = {"from_attributes": True}

    @field_validator("due_at")
    @classmethod
    def utc_due_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class TreasuryPolicyUpdate(BaseModel):
    reserve_floor: Decimal = Field(ge=0, max_digits=20, decimal_places=4)
    approval_threshold: Decimal = Field(ge=0, max_digits=20, decimal_places=4)
    priority_order: list[Literal["CRITICAL", "HIGH", "NORMAL", "LOW"]] = Field(default_factory=lambda: ["CRITICAL", "HIGH", "NORMAL", "LOW"])

    @field_validator("priority_order")
    @classmethod
    def require_complete_priority_order(cls, value: list[str]) -> list[str]:
        if len(value) != 4 or set(value) != {"CRITICAL", "HIGH", "NORMAL", "LOW"}:
            raise ValueError("Priority order must contain each priority exactly once")
        return value

class TreasuryPolicyView(BaseModel):
    id: int
    currency: str
    reserve_floor: Decimal
    approval_threshold: Decimal
    priority_order: list[str]
    version: int
    updated_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("updated_at")
    @classmethod
    def utc_updated_at(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class TreasuryPlanEntryView(BaseModel):
    obligation_id: int
    supplier: str
    amount: Decimal
    currency: str
    priority: str
    source: str
    action: Literal["HOLD", "PLAN_TO_PAY", "BLOCKED"]
    approval_required: bool
    projected_balance_after_payment: Decimal | None
    reasons: list[str]

class TreasuryCurrencyPlanView(BaseModel):
    currency: str
    status: Literal["BLOCKED", "PLANNED", "NO_BALANCE", "AMBIGUOUS_ACCOUNT"]
    available_amount: Decimal | None
    reserve_floor: Decimal | None
    approval_threshold: Decimal | None
    policy_version: int | None
    reasons: list[str]
    entries: list[TreasuryPlanEntryView]

class TreasuryPlanView(BaseModel):
    id: str
    source: str
    status: Literal["BLOCKED", "PLANNED", "NO_OPEN_OBLIGATIONS"]
    data_as_of: datetime
    balance_snapshot: list[dict]
    currency_plans: list[TreasuryCurrencyPlanView]
    assumptions: list[str]
    created_at: datetime

    @field_validator("data_as_of", "created_at")
    @classmethod
    def utc_plan_timestamp(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
