"""Populate the portal with a demo dataset (synthetic, fictitious). Usage: python scripts/seed_demo.py [n_instruments]"""
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import bank, services, release, scheduler, clock  # noqa: E402
from app.config import DATA_DIR  # noqa: E402
from app.db import init_db, SessionLocal  # noqa: E402
from app.models import Institution, Instrument, Document, User  # noqa: E402
from app.seed import ensure_users  # noqa: E402
from eval import synth  # noqa: E402

F = ("ref_no", "bank", "amount", "issue_date", "maturity_date", "lien")


def main(n: int = 36):
    init_db()
    db = SessionLocal()
    if db.query(Instrument).count():
        print("Database already has data; delete data/nidhisetu.db to re-seed.")
        return
    demo_dir = DATA_DIR / "demo_docs"
    ds = synth.generate(demo_dir, n=n, today=date.today())
    ids = {}
    for i in ds["institutions"]:
        row = Institution(pid=i["pid"], name=i["name"], state="Synthetic")
        db.add(row); db.flush(); ids[i["pid"]] = row.id
    db.commit()
    ensure_users(db)
    inst_user = db.query(User).filter_by(username="institution").one()
    inst_user.institution_id = list(ids.values())[0]
    db.commit()
    actor = db.query(User).filter_by(username="dealing_hand").one()
    ca = db.query(User).filter_by(username="competent_authority").one()
    for it in ds["items"]:
        t = it["bank_truth"]
        if t:
            bank.register_bank_record(db, t["ref_no"], t["bank"], t["amount"], date.fromisoformat(t["issue_date"]),
                                      date.fromisoformat(t["maturity_date"]), t["lien"])
    db.commit()
    made = []
    for it in ds["items"]:
        if it["pid"] not in ids:
            continue
        content = (demo_dir / it["file"]).read_bytes()
        inst = services.create_draft(db, actor, ids[it["pid"]], it["kind"], Path(it["file"]).name, content, run_ocr=False)
        for dt in ("affidavit_6", "loa_eoa"):
            if not (it["id"] % 7 == 0 and dt == "loa_eoa"):          # leave a few incomplete
                db.add(Document(instrument_id=inst.id, doc_type=dt, filename=f"{dt}.pdf", sha256="0" * 64))
        db.flush()
        services.confirm(db, actor, inst, {f: it[f] for f in F})
        made.append(inst)
    # one verified deposit maturing in ~58 days for the first institution (drives the T-60 renewal banner)
    first = list(ids.values())[0]
    ref = "40012345678"
    soon = date.today() + timedelta(days=58)
    bank.register_bank_record(db, ref, "State Bank of India", 2500000, soon - timedelta(days=365 * 8), soon, True)
    s0 = Instrument(institution_id=first, kind="FDR", d_ref_no=ref, d_bank="State Bank of India", d_amount=2500000,
                    d_issue_date=soon - timedelta(days=365 * 8), d_maturity_date=soon, d_lien=True, b_ref_no=ref,
                    b_bank="State Bank of India", b_amount=2500000, b_issue_date=soon - timedelta(days=365 * 8),
                    b_maturity_date=soon, b_lien=True, verification="VERIFIED", state="ACTIVE", bank_channel="mock-cbs-nesl")
    db.add(s0); db.commit()
    # a renewal chain
    verified = [i for i in made if i.verification == "VERIFIED" and i.state == "ACTIVE"]
    if verified:
        par = verified[0]
        child = Instrument(institution_id=par.institution_id, kind=par.kind, parent_id=par.id, d_ref_no="RN" + par.d_ref_no[2:],
                           d_bank=par.d_bank, d_amount=par.d_amount, d_issue_date=par.d_maturity_date,
                           d_maturity_date=par.d_maturity_date + timedelta(days=365 * 8), d_lien=True, verification="VERIFIED", state="ACTIVE")
        db.add(child); par.state = "RENEWED"; db.commit()
    # a release in flight and one completed
    today = date.today()
    matured = [i for i in made if i.verification == "VERIFIED" and i.state == "ACTIVE" and i.d_maturity_date < today]
    for k, inst in enumerate(matured[:2]):
        for dt in ("request_letter", "original_proof"):
            db.add(Document(instrument_id=inst.id, doc_type=dt, filename=f"{dt}.pdf", sha256="0" * 64))
        db.flush(); db.refresh(inst)
        req = release.open_request(db, actor, inst)
        for cell in ("pgrc", "vigilance", "grants") if k == 0 else ("pgrc",):
            release.set_clearance(db, actor, req, cell, True)
        if k == 0:
            release.decide(db, ca, inst, req, True, today)
    scheduler.run_daily(db, today)
    print(f"Seeded {db.query(Instrument).count()} instruments for {len(ids)} institutions.")
    print("Demo logins (passwordless in DEMO_MODE): institution, dealing_hand, scrutiny_officer, competent_authority, bank_nodal, admin")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 36)
