from datetime import datetime, date
from sqlalchemy import String, Integer, Float, Date, DateTime, Boolean, ForeignKey, Text, JSON, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

KINDS = ("FDR", "JOINT_FDR", "RPGF", "RTGS")


class Institution(Base):
    __tablename__ = "institutions"
    id: Mapped[int] = mapped_column(primary_key=True)
    pid: Mapped[str] = mapped_column(String(32), unique=True)      # AICTE Permanent Institute ID
    name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(60), default="")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(30))   # institution|dealing_hand|scrutiny_officer|competent_authority|bank_nodal|admin
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id"), nullable=True)
    display: Mapped[str] = mapped_column(String(120), default="")


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"))
    kind: Mapped[str] = mapped_column(String(12), default="FDR")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    # ---- Column A: institution-submitted (declared) values, confirmed by uploader
    d_ref_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    d_bank: Mapped[str | None] = mapped_column(String(120), nullable=True)
    d_amount: Mapped[float | None] = mapped_column(Numeric(16, 2, asdecimal=False), nullable=True)
    d_issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    d_maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    d_lien: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # ---- Column B: independent bank-verified values (never overwritten by Column A)
    b_ref_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    b_bank: Mapped[str | None] = mapped_column(String(120), nullable=True)
    b_amount: Mapped[float | None] = mapped_column(Numeric(16, 2, asdecimal=False), nullable=True)
    b_issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    b_maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    b_lien: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    bank_channel: Mapped[str | None] = mapped_column(String(30), nullable=True)
    bank_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # ---- workflow
    verification: Mapped[str] = mapped_column(String(20), default="DRAFT")
    # DRAFT | PENDING_BANK | VERIFIED | DISCREPANCY | MANUAL_REVIEW
    state: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    # ACTIVE | RENEWED | RELEASE_PENDING | RELEASED | REJECTED
    mismatches: Mapped[list] = mapped_column(JSON, default=list)
    missing_docs: Mapped[list] = mapped_column(JSON, default=list)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    doc_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    doc_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ocr: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    institution = relationship("Institution")
    documents = relationship("Document", back_populates="instrument", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="instrument", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    doc_type: Mapped[str] = mapped_column(String(30))   # fdr_scan|affidavit_6|loa_eoa|request_letter|original_proof|deposit_confirmation
    filename: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    instrument = relationship("Instrument", back_populates="documents")


class BankRecord(Base):
    """Simulated bank-side registry (stands in for CBS / NeSL e-BG APIs in the prototype)."""
    __tablename__ = "bank_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    ref_no: Mapped[str] = mapped_column(String(64), index=True)
    bank: Mapped[str] = mapped_column(String(120))
    amount: Mapped[float] = mapped_column(Numeric(16, 2, asdecimal=False))
    issue_date: Mapped[date] = mapped_column(Date)
    maturity_date: Mapped[date] = mapped_column(Date)
    lien: Mapped[bool] = mapped_column(Boolean, default=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    kind: Mapped[str] = mapped_column(String(20))      # T-90 | T-60 | T-30 | T-15 | OVERDUE
    channel: Mapped[str] = mapped_column(String(20), default="portal+email")
    recipient: Mapped[str] = mapped_column(String(120), default="institution")
    sent_on: Mapped[date] = mapped_column(Date)
    message: Mapped[str] = mapped_column(Text, default="")
    instrument = relationship("Instrument", back_populates="alerts")


class ReleaseRequest(Base):
    __tablename__ = "release_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    checklist: Mapped[dict] = mapped_column(JSON, default=dict)     # document flags
    clearances: Mapped[dict] = mapped_column(JSON, default=dict)    # pgrc/vigilance/grants -> bool|None
    state: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN|APPROVED|REJECTED
    order_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
