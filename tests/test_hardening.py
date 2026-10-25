"""Regression tests for the loopholes found in the security review (state locks, scoping, upload validation, rules)."""
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from tests.test_features import _matured_verified

ROOT = Path(__file__).resolve().parent.parent


def _released(client, db):
    inst = _matured_verified(db, client)
    dh, ca = client.login("dealing_hand"), client.login("competent_authority")
    client.post(f"/api/instruments/{inst.id}/release", headers=dh)
    for c in ("pgrc", "vigilance", "grants"):
        client.post(f"/api/instruments/{inst.id}/release/clearance", json={"cell": c, "approve": True}, headers=dh)
    assert client.post(f"/api/instruments/{inst.id}/release/decision", json={"approve": True}, headers=ca).status_code == 200
    return inst, dh, ca


def test_approved_release_cannot_be_reversed_or_reedited(client, db):
    inst, dh, ca = _released(client, db)
    assert client.post(f"/api/instruments/{inst.id}/release/decision", json={"approve": False}, headers=ca).status_code == 409
    assert client.post(f"/api/instruments/{inst.id}/release/decision", json={"approve": True}, headers=ca).status_code == 409
    assert client.post(f"/api/instruments/{inst.id}/release/clearance", json={"cell": "pgrc", "approve": False}, headers=dh).status_code == 409
    body = {"ref_no": "X1", "bank": "SBI", "amount": "5", "issue_date": "2020-01-01", "maturity_date": "2030-01-01", "lien": False}
    assert client.post(f"/api/instruments/{inst.id}/confirm", json=body, headers=client.login("institution")).status_code == 409
    assert client.get(f"/api/instruments/{inst.id}", headers=dh).json()["state"] == "RELEASED"
    pdf = client.post(f"/api/instruments/{inst.id}/documents", data={"doc_type": "affidavit_6"},
                      files={"file": ("a.pdf", b"x", "application/pdf")}, headers=dh)
    assert pdf.status_code == 409
    assert client.post(f"/api/instruments/{inst.id}/renew", files={"file": ("r.pdf", b"%PDF-1.4", "application/pdf")}, headers=dh).status_code == 409


def test_institution_dashboard_and_alerts_are_scoped(client, db):
    from app.models import Institution, Instrument
    other = Institution(pid="1-OTHER", name="Other Inst"); db.add(other); db.commit()
    db.add(Instrument(institution_id=other.id, kind="FDR", d_ref_no="SECRET99", d_bank="SBI", d_amount=1,
                      d_maturity_date=date.today() + timedelta(days=5), verification="VERIFIED"))
    db.commit()
    client.post("/api/scheduler/run", headers=client.login("scrutiny_officer"))
    h = client.login("institution")
    assert "SECRET99" not in str(client.get("/api/dashboard", headers=h).json())
    assert "SECRET99" not in str(client.get("/api/alerts", headers=h).json())
    assert "SECRET99" in str(client.get("/api/alerts", headers=client.login("scrutiny_officer")).json())   # bureau still sees it
    assert client.get("/api/dashboard", headers=h).json()["total"] == 0


def test_upload_validation(client):
    dh = client.login("dealing_hand")
    data = {"institution_id": client.inst_id, "kind": "FDR"}
    up = lambda name, body: client.post("/api/instruments/upload", files={"file": (name, body, "application/octet-stream")}, data=data, headers=dh)
    assert up("evil.html", b"<script>alert(1)</script>").status_code == 422
    assert up("fake.pdf", b"<script>alert(1)</script>").status_code == 422        # extension lies
    assert up("empty.pdf", b"").status_code == 422
    r = up("broken.pdf", b"%PDF-1.4 this is not a real pdf")                       # right magic, unreadable
    assert r.status_code == 422 and "could not be read" in r.json()["detail"]
    assert client.get("/api/instruments", headers=dh).json() == []                 # nothing half-created


def test_document_type_whitelist(client, db):
    inst = _matured_verified(db, client)
    dh = client.login("dealing_hand")
    bad = client.post(f"/api/instruments/{inst.id}/documents", data={"doc_type": "anything_goes"}, files={"file": ("a.pdf", b"x", "application/pdf")}, headers=dh)
    assert bad.status_code == 422
    exe = client.post(f"/api/instruments/{inst.id}/documents", data={"doc_type": "affidavit_6"}, files={"file": ("a.exe", b"x", "application/pdf")}, headers=dh)
    assert exe.status_code == 422


def test_rules_validation_protects_scheduler(client):
    so = client.login("scrutiny_officer")
    for bad in ({"reminder_offsets_days": "abc"}, {"reminder_offsets_days": []}, {"reminder_offsets_days": [0]},
                {"nonsense": 1}, {"order_type": {"FDR": "FREE_MONEY"}}, {"expiring_window_days": "x"}):
        assert client.put("/api/rules", json=bad, headers=so).status_code == 422, bad
    assert client.put("/api/rules", json={"reminder_offsets_days": [90, 45, 7]}, headers=so).status_code == 200
    assert client.post("/api/scheduler/run", headers=so).status_code == 200


def test_bank_webhook_cannot_reassign_a_reference_to_another_bank(client):
    bn = client.login("bank_nodal")
    b = {"ref_no": "RF1234567", "bank": "State Bank of India", "amount": 100, "issue_date": "2018-01-01", "maturity_date": "2027-01-01", "lien": True}
    assert client.post("/api/bank/webhook", json=b, headers=bn).status_code == 200
    b2 = dict(b, amount=200)                                              # same bank may amend its own record
    assert client.post("/api/bank/webhook", json=b2, headers=bn).status_code == 200
    assert client.post("/api/bank/webhook", json=dict(b, bank="HDFC Bank"), headers=bn).status_code == 409
    log = client.get("/api/audit?limit=20", headers=client.login("scrutiny_officer")).json()
    assert sum(1 for a in log if a["action"] == "bank.webhook") == 2


def test_refuses_to_start_without_real_secret_outside_demo_mode():
    code = "import asyncio, app.main as m; asyncio.run(m._startup())"
    env = {"DEMO_MODE": "0", "NIDHISETU_DATA": "/tmp/ns_guard", "PATH": "/usr/bin:/bin", "NIDHISETU_NO_SCHEDULER": "1"}
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**env}, capture_output=True, text=True)
    assert r.returncode != 0 and "JWT_SECRET" in r.stderr
    ok = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**env, "JWT_SECRET": "x" * 40}, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
