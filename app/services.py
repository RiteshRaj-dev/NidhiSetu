"""Core pipeline: upload -> OCR pre-fill -> uploader confirms -> completeness -> bank verify -> reconcile."""
import hashlib
from datetime import date, datetime
from pathlib import Path
from sqlalchemy.orm import Session
from . import audit, bank, ocr
from .config import VAULT_DIR
from .models import Instrument, Document, User
from .normalize import parse_amount, parse_date, parse_bool, canon_bank
from .reconcile import compare
from .rules import get_rules

DECLARED = ("ref_no", "bank", "amount", "issue_date", "maturity_date", "lien")


def store_file(content: bytes, filename: str) -> tuple[str, str]:
    sha = hashlib.sha256(content).hexdigest()
    safe = Path(filename).name.replace(" ", "_")
    (VAULT_DIR / f"{sha[:16]}_{safe}").write_bytes(content)
    return f"{sha[:16]}_{safe}", sha


def vault_path(inst_or_doc_name: str) -> Path:
    return VAULT_DIR / inst_or_doc_name


def create_draft(db: Session, user: User, institution_id: int, kind: str, filename: str, content: bytes,
                 run_ocr: bool = True) -> Instrument:
    name, sha = store_file(content, filename)
    inst = Instrument(institution_id=institution_id, kind=kind, doc_name=name, doc_sha256=sha, verification="DRAFT")
    db.add(inst)
    db.flush()
    db.add(Document(instrument_id=inst.id, doc_type="fdr_scan", filename=name, sha256=sha))
    if run_ocr:
        inst.ocr = ocr.extract_fields(vault_path(name))
    audit.append(db, user.username, user.role, "instrument.upload", "instrument", inst.id,
                 {"kind": kind, "sha256": sha, "ocr_fields": (inst.ocr or {}).get("fields")})
    db.commit()
    return inst


def add_document(db: Session, user: User, inst: Instrument, doc_type: str, filename: str, content: bytes) -> Document:
    name, sha = store_file(content, filename)
    doc = Document(instrument_id=inst.id, doc_type=doc_type, filename=name, sha256=sha)
    db.add(doc)
    db.flush()
    refresh_completeness(db, inst)
    audit.append(db, user.username, user.role, "document.upload", "instrument", inst.id, {"doc_type": doc_type, "sha256": sha})
    db.commit()
    return doc


def refresh_completeness(db: Session, inst: Instrument) -> None:
    rules = get_rules(db)
    have = {d.doc_type for d in inst.documents}
    inst.missing_docs = [d for d in rules["required_docs_submission"] if d not in have]
    inst.missing_fields = [f for f in rules["required_fields"] if getattr(inst, "d_" + f) in (None, "")]


def confirm(db: Session, user: User, inst: Instrument, fields: dict, adapter: str = "mock-cbs-nesl") -> Instrument:
    """Uploader confirms (or corrects) OCR values. Writes Column A only, then runs bank verification."""
    inst.d_ref_no = (fields.get("ref_no") or "").strip() or None
    b = fields.get("bank")
    inst.d_bank = canon_bank(b) if b else None
    inst.d_amount = parse_amount(fields.get("amount"))
    inst.d_issue_date = parse_date(fields.get("issue_date"))
    inst.d_maturity_date = parse_date(fields.get("maturity_date"))
    inst.d_lien = parse_bool(fields.get("lien"))
    refresh_completeness(db, inst)
    audit.append(db, user.username, user.role, "instrument.confirm", "instrument", inst.id,
                 {"declared": {k: str(getattr(inst, "d_" + k)) for k in DECLARED}})
    if inst.missing_fields:
        inst.verification = "DRAFT"
        db.commit()
        return inst
    return run_bank_verification(db, user, inst, adapter)


def run_bank_verification(db: Session, user: User, inst: Instrument, adapter: str = "mock-cbs-nesl") -> Instrument:
    rules = get_rules(db)
    res = bank.get_adapter(adapter).verify(db, inst.d_ref_no, inst.d_bank, rules)
    inst.bank_channel, inst.bank_checked_at = res.channel, datetime.utcnow()
    if res.status == "manual_review":
        inst.verification, inst.mismatches = "MANUAL_REVIEW", []
    elif res.status == "not_found":
        inst.verification = "DISCREPANCY"
        inst.mismatches = [{"field": "bank_record", "declared": inst.d_ref_no, "bank": None}]
        for f in DECLARED:
            setattr(inst, "b_" + f, None)
    else:
        v = res.values
        for f in DECLARED:
            setattr(inst, "b_" + f, v[f])
        declared = {f: getattr(inst, "d_" + f) for f in DECLARED}
        inst.mismatches = compare(declared, v)
        inst.verification = "DISCREPANCY" if inst.mismatches else "VERIFIED"
    audit.append(db, user.username, user.role, "bank.verify", "instrument", inst.id,
                 {"channel": res.channel, "outcome": inst.verification, "mismatches": inst.mismatches})
    db.commit()
    return inst


def suggest_parent(db: Session, inst: Instrument) -> Instrument | None:
    """Propose the expiring/matured instrument this one most likely renews or replaces."""
    if not inst.d_issue_date:
        return None
    cands = (db.query(Instrument)
             .filter(Instrument.institution_id == inst.institution_id, Instrument.id != inst.id,
                     Instrument.state == "ACTIVE", Instrument.d_maturity_date.isnot(None)).all())
    best, best_gap = None, None
    for c in cands:
        if c.kind != inst.kind and not {c.kind, inst.kind} <= {"FDR", "JOINT_FDR"}:
            continue
        if c.d_amount and inst.d_amount and float(inst.d_amount) < float(c.d_amount):
            continue    # a renewal never reduces the pledged corpus
        gap = abs((inst.d_issue_date - c.d_maturity_date).days)
        if gap <= 120 and (best_gap is None or gap < best_gap):
            best, best_gap = c, gap
    return best


def renew(db: Session, user: User, parent: Instrument, filename: str, content: bytes, fields: dict | None = None) -> Instrument:
    child = create_draft(db, user, parent.institution_id, parent.kind, filename, content,
                         run_ocr=fields is None)
    child.parent_id = parent.id
    data = fields or (child.ocr or {}).get("fields", {})
    confirm(db, user, child, data)
    parent.state = "RENEWED"
    audit.append(db, user.username, user.role, "instrument.renewed", "instrument", parent.id, {"child_id": child.id})
    db.commit()
    return child


def lineage(db: Session, inst: Instrument) -> list[dict]:
    chain = []
    cur = inst
    while cur.parent_id:
        cur = db.get(Instrument, cur.parent_id)
        chain.insert(0, cur)
    chain.append(inst)
    nxt = db.query(Instrument).filter(Instrument.parent_id == inst.id).first()
    while nxt:
        chain.append(nxt)
        nxt = db.query(Instrument).filter(Instrument.parent_id == nxt.id).first()
    return [{"id": c.id, "ref_no": c.d_ref_no, "maturity_date": c.d_maturity_date, "state": c.state} for c in chain]


# --------------------------------------------------------------- derived status
def days_to_maturity(inst: Instrument, today: date) -> int | None:
    return (inst.d_maturity_date - today).days if inst.d_maturity_date else None


def validity(inst: Instrument, today: date, rules: dict) -> str:
    d = days_to_maturity(inst, today)
    if d is None:
        return "UNKNOWN"
    if d < 0:
        return "MATURED"
    return "EXPIRING" if d <= rules["expiring_window_days"] else "ACTIVE"


def category(inst: Instrument, today: date, rules: dict) -> str:
    """Dashboard bucket: Active | Expiring | Matured | Renewed | Released | Pending."""
    if inst.state == "RELEASED":
        return "Released"
    if inst.state == "RENEWED":
        return "Renewed"
    if inst.verification in ("DRAFT", "PENDING_BANK", "DISCREPANCY", "MANUAL_REVIEW"):
        return "Pending"
    return validity(inst, today, rules).title()


def serialize(inst: Instrument, today: date, rules: dict, with_ocr: bool = False) -> dict:
    d = {f: getattr(inst, "d_" + f) for f in DECLARED}
    b = {f: getattr(inst, "b_" + f) for f in DECLARED}
    out = {
        "id": inst.id, "institution_id": inst.institution_id,
        "institution": inst.institution.name if inst.institution else None,
        "pid": inst.institution.pid if inst.institution else None,
        "kind": inst.kind, "parent_id": inst.parent_id, "declared": d, "bank": b,
        "verification": inst.verification, "state": inst.state, "validity": validity(inst, today, rules),
        "category": category(inst, today, rules), "days_to_maturity": days_to_maturity(inst, today),
        "mismatches": inst.mismatches or [], "missing_docs": inst.missing_docs or [],
        "missing_fields": inst.missing_fields or [], "bank_channel": inst.bank_channel,
        "escalated": inst.escalated, "doc_sha256": inst.doc_sha256,
        "documents": [{"type": x.doc_type, "filename": x.filename, "sha256": x.sha256} for x in inst.documents],
        "alerts": [{"kind": a.kind, "sent_on": a.sent_on, "recipient": a.recipient, "message": a.message} for a in inst.alerts],
    }
    if with_ocr:
        out["ocr"] = inst.ocr
    return out
