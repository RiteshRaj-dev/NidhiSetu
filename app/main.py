from datetime import date, datetime
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from . import audit, bank, bulk, clock, forensics, notice, release, rules as rules_mod, scheduler, services, seed
from .config import BASE_DIR, DEMO_MODE, VAULT_DIR
from .db import get_db, init_db, SessionLocal
from .models import Institution, Instrument, ReleaseRequest, User, AuditLog, KINDS
from .normalize import parse_amount, parse_date
from .security import current_user, require, make_token, verify_password

app = FastAPI(title="NidhiSetu", version="1.0.0",
              description="Bank-verified security deposit and FDR lifecycle portal (SIH 2026, PS SIH26265)")


@app.on_event("startup")
def _startup():
    init_db()
    with SessionLocal() as db:
        seed.ensure_users(db)


def _inst(db: Session, iid: int, user: User) -> Instrument:
    inst = db.get(Instrument, iid)
    if not inst:
        raise HTTPException(404, "Instrument not found")
    if user.role == "institution" and user.institution_id != inst.institution_id:
        raise HTTPException(403, "Not your institution's instrument")
    return inst


def _ser(db, inst, with_ocr=False):
    return services.serialize(inst, clock.today(db), rules_mod.get_rules(db), with_ocr)


# ------------------------------------------------------------------ auth
class Login(BaseModel):
    username: str
    password: str | None = None


@app.post("/api/auth/login")
def login(body: Login, db: Session = Depends(get_db)):
    u = db.query(User).filter_by(username=body.username).one_or_none()
    if not u or not ((DEMO_MODE and body.password is None) or (body.password and verify_password(body.password, u.password_hash))):
        raise HTTPException(401, "Invalid credentials")
    return {"token": make_token(u), "role": u.role, "display": u.display, "institution_id": u.institution_id}


@app.get("/api/auth/demo-users")
def demo_users(db: Session = Depends(get_db)):
    if not DEMO_MODE:
        return []
    return [{"username": u.username, "role": u.role, "display": u.display} for u in db.query(User).all()]


# ------------------------------------------------------------------ reference data
@app.get("/api/institutions")
def institutions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = db.query(Institution)
    if user.role == "institution":
        q = q.filter(Institution.id == user.institution_id)
    return [{"id": i.id, "pid": i.pid, "name": i.name, "state": i.state} for i in q.order_by(Institution.name)]


@app.get("/api/rules")
def get_rules(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return rules_mod.get_rules(db)


@app.put("/api/rules")
def put_rules(patch: dict, db: Session = Depends(get_db), user: User = Depends(require("admin", "scrutiny_officer"))):
    r = rules_mod.set_rules(db, patch)
    audit.append(db, user.username, user.role, "rules.update", "rules", None, patch)
    db.commit()
    return r


# ------------------------------------------------------------------ instruments
@app.post("/api/instruments/upload")
async def upload(file: UploadFile = File(...), institution_id: int = Form(...), kind: str = Form("FDR"),
                 db: Session = Depends(get_db), user: User = Depends(require("institution", "dealing_hand"))):
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {KINDS}")
    if user.role == "institution" and user.institution_id != institution_id:
        raise HTTPException(403, "Cannot upload for another institution")
    content = await file.read()
    if not content or len(content) > 15_000_000:
        raise HTTPException(422, "Empty or too large (15 MB max)")
    inst = services.create_draft(db, user, institution_id, kind, file.filename or "upload.pdf", content)
    return _ser(db, inst, with_ocr=True)


class Confirm(BaseModel):
    ref_no: str | None = None
    bank: str | None = None
    amount: str | float | None = None
    issue_date: str | None = None
    maturity_date: str | None = None
    lien: bool | str | None = None


@app.post("/api/instruments/{iid}/confirm")
def confirm(iid: int, body: Confirm, db: Session = Depends(get_db), user: User = Depends(require("institution", "dealing_hand"))):
    inst = _inst(db, iid, user)
    services.confirm(db, user, inst, body.model_dump())
    return _ser(db, inst)


@app.post("/api/instruments/{iid}/documents")
async def add_doc(iid: int, doc_type: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db),
                  user: User = Depends(require("institution", "dealing_hand"))):
    inst = _inst(db, iid, user)
    services.add_document(db, user, inst, doc_type, file.filename or "doc.pdf", await file.read())
    return _ser(db, inst)


# ---- consolidated CSV export / bulk import (declared routes before /{iid} so they are not shadowed)
@app.get("/api/instruments/export.csv")
def export_csv(db: Session = Depends(get_db), user: User = Depends(require("dealing_hand", "scrutiny_officer", "competent_authority"))):
    today, r = clock.today(db), rules_mod.get_rules(db)
    rows = [services.serialize(i, today, r) for i in db.query(Instrument).order_by(Instrument.id).all()]
    return Response(bulk.export_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="nidhisetu_deposits_{today.isoformat()}.csv"'})


@app.post("/api/instruments/bulk-csv")
async def bulk_csv(file: UploadFile | None = File(None), sample: bool = Query(False), db: Session = Depends(get_db),
                   user: User = Depends(require("dealing_hand", "scrutiny_officer"))):
    name = "upload.csv"
    if sample:
        if not DEMO_MODE:
            raise HTTPException(403, "Sample import is available in demo mode only")
        text, bank_rows = bulk.sample_csv(clock.today(db))
        for b in bank_rows:     # stands in for the bank-side registry the demo rows are checked against
            bank.register_bank_record(db, b["ref_no"], b["bank"], float(b["amount"]), parse_date(b["issue_date"]),
                                      parse_date(b["maturity_date"]), True)
        db.commit()
        name = "sample-legacy.csv"
    else:
        if file is None:
            raise HTTPException(422, "Attach a CSV file")
        raw = await file.read()
        if not raw or len(raw) > 5_000_000:
            raise HTTPException(422, "Empty or too large (5 MB max)")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(422, "CSV must be UTF-8 encoded")
        name = file.filename or name
    try:
        return bulk.import_csv(db, user, text, name)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/instruments")
def list_instruments(category: str | None = None, institution_id: int | None = None, q: str | None = None,
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    qry = db.query(Instrument)
    if user.role == "institution":
        institution_id = user.institution_id
    if institution_id:
        qry = qry.filter(Instrument.institution_id == institution_id)
    today, r = clock.today(db), rules_mod.get_rules(db)
    out = [services.serialize(i, today, r) for i in qry.order_by(Instrument.id.desc()).all()]
    if category:
        out = [o for o in out if o["category"].lower() == category.lower()]
    if q:
        ql = q.lower()
        out = [o for o in out if ql in (o["declared"]["ref_no"] or "").lower() or ql in (o["institution"] or "").lower()]
    return out


@app.get("/api/instruments/{iid}")
def get_instrument(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    inst = _inst(db, iid, user)
    out = _ser(db, inst, with_ocr=True)
    out["lineage"] = services.lineage(db, inst)
    return out


@app.get("/api/instruments/{iid}/file")
def get_file(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    inst = _inst(db, iid, user)
    p = VAULT_DIR / (inst.doc_name or "")
    if not inst.doc_name or not p.exists():
        raise HTTPException(404, "No document")
    return FileResponse(p, media_type="application/pdf" if p.suffix == ".pdf" else None)


@app.get("/api/instruments/{iid}/preview")
def preview(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """First page of the stored document as PNG, so any browser can show it beside the OCR form."""
    import io
    from fastapi.responses import Response
    inst = _inst(db, iid, user)
    p = VAULT_DIR / (inst.doc_name or "")
    if not inst.doc_name or not p.exists():
        raise HTTPException(404, "No document")
    from .ocr import render_pages
    buf = io.BytesIO()
    render_pages(p, scale=1.4)[0].save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


class Scrutiny(BaseModel):
    note: str


@app.post("/api/instruments/{iid}/scrutiny")
def scrutiny(iid: int, body: Scrutiny, db: Session = Depends(get_db), user: User = Depends(require("scrutiny_officer", "dealing_hand"))):
    inst = _inst(db, iid, user)
    audit.append(db, user.username, user.role, "scrutiny.note", "instrument", inst.id, {"note": body.note})
    db.commit()
    return {"ok": True}


@app.post("/api/instruments/{iid}/renew")
async def renew(iid: int, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require("institution", "dealing_hand"))):
    parent = _inst(db, iid, user)
    child = services.renew(db, user, parent, file.filename or "renewal.pdf", await file.read())
    return _ser(db, child, with_ocr=True)


@app.get("/api/instruments/{iid}/suggest-parent")
def suggest_parent(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = services.suggest_parent(db, _inst(db, iid, user))
    return _ser(db, p) if p else None


# ------------------------------------------------------------------ bank webhook (nodal officer)
class BankConfirm(BaseModel):
    ref_no: str
    bank: str
    amount: float
    issue_date: str
    maturity_date: str
    lien: bool = True


@app.post("/api/bank/webhook")
def bank_webhook(body: BankConfirm, db: Session = Depends(get_db), user: User = Depends(require("bank_nodal"))):
    rec = bank.register_bank_record(db, body.ref_no, body.bank, body.amount, parse_date(body.issue_date),
                                    parse_date(body.maturity_date), body.lien)
    audit.append(db, user.username, user.role, "bank.webhook", "bank_record", rec.id, body.model_dump())
    db.commit()
    return {"ok": True, "id": rec.id}


@app.post("/api/instruments/{iid}/reverify")
def reverify(iid: int, db: Session = Depends(get_db), user: User = Depends(require("dealing_hand", "scrutiny_officer", "institution"))):
    inst = _inst(db, iid, user)
    services.run_bank_verification(db, user, inst)
    return _ser(db, inst)


# ------------------------------------------------------------------ dashboard, clock, alerts
@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(current_user)):
    today, r = clock.today(db), rules_mod.get_rules(db)
    rows = [services.serialize(i, today, r) for i in db.query(Instrument).all()]
    counts = {k: 0 for k in ("Active", "Expiring", "Matured", "Renewed", "Released", "Pending")}
    for x in rows:
        counts[x["category"]] = counts.get(x["category"], 0) + 1
    expiring = sorted([x for x in rows if x["category"] in ("Expiring", "Matured")], key=lambda x: x["days_to_maturity"] or 0)[:8]
    corpus = sum(float(x["declared"]["amount"] or 0) for x in rows if x["state"] in ("ACTIVE", "RELEASE_PENDING"))
    return {"today": today, "total": len(rows), "counts": counts, "discrepancies": counts["Pending"],
            "expiring": expiring, "pledged_corpus": corpus,
            "overdue_escalated": sum(1 for x in rows if x["escalated"])}


@app.get("/api/clock")
def get_clock(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"today": clock.today(db), "offset_days": clock.get_offset(db)}


class Advance(BaseModel):
    days: int | None = None
    set_offset: int | None = None


@app.post("/api/clock")
def advance(body: Advance, db: Session = Depends(get_db), user: User = Depends(require("admin", "scrutiny_officer"))):
    off = body.set_offset if body.set_offset is not None else clock.get_offset(db) + (body.days or 0)
    clock.set_offset(db, off)
    return {"today": clock.today(db), "offset_days": off}


@app.post("/api/scheduler/run")
def run_scheduler(db: Session = Depends(get_db), user: User = Depends(require("admin", "scrutiny_officer"))):
    created = scheduler.run_daily(db, clock.today(db), actor=user.username)
    return {"today": clock.today(db), "created": [{"instrument_id": a.instrument_id, "kind": a.kind, "message": a.message} for a in created]}


@app.get("/api/alerts")
def alerts(db: Session = Depends(get_db), user: User = Depends(current_user)):
    from .models import Alert
    rows = db.query(Alert).order_by(Alert.id.desc()).limit(100).all()
    return [{"id": a.id, "instrument_id": a.instrument_id, "kind": a.kind, "sent_on": a.sent_on, "recipient": a.recipient,
             "message": a.message} for a in rows]


# ------------------------------------------------------------------ release workflow
def _req(db, iid):
    return db.query(ReleaseRequest).filter_by(instrument_id=iid).order_by(ReleaseRequest.id.desc()).first()


@app.post("/api/instruments/{iid}/release")
def open_release(iid: int, db: Session = Depends(get_db), user: User = Depends(require("institution", "dealing_hand"))):
    inst = _inst(db, iid, user)
    if inst.state not in ("ACTIVE", "RELEASE_PENDING"):
        raise HTTPException(409, f"Instrument is {inst.state}")
    req = _req(db, iid) if inst.state == "RELEASE_PENDING" else release.open_request(db, user, inst)
    return {"request_id": req.id, **release.evaluate(db, inst, req, clock.today(db))}


@app.get("/api/instruments/{iid}/release")
def release_status(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    inst = _inst(db, iid, user)
    req = _req(db, iid)
    if not req:
        raise HTTPException(404, "No release request")
    sha_file = VAULT_DIR / f"order_{req.id}.sha256"
    return {"request_id": req.id, "state": req.state, "order_ref": req.order_ref, "order_type": req.order_type,
            "order_sha256": sha_file.read_text().strip() if req.state == "APPROVED" and sha_file.is_file() else None,
            **release.evaluate(db, inst, req, clock.today(db))}


@app.get("/api/instruments/{iid}/release/order-pdf")
def release_order_pdf(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _inst(db, iid, user)
    req = _req(db, iid)
    if not req or req.state != "APPROVED":
        raise HTTPException(409, "No approved release order for this instrument")
    path = VAULT_DIR / f"order_{req.id}.pdf"
    if not path.is_file():
        raise HTTPException(404, "Order file missing")
    return FileResponse(path, media_type="application/pdf", filename=f"{req.order_type}_{req.id}.pdf")


class Clearance(BaseModel):
    cell: str
    approve: bool


@app.post("/api/instruments/{iid}/release/clearance")
def clearance(iid: int, body: Clearance, db: Session = Depends(get_db), user: User = Depends(require("dealing_hand", "scrutiny_officer"))):
    inst = _inst(db, iid, user)
    req = _req(db, iid)
    if not req or body.cell not in rules_mod.get_rules(db)["release_clearances"]:
        raise HTTPException(404, "Unknown request or clearance cell")
    release.set_clearance(db, user, req, body.cell, body.approve)
    return release.evaluate(db, inst, req, clock.today(db))


class Decision(BaseModel):
    approve: bool


@app.post("/api/instruments/{iid}/release/decision")
def decision(iid: int, body: Decision, db: Session = Depends(get_db), user: User = Depends(require("competent_authority"))):
    inst = _inst(db, iid, user)
    req = _req(db, iid)
    if not req:
        raise HTTPException(404, "No release request")
    try:
        release.decide(db, user, inst, req, body.approve, clock.today(db))
    except ValueError as e:
        raise HTTPException(409, f"Release blocked: {e}")
    return {"state": req.state, "order_ref": req.order_ref, "order_type": req.order_type}


# ------------------------------------------------------------------ audit
@app.get("/api/audit")
def audit_list(limit: int = Query(50, le=500), db: Session = Depends(get_db), user: User = Depends(require("scrutiny_officer", "competent_authority", "dealing_hand"))):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    return [{"id": r.id, "ts": r.ts, "actor": r.actor, "role": r.role, "action": r.action, "entity_id": r.entity_id,
             "hash": r.hash, "prev_hash": r.prev_hash} for r in rows]


@app.get("/api/audit/verify")
def audit_verify(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return audit.verify_chain(db)


# ------------------------------------------------------------------ forensics (ELA), show-cause notice, manual resolution
@app.get("/api/instruments/{iid}/ela-preview")
def ela_preview(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Colourised Error Level Analysis heatmap of the first page of the stored document."""
    import cv2
    from .ocr import render_pages
    inst = _inst(db, iid, user)
    p = VAULT_DIR / (inst.doc_name or "")
    if not inst.doc_name or not p.is_file():
        raise HTTPException(404, "No document")
    ok, png = cv2.imencode(".png", forensics.ela_heatmap(render_pages(p, scale=1.4)[0]))
    return Response(png.tobytes(), media_type="image/png")


@app.get("/api/instruments/{iid}/discrepancy-notice.pdf")
def discrepancy_notice(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    inst = _inst(db, iid, user)
    if inst.verification != "DISCREPANCY":
        raise HTTPException(409, f"No discrepancy on this instrument (status {inst.verification})")
    pdf, digest = notice.build_pdf(inst, clock.today(db))
    audit.append(db, user.username, user.role, "discrepancy.notice", "instrument", inst.id, {"sha256": digest})
    db.commit()
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="show_cause_{inst.id}.pdf"',
                             "X-Notice-SHA256": digest})


class ManualResolve(BaseModel):
    note: str
    nodal_ref: str | None = None


@app.post("/api/instruments/{iid}/manual-resolve")
def manual_resolve(iid: int, body: ManualResolve, db: Session = Depends(get_db),
                   user: User = Depends(require("scrutiny_officer"))):
    inst = _inst(db, iid, user)
    if inst.verification != "MANUAL_REVIEW":
        raise HTTPException(409, f"Only MANUAL_REVIEW instruments can be resolved manually (status {inst.verification})")
    if not body.note.strip():
        raise HTTPException(422, "A confirmation note is required")
    inst.verification, inst.mismatches, inst.bank_channel = "VERIFIED", [], "manual-nodal-confirmation"
    inst.bank_checked_at = datetime.utcnow()      # Column A (d_*) is left untouched
    audit.append(db, user.username, user.role, "instrument.manual_resolve", "instrument", inst.id,
                 {"note": body.note.strip(), "nodal_ref": body.nodal_ref, "bank": inst.d_bank})
    db.commit()
    return _ser(db, inst)


# ------------------------------------------------------------------ frontend
FRONT = BASE_DIR / "frontend"
if FRONT.exists():
    app.mount("/static", StaticFiles(directory=FRONT), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONT / "index.html")
