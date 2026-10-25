"""Tests for: order PDF download, CSV export / bulk import, ELA forensics, show-cause notice, manual resolution,
password-hash guard. These sit on top of (and do not replace) test_core.py and test_api.py."""
import hashlib
import io
import random
import shutil
from datetime import date, timedelta
import pytest
from PIL import Image, ImageDraw, ImageFont
from eval import synth


def _jpeg(im, q):
    b = io.BytesIO(); im.save(b, "JPEG", quality=q); b.seek(0)
    return Image.open(b).convert("RGB")


def _synth_pdf(tmp_path, n=1):
    item = {"id": n, "institution": "Test", "layout": 0, "fmt": 0, "bank_printed": "State Bank of India", "ref_no": "31245678901",
            "amount": 3500000, "issue_date": "2018-08-15", "maturity_date": "2027-01-01", "lien": True}
    p = tmp_path / f"f{n}.pdf"
    synth.draw_receipt(p, item, random.Random(n))
    return p


# ------------------------------------------------------------------ password guard
def test_verify_password_rejects_malformed_hash():
    from app.security import verify_password, hash_password
    assert verify_password("x", "no-dollar-sign") is False
    assert verify_password("x", "zz$abcd") is False            # non-hex salt must not raise
    assert verify_password("pw", hash_password("pw")) is True


def test_login_with_malformed_stored_hash_is_401_not_500(client, db):
    from app.models import User
    db.query(User).filter_by(username="admin").one().password_hash = "garbage"
    db.commit()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 401


# ------------------------------------------------------------------ forensics / ELA
def test_ela_scores_clean_low_and_pasted_amount_suspect(tmp_path):
    from app import forensics
    from app.ocr import render_pages
    orig = render_pages(_synth_pdf(tmp_path))[0]
    scan = _jpeg(_jpeg(orig, 85), 85)                      # a scan that has been re-saved
    w, h = scan.size
    box = (int(w * .45), int(h * .30), int(w * .80), int(h * .34))
    patch = orig.crop(box)
    d = ImageDraw.Draw(patch)
    d.rectangle((0, 0, patch.width, patch.height), fill="white")
    d.text((10, 8), "Rs. 95,00,000.00", fill="black", font=ImageFont.load_default(size=40))
    forged = scan.copy(); forged.paste(patch, box)
    clean_r, forged_r = forensics.analyse(_jpeg(scan, 90)), forensics.analyse(_jpeg(forged, 90))
    assert clean_r["suspect"] is False and forged_r["suspect"] is True
    assert 0 <= clean_r["tamper_score"] < forged_r["tamper_score"] <= 100


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_ocr_result_carries_forensics_and_keeps_existing_keys(tmp_path):
    from app import ocr
    out = ocr.extract_fields(_synth_pdf(tmp_path))
    for k in ("engine", "deskew_angle", "second_pass", "fields", "confidence", "raw_text"):
        assert k in out
    assert set(out["forensics"]) == {"tamper_score", "suspect"}


def _draft(db, tmp_path, client):
    from app import services
    from app.models import User
    user = db.query(User).filter_by(username="dealing_hand").one()
    p = _synth_pdf(tmp_path)
    return services.create_draft(db, user, client.inst_id, "FDR", "fdr.pdf", p.read_bytes(), run_ocr=False)


def test_ela_preview_returns_png_heatmap(client, db, tmp_path):
    inst = _draft(db, tmp_path, client)
    r = client.get(f"/api/instruments/{inst.id}/ela-preview", headers=client.login("dealing_hand"))
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get("/api/instruments/9999/ela-preview", headers=client.login("dealing_hand")).status_code == 404
    assert client.get(f"/api/instruments/{inst.id}/ela-preview").status_code == 401


# ------------------------------------------------------------------ NOC / sanction order PDF
def _matured_verified(db, client, kind="FDR"):
    from app.models import Instrument, Document
    inst = Instrument(institution_id=client.inst_id, kind=kind, d_ref_no="REL0001", d_bank="State Bank of India", d_amount=1000000,
                      d_issue_date=date.today() - timedelta(days=3000), d_maturity_date=date.today() - timedelta(days=5), d_lien=True,
                      verification="VERIFIED")
    db.add(inst); db.flush()
    for t in ("request_letter", "affidavit_6", "original_proof", "loa_eoa"):
        db.add(Document(instrument_id=inst.id, doc_type=t, filename=t + ".pdf", sha256="0" * 64))
    db.commit()
    return inst


def test_order_pdf_download_and_sha256_after_approval(client, db):
    inst = _matured_verified(db, client)
    dh, ca = client.login("dealing_hand"), client.login("competent_authority")
    assert client.post(f"/api/instruments/{inst.id}/release", headers=dh).status_code == 200
    assert client.get(f"/api/instruments/{inst.id}/release/order-pdf", headers=dh).status_code == 409    # not approved yet
    assert client.get(f"/api/instruments/{inst.id}/release", headers=dh).json()["order_sha256"] is None
    for cell in ("pgrc", "vigilance", "grants"):
        client.post(f"/api/instruments/{inst.id}/release/clearance", json={"cell": cell, "approve": True}, headers=dh)
    assert client.post(f"/api/instruments/{inst.id}/release/decision", json={"approve": True}, headers=ca).status_code == 200
    st = client.get(f"/api/instruments/{inst.id}/release", headers=dh).json()
    pdf = client.get(f"/api/instruments/{inst.id}/release/order-pdf", headers=dh)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and "NOC_" in pdf.headers["content-disposition"]
    assert st["state"] == "APPROVED" and st["order_sha256"] == hashlib.sha256(pdf.content).hexdigest()
    assert client.get(f"/api/instruments/{inst.id}/release/order-pdf").status_code == 401


# ------------------------------------------------------------------ CSV export / bulk import
def test_bulk_sample_import_covers_all_outcomes_and_is_audited(client, db):
    dh = client.login("dealing_hand")
    r = client.post("/api/instruments/bulk-csv?sample=true", headers=dh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 4 and body["outcomes"] == {"VERIFIED": 2, "DISCREPANCY": 1, "MANUAL_REVIEW": 1}
    audit = client.get("/api/audit?limit=50", headers=dh).json()
    assert any(a["action"] == "instrument.bulk_import" for a in audit)
    assert client.get("/api/audit/verify", headers=dh).json()["valid"]
    again = client.post("/api/instruments/bulk-csv?sample=true", headers=dh).json()       # idempotent: duplicates skipped
    assert again["created"] == 0 and len(again["skipped"]) == 4


def test_bulk_upload_validation_and_rbac(client):
    dh = client.login("dealing_hand")
    good = "pid,institution,kind,ref_no,bank,amount,issue_date,maturity_date,lien\n1-NEW,New Inst,FDR,ABC12345,SBI,1,00,000,2018-01-01,2027-01-01,Yes\n"
    good = good.replace("1,00,000", '"1,00,000"')
    r = client.post("/api/instruments/bulk-csv", files={"file": ("x.csv", good.encode(), "text/csv")}, headers=dh).json()
    assert r["created"] == 1 and r["outcomes"] == {"DISCREPANCY": 1}                      # no bank record -> flagged
    bad = client.post("/api/instruments/bulk-csv", files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")}, headers=dh)
    assert bad.status_code == 422 and "missing required column" in bad.json()["detail"]
    rowerr = "pid,ref_no,bank,amount,maturity_date,kind\n1-NEW,Z1,SBI,notanumber,2027-01-01,FDR\n1-NEW,Z2,SBI,500,2027-01-01,BOGUS\n"
    r = client.post("/api/instruments/bulk-csv", files={"file": ("x.csv", rowerr.encode(), "text/csv")}, headers=dh).json()
    assert r["created"] == 0 and len(r["errors"]) == 2
    assert client.post("/api/instruments/bulk-csv?sample=true", headers=client.login("institution")).status_code == 403
    assert client.post("/api/instruments/bulk-csv?sample=true", headers=client.login("bank_nodal")).status_code == 403


def test_export_csv_columns_rows_and_formula_safety(client):
    import csv
    dh = client.login("dealing_hand")
    client.post("/api/instruments/bulk-csv?sample=true", headers=dh)
    mal = "pid,institution,kind,ref_no,bank,amount,maturity_date\n1-EVIL,=HYPERLINK(\"x\"),FDR,EV1,SBI,100,2030-01-01\n"
    client.post("/api/instruments/bulk-csv", files={"file": ("m.csv", mal.encode(), "text/csv")}, headers=dh)
    r = client.get("/api/instruments/export.csv", headers=dh)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:3] == ["PID", "Institution", "Kind"] and rows[0][-3:] == ["Verification", "Category", "Days to Maturity"]
    assert len(rows[0]) == 17 and len(rows) == 6
    assert any(x[3] == "LEG0003" and x[14] == "DISCREPANCY" for x in rows[1:])
    assert any(x[1].startswith("'=") for x in rows[1:])                                   # formula neutralised
    assert client.get("/api/instruments/export.csv", headers=client.login("institution")).status_code == 403
    assert client.get("/api/instruments/export.csv").status_code == 401


# ------------------------------------------------------------------ show-cause notice + manual resolve
def _by_ref(client, ref):
    rows = client.get("/api/instruments", headers=client.login("dealing_hand")).json()
    return next(r for r in rows if r["declared"]["ref_no"] == ref)


def test_show_cause_notice_pdf(client):
    dh = client.login("dealing_hand")
    client.post("/api/instruments/bulk-csv?sample=true", headers=dh)
    bad, ok = _by_ref(client, "LEG0003"), _by_ref(client, "LEG0001")
    r = client.get(f"/api/instruments/{bad['id']}/discrepancy-notice.pdf", headers=client.login("scrutiny_officer"))
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and r.headers["content-type"] == "application/pdf"
    assert len(r.headers["x-notice-sha256"]) == 64
    assert client.get(f"/api/instruments/{ok['id']}/discrepancy-notice.pdf", headers=dh).status_code == 409
    assert any(a["action"] == "discrepancy.notice" for a in client.get("/api/audit?limit=50", headers=dh).json())


def test_manual_resolve_marks_verified_without_touching_column_a(client):
    dh, so = client.login("dealing_hand"), client.login("scrutiny_officer")
    client.post("/api/instruments/bulk-csv?sample=true", headers=dh)
    inst = _by_ref(client, "LEG0004")
    assert inst["verification"] == "MANUAL_REVIEW"
    url = f"/api/instruments/{inst['id']}/manual-resolve"
    assert client.post(url, json={"note": "ok"}, headers=dh).status_code == 403
    assert client.post(url, json={"note": "  "}, headers=so).status_code == 422
    r = client.post(url, json={"note": "Nodal officer confirmed by phone, ref N-17", "nodal_ref": "N-17"}, headers=so)
    assert r.status_code == 200
    out = r.json()
    assert out["verification"] == "VERIFIED" and out["bank_channel"] == "manual-nodal-confirmation"
    assert out["declared"] == inst["declared"]                                            # Column A untouched
    assert client.post(url, json={"note": "again"}, headers=so).status_code == 409        # already resolved
    log = client.get("/api/audit?limit=50", headers=so).json()
    assert any(a["action"] == "instrument.manual_resolve" for a in log)
    assert client.get("/api/audit/verify", headers=so).json()["valid"]
    ok = _by_ref(client, "LEG0001")
    assert client.post(f"/api/instruments/{ok['id']}/manual-resolve", json={"note": "x"}, headers=so).status_code == 409


def test_ela_threshold_separates_clean_and_forged_across_layouts(tmp_path):
    """Calibration guard: several receipt layouts, clean vs pasted-amount, all on the right side of the cutoff."""
    from app import forensics
    from app.ocr import render_pages
    for i in range(1, 7):
        item = {"id": i, "institution": "T", "layout": i % 3, "fmt": i % 2, "bank_printed": "State Bank of India",
                "ref_no": f"7001122{i:04d}", "amount": 1000000 + i * 250000, "issue_date": "2018-08-15",
                "maturity_date": "2027-01-01", "lien": True}
        p = tmp_path / f"c{i}.pdf"
        synth.draw_receipt(p, item, random.Random(i))
        orig = render_pages(p)[0]
        scan = _jpeg(_jpeg(orig, 85), 85)
        w, h = scan.size
        box = (int(w * .45), int(h * .30), int(w * .80), int(h * .34))
        patch = orig.crop(box)
        ImageDraw.Draw(patch).rectangle((0, 0, patch.width, patch.height), fill="white")
        ImageDraw.Draw(patch).text((10, 8), "Rs. 95,00,000.00", fill="black", font=ImageFont.load_default(size=40))
        forged = scan.copy(); forged.paste(patch, box)
        assert not forensics.analyse(_jpeg(scan, 90))["suspect"], i
        assert forensics.analyse(_jpeg(forged, 90))["suspect"], i
