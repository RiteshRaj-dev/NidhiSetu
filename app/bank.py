"""Pluggable bank verification gateway. The prototype ships a simulated CBS/NeSL adapter backed by the
bank_records table plus a nodal-officer webhook; production adapters implement the same interface."""
from dataclasses import dataclass
from typing import Protocol
from sqlalchemy.orm import Session
from .models import BankRecord
from .normalize import canon_ref, canon_bank


@dataclass
class BankResult:
    status: str                 # confirmed | not_found | manual_review
    channel: str
    values: dict | None = None  # ref_no, bank, amount, issue_date, maturity_date, lien


class BankAdapter(Protocol):
    name: str

    def verify(self, db: Session, ref_no: str | None, bank: str | None, rules: dict) -> BankResult: ...


class MockCbsNeslAdapter:
    name = "mock-cbs-nesl"

    def verify(self, db: Session, ref_no, bank, rules) -> BankResult:
        cb = canon_bank(bank) if bank else None
        if cb and cb in [canon_bank(b) for b in rules.get("unsupported_banks", [])]:
            return BankResult("manual_review", "manual-nodal-officer")
        rec = db.query(BankRecord).filter(BankRecord.ref_no == canon_ref(ref_no)).first() if ref_no else None
        if not rec:
            return BankResult("not_found", self.name)
        return BankResult("confirmed", self.name, {
            "ref_no": rec.ref_no, "bank": rec.bank, "amount": float(rec.amount),
            "issue_date": rec.issue_date, "maturity_date": rec.maturity_date, "lien": rec.lien})


ADAPTERS: dict[str, BankAdapter] = {"mock-cbs-nesl": MockCbsNeslAdapter()}


def get_adapter(name: str = "mock-cbs-nesl") -> BankAdapter:
    return ADAPTERS[name]


def register_bank_record(db: Session, ref_no, bank, amount, issue_date, maturity_date, lien=True) -> BankRecord:
    """Called by the nodal-officer webhook (or the seeder) to publish a bank-side confirmation."""
    rec = db.query(BankRecord).filter(BankRecord.ref_no == canon_ref(ref_no)).first()
    if rec is None:
        rec = BankRecord(ref_no=canon_ref(ref_no))
        db.add(rec)
    rec.bank, rec.amount, rec.issue_date, rec.maturity_date, rec.lien = canon_bank(bank), amount, issue_date, maturity_date, lien
    db.flush()
    return rec
