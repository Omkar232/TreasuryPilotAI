from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base

def now_utc(): return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    role: Mapped[str] = mapped_column(String(32), default="operator")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Obligation(Base):
    __tablename__ = "obligations"
    __table_args__ = (Index("ix_obligations_status_due", "status", "due_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(String(24), default="NORMAL")
    status: Mapped[str] = mapped_column(String(24), default="OPEN")
    source: Mapped[str] = mapped_column(String(32), default="operator_entered")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)

class PaymentIncident(Base):
    __tablename__ = "payment_incidents"
    __table_args__ = (Index("ix_incident_transfer_id", "original_transfer_id"), Index("ix_incident_state", "state"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    supplier: Mapped[str] = mapped_column(String(200))
    original_transfer_id: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3))
    supplier_claim: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), default="NEW")
    external_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="unverified")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    evidence: Mapped[list["IncidentEvidence"]] = relationship(back_populates="incident", cascade="all, delete-orphan")

class IncidentEvidence(Base):
    __tablename__ = "incident_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("payment_incidents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    incident: Mapped[PaymentIncident] = relationship(back_populates="evidence")

class TreasuryPolicy(Base):
    __tablename__ = "treasury_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), unique=True)
    reserve_floor: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    approval_threshold: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    priority_order: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

class TreasuryPlan(Base):
    __tablename__ = "treasury_plans"
    __table_args__ = (Index("ix_treasury_plans_created_at", "created_at"), Index("ix_treasury_plans_status", "status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    balance_snapshot: Mapped[list] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("payment_incidents.id"), index=True)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("agent_executions.id"), nullable=True, index=True)
    action_hash: Mapped[str] = mapped_column(String(64))
    counterparty: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3))
    evidence_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(nullable=True)

class AgentExecution(Base):
    __tablename__ = "agent_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("payment_incidents.id"), index=True)
    operation: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    external_transfer_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_parameters: Mapped[dict] = mapped_column(JSON)
    response_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("operation_key", name="uq_operation_key"),
        Index("uq_idempotency_original_transfer_id", "original_transfer_id", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(128))
    original_transfer_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("payment_incidents.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    parameters_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_created_at", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(320))
    event_type: Mapped[str] = mapped_column(String(64))
    incident_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    external_transfer_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    previous_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
