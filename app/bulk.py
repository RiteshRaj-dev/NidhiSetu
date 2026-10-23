"""CSV bulk import (legacy records) and consolidated CSV export for the Regulation Bureau."""
import csv
import io
from datetime import timedelta
from sqlalchemy.orm import Session
from . import audit, bank, services
from .models import Institution, Instrument, User, KINDS
from .normalize import parse_amount, parse_date, parse_bool, canon_bank

MAX_ROWS = 5000
IMPORT_COLUMNS = ["pid", "institution", "kind", "ref_no", "bank", "amount", "issue_date", "maturity_date", "lien"]
EXPORT_HEADER = ["PID", "Institution", "Kind", "Declared Ref", "Declared Bank", "Declared Amount", "Declared Issue",
                 "Declared Maturity", "Declared Lien", "Bank-Verified Ref", "Bank-Verified Bank", "Bank-Verified Amount",
                 "Bank-Verified Maturity", "Bank-Verified Lien", "Verification", "Category", "Days to Maturity"]


def _safe(v):
    """Neutralise spreadsheet formula injection in exported cells."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _lien(v):
    return "" if v is None else ("Yes" if v else "No")


def export_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(EXPORT_HEADER)
    for r in rows:
        d, b = r["declared"], r["bank"]
        w.writerow([_safe(x) for x in (
            r["pid"], r["institution"], r["kind"], d["ref_no"], d["bank"], d["amount"], d["issue_date"], d["maturity_date"],
            _lien(d["lien"]), b["ref_no"], b["bank"], b["amount"], b["maturity_date"], _lien(b["lien"]),
            r["verification"], r["category"], r["days_to_maturity"])])
    return out.getvalue()


def sample_csv(today) -> tuple[str, list[dict]]:
    """Four legacy rows covering every verification outcome, plus the bank-side records that back them."""
    def row(ref, bk, amt, mat_days, inst="Sample Legacy Institute of Technology", pid="1-LEGACY"):
        mat = today + timedelta(days=mat_days)
        return {"pid": pid, "institution": inst, "kind": "FDR", "ref_no": ref, "bank": bk, "amount": amt,
                "issue_date": (mat - timedelta(days=365 * 8)).isoformat(), "maturity_date": mat.isoformat(), "lien": "Yes"}
    rows = [row("LEG0001", "State Bank of India", 3500000, 25), row("LEG0002", "HDFC Bank", 2000000, 70),
            row("LEG0003", "Canara Bank", 5000000, -12), row("LEG0004", "The Example Co-operative Bank", 1000000, 200)]
    banks = [dict(r) for r in rows[:3]]
    banks[2]["amount"] = 4500000        # LEG0003: bank says a smaller amount -> discrepancy
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=IMPORT_COLUMNS)
    w.writeheader()
    w.writerows(rows)
    return out.getvalue(), banks


def import_csv(db: Session, user: User, text: str, filename: str = "upload.csv") -> dict:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    cols = {(c or "").strip().lower() for c in (reader.fieldnames or [])}
    missing = [c for c in ("pid", "ref_no", "bank", "amount", "maturity_date") if c not in cols]
    if missing:
        raise ValueError("CSV is missing required column(s): " + ", ".join(missing))
    created, skipped, errors = [], [], []
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_ROWS:
            errors.append({"row": n, "error": f"row limit {MAX_ROWS} reached"})
            break
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            kind = (r.get("kind") or "FDR").upper()
            if kind not in KINDS:
                raise ValueError(f"kind must be one of {KINDS}")
            if not r["pid"] or not r["ref_no"]:
                raise ValueError("pid and ref_no are required")
            amt, mat = parse_amount(r["amount"]), parse_date(r["maturity_date"])
            if amt is None or mat is None:
                raise ValueError("unreadable amount or maturity_date")
            iss = parse_date(r.get("issue_date")) if r.get("issue_date") else None
            inst_row = db.query(Institution).filter_by(pid=r["pid"]).one_or_none()
            if inst_row is None:
                inst_row = Institution(pid=r["pid"], name=r.get("institution") or r["pid"], state="Imported")
                db.add(inst_row)
                db.flush()
            if db.query(Instrument).filter_by(institution_id=inst_row.id, d_ref_no=r["ref_no"]).first():
                skipped.append({"row": n, "ref_no": r["ref_no"], "reason": "already exists"})
                continue
            inst = Instrument(institution_id=inst_row.id, kind=kind, d_ref_no=r["ref_no"],
                              d_bank=canon_bank(r["bank"]) if r["bank"] else None, d_amount=amt, d_issue_date=iss,
                              d_maturity_date=mat, d_lien=parse_bool(r.get("lien")) if r.get("lien") else None,
                              verification="DRAFT", doc_name=None)
            db.add(inst)
            db.flush()
            services.refresh_completeness(db, inst)
            if not inst.missing_fields:
                services.run_bank_verification(db, user, inst)
            db.commit()
            created.append({"row": n, "id": inst.id, "ref_no": r["ref_no"], "verification": inst.verification})
        except ValueError as e:
            db.rollback()
            errors.append({"row": n, "error": str(e)})
    outcome: dict[str, int] = {}
    for c in created:
        outcome[c["verification"]] = outcome.get(c["verification"], 0) + 1
    audit.append(db, user.username, user.role, "instrument.bulk_import", "instrument", None,
                 {"file": filename, "created": len(created), "skipped": len(skipped), "errors": len(errors), "outcomes": outcome})
    db.commit()
    return {"created": len(created), "skipped": skipped, "errors": errors, "outcomes": outcome, "instruments": created}
