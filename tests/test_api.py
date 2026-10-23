import io
import shutil
from datetime import date, timedelta
import pytest
from eval import synth

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")


def _doc(tmp_path, **over):
    item = {"id": 1, "institution": "Test Institute", "layout": 0, "fmt": 0, "bank_printed": "State Bank of India",
            "ref_no": "31245678901", "amount": 3500000, "issue_date": "2018-08-15", "maturity_date": (date.today() + timedelta(days=45)).isoformat(),
            "lien": True}
    item.update(over)
    p = tmp_path / "fdr.pdf"
    import random
    synth.draw_receipt(p, item, random.Random(1))
    return p, item


def test_rbac_blocks_wrong_role(client):
    h = client.login("institution")
    assert client.post("/api/clock", json={"days": 5}, headers=h).status_code == 403
    assert client.get("/api/audit", headers=h).status_code == 403
    assert client.get("/api/dashboard").status_code == 401


def test_full_flow_verified_then_discrepancy(client, tmp_path, db):
    from app import bank
    inst_h, bank_h = client.login("institution"), client.login("bank_nodal")
    p, item = _doc(tmp_path)
    bank.register_bank_record(db, item["ref_no"], "State Bank of India", 3500000, date(2018, 8, 15), date.fromisoformat(item["maturity_date"]), True)
    db.commit()
    r = client.post("/api/instruments/upload", files={"file": ("fdr.pdf", p.read_bytes(), "application/pdf")},
                    data={"institution_id": client.inst_id, "kind": "FDR"}, headers=inst_h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ocr"]["fields"]["ref_no"] == item["ref_no"]            # OCR pre-fill
    f = d["ocr"]["fields"]
    r = client.post(f"/api/instruments/{d['id']}/confirm", json=f, headers=inst_h).json()
    assert r["verification"] == "VERIFIED" and r["category"] == "Expiring"
    # now the bank publishes a different amount -> re-verify flags it
    client.post("/api/bank/webhook", json={"ref_no": item["ref_no"], "bank": "SBI", "amount": 1500000, "issue_date": "2018-08-15", "maturity_date": item["maturity_date"], "lien": True}, headers=bank_h)
    r = client.post(f"/api/instruments/{d['id']}/reverify", headers=inst_h).json()
    assert r["verification"] == "DISCREPANCY" and r["mismatches"][0]["field"] == "amount"
    assert r["declared"]["amount"] == 3500000 and r["bank"]["amount"] == 1500000
    assert client.get("/api/audit/verify", headers=inst_h).json()["valid"]


def test_institution_cannot_see_other_institution(client, db):
    from app.models import Institution
    other = Institution(pid="1-OTHER", name="Other"); db.add(other); db.commit()
    h = client.login("institution")
    r = client.post("/api/instruments/upload", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}, data={"institution_id": other.id}, headers=h)
    assert r.status_code == 403
