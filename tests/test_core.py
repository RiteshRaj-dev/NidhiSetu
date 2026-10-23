from datetime import date, timedelta
import pytest
from app import audit, bank, release, scheduler, services
from app.models import Institution, Instrument, Document, User, AuditLog, Alert
from app.normalize import parse_amount, parse_date, canon_bank, canon_ref
from app.reconcile import compare
from app.rules import get_rules


def mk(db, **kw):
    inst = db.query(Institution).first() or Institution(pid="p", name="n")
    db.add(inst); db.flush()
    base = dict(institution_id=inst.id, kind="FDR", d_ref_no="12345678901", d_bank="State Bank of India", d_amount=1500000,
                d_issue_date=date(2020, 1, 1), d_maturity_date=date.today() + timedelta(days=200), d_lien=True,
                verification="VERIFIED", state="ACTIVE")
    base.update(kw)
    i = Instrument(**base); db.add(i); db.commit(); return i


def actor(db, role="dealing_hand"):
    u = User(username=role + str(db.query(User).count()), password_hash="x", role=role); db.add(u); db.commit(); return u


def test_normalisation_formats():
    assert parse_amount("Rs. 35,00,000.00") == parse_amount("INR 3500000") == parse_amount("₹ 35,00,000/-") == 3500000.0
    assert parse_date("15-Aug-2034") == parse_date("15/08/2034") == parse_date("15 Aug 2034") == parse_date("2034-08-15") == date(2034, 8, 15)
    assert canon_bank("SBI") == canon_bank("State Bank of India") == canon_bank("S.B.I.")
    assert canon_ref("fdr-123 45") == "FDR12345"


def test_iso_dates_are_not_read_day_first():
    assert parse_date("2018-03-12") == date(2018, 3, 12)


def test_formatting_differences_are_not_discrepancies():
    d = {"ref_no": "FDR 123", "bank": "SBI", "amount": "35,00,000", "issue_date": date(2020, 1, 1), "maturity_date": date(2028, 1, 1), "lien": True}
    b = {"ref_no": "FDR123", "bank": "State Bank of India", "amount": 3500000.0, "issue_date": date(2020, 1, 1), "maturity_date": date(2028, 1, 1), "lien": True}
    assert compare({**d, "amount": 3500000.0}, b) == []


def test_amount_date_and_lien_mismatches_detected():
    d = {"ref_no": "A1", "bank": "HDFC Bank", "amount": 3500000.0, "issue_date": date(2020, 1, 1), "maturity_date": date(2034, 8, 15), "lien": True}
    b = {"ref_no": "A1", "bank": "HDFC Bank", "amount": 1500000.0, "issue_date": date(2020, 1, 1), "maturity_date": date(2028, 8, 15), "lien": False}
    assert {m["field"] for m in compare(d, b)} == {"amount", "maturity_date", "lien"}


def test_pipeline_flags_mismatch_and_keeps_both_columns(db):
    u = actor(db)
    inst = mk(db, verification="DRAFT", d_amount=None, d_ref_no=None)
    bank.register_bank_record(db, "FD998877", "SBI", 1500000, date(2020, 1, 1), date(2028, 1, 1), True)
    db.commit()
    services.confirm(db, u, inst, {"ref_no": "FD998877", "bank": "SBI", "amount": "35,00,000", "issue_date": "01-Jan-2020", "maturity_date": "01-Jan-2028", "lien": True})
    assert inst.verification == "DISCREPANCY"
    assert float(inst.d_amount) == 3500000 and float(inst.b_amount) == 1500000    # never overwritten
    assert [m["field"] for m in inst.mismatches] == ["amount"]


def test_unsupported_bank_goes_to_manual_review(db):
    u = actor(db)
    inst = mk(db, verification="DRAFT")
    services.confirm(db, u, inst, {"ref_no": "ZZ1", "bank": "The Example Co-operative Bank", "amount": 100000, "issue_date": "01-Jan-2020", "maturity_date": "01-Jan-2030", "lien": True})
    assert inst.verification == "MANUAL_REVIEW"


def test_audit_chain_detects_tampering(db):
    for i in range(5):
        audit.append(db, "u", "r", "act", "instrument", i, {"i": i})
    db.commit()
    assert audit.verify_chain(db)["valid"]
    row = db.query(AuditLog).filter_by(id=3).one()
    row.payload = {"i": 999}
    db.commit()
    res = audit.verify_chain(db)
    assert not res["valid"] and res["broken_at"] == 3


@pytest.mark.parametrize("days_left,expected", [(90, "T-90"), (60, "T-60"), (30, "T-30"), (15, "T-15"), (-1, "OVERDUE")])
def test_alert_fires_on_threshold_day_once(db, days_left, expected):
    inst = mk(db, d_maturity_date=date.today() + timedelta(days=days_left))
    got = scheduler.run_daily(db, date.today())
    assert [a.kind for a in got] == [expected]
    assert scheduler.run_daily(db, date.today()) == []        # idempotent


def test_no_alert_outside_window(db):
    mk(db, d_maturity_date=date.today() + timedelta(days=200))
    assert scheduler.run_daily(db, date.today()) == []


def test_lineage_keeps_parent_child_history(db):
    a = mk(db, d_ref_no="A")
    b = mk(db, d_ref_no="B", parent_id=a.id)
    c = mk(db, d_ref_no="C", parent_id=b.id)
    assert [x["ref_no"] for x in services.lineage(db, c)] == ["A", "B", "C"]
    assert [x["ref_no"] for x in services.lineage(db, a)] == ["A", "B", "C"]


def test_release_blocked_until_all_clauses_met(db):
    ca, dh = actor(db, "competent_authority"), actor(db)
    inst = mk(db, d_maturity_date=date.today() - timedelta(days=5))
    req = release.open_request(db, dh, inst)
    with pytest.raises(ValueError):
        release.decide(db, ca, inst, req, True, date.today())
    for dt in get_rules(db)["release_documents"]:
        db.add(Document(instrument_id=inst.id, doc_type=dt, filename="x", sha256="0"))
    db.commit(); db.refresh(inst)
    for cell in ("pgrc", "vigilance"):
        release.set_clearance(db, dh, req, cell, True)
    with pytest.raises(ValueError):                              # grants still pending
        release.decide(db, ca, inst, req, True, date.today())
    release.set_clearance(db, dh, req, "grants", True)
    release.decide(db, ca, inst, req, True, date.today())
    assert inst.state == "RELEASED" and req.order_ref.startswith("NOC/")


def test_release_blocked_before_maturity(db):
    ca, dh = actor(db, "competent_authority"), actor(db)
    inst = mk(db, d_maturity_date=date.today() + timedelta(days=10))
    req = release.open_request(db, dh, inst)
    for dt in get_rules(db)["release_documents"]:
        db.add(Document(instrument_id=inst.id, doc_type=dt, filename="x", sha256="0"))
    db.commit(); db.refresh(inst)
    for cell in ("pgrc", "vigilance", "grants"):
        release.set_clearance(db, dh, req, cell, True)
    with pytest.raises(ValueError, match="not matured"):
        release.decide(db, ca, inst, req, True, date.today())


def test_rtgs_gets_sanction_order(db):
    ca, dh = actor(db, "competent_authority"), actor(db)
    inst = mk(db, kind="RTGS", d_maturity_date=date.today() - timedelta(days=1))
    for dt in get_rules(db)["release_documents"]:
        db.add(Document(instrument_id=inst.id, doc_type=dt, filename="x", sha256="0"))
    db.commit(); db.refresh(inst)
    req = release.open_request(db, dh, inst)
    for cell in ("pgrc", "vigilance", "grants"):
        release.set_clearance(db, dh, req, cell, True)
    release.decide(db, ca, inst, req, True, date.today())
    assert req.order_type == "SANCTION_ORDER" and req.order_ref.startswith("SO/")
