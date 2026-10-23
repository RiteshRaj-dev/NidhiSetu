"""Drive the running portal with a headless browser and save real prototype screenshots to docs/screenshots/.
Usage: python scripts/capture_screenshots.py [base_url]   (portal must be running and seeded)"""
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval import synth  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
api = httpx.Client(base_url=BASE, timeout=60)


def tok(u):
    return {"Authorization": "Bearer " + api.post("/api/auth/login", json={"username": u}).json()["token"]}


def make_doc(tmp, name, **over):
    item = {"id": 1, "institution": "Demo Institute", "layout": 1, "fmt": 0, "bank_printed": "State Bank of India", "lien": True}
    item.update(over)
    p = Path(tmp) / name
    rnd = random.Random(7)
    synth.draw_receipt(p, item, rnd)
    synth.degrade(p, "skew", rnd)
    return p


def main():
    tmp = tempfile.mkdtemp()
    nodal, dealer, inst_user = tok("bank_nodal"), tok("dealing_hand"), tok("institution")
    first_inst = api.get("/api/institutions", headers=inst_user).json()[0]["id"]
    today = date.today()
    # (a) a clean institution submission that the bank will confirm
    mat = today + timedelta(days=44)
    iss = mat - timedelta(days=365 * 8)
    a = make_doc(tmp, "sbi_fdr.pdf", ref_no="31245678901", amount=3500000, issue_date=iss.isoformat(), maturity_date=mat.isoformat())
    api.post("/api/bank/webhook", headers=nodal, json={"ref_no": "31245678901", "bank": "State Bank of India", "amount": 3500000,
                                                      "issue_date": iss.isoformat(), "maturity_date": mat.isoformat(), "lien": True})
    # (b) the deck's example: institution claims 35L / 2034 / lien, bank holds 15L / 2028 / no lien
    b = make_doc(tmp, "pnb_fdr.pdf", ref_no="52019876543", bank_printed="Punjab National Bank", amount=3500000,
                 issue_date="2026-08-15", maturity_date="2034-08-15")
    api.post("/api/bank/webhook", headers=nodal, json={"ref_no": "52019876543", "bank": "Punjab National Bank", "amount": 1500000,
                                                      "issue_date": "2026-08-15", "maturity_date": "2028-08-15", "lien": False})
    r = api.post("/api/instruments/upload", headers=dealer, files={"file": ("pnb_fdr.pdf", b.read_bytes(), "application/pdf")},
                 data={"institution_id": first_inst, "kind": "FDR"}).json()
    r = api.post(f"/api/instruments/{r['id']}/confirm", headers=dealer, json=r["ocr"]["fields"]).json()
    disc_id = r["id"]
    print("discrepancy example:", r["verification"], [m["field"] for m in r["mismatches"]])
    inflight = [i for i in api.get("/api/instruments", headers=dealer).json() if i["state"] == "RELEASE_PENDING"]
    rel_id = inflight[0]["id"] if inflight else None

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
        # 1. institution view: upload -> OCR -> confirm -> bank verified
        pg.goto(f"{BASE}/?as=institution&view=institution")
        pg.wait_for_selector("#upGo")
        pg.set_input_files("#upFile", str(a))
        pg.click("#upGo")
        pg.wait_for_selector("#cfGo", timeout=60000)
        pg.wait_for_selector("#pdfWrap img")
        pg.click("#cfGo")
        pg.wait_for_selector("#cfOut .okbox, #cfOut .flag", timeout=20000)
        pg.wait_for_timeout(400)
        pg.screenshot(path=str(OUT / "01_institution_ocr_confirm.png"), full_page=True)
        # 2. bureau dashboard
        pg.goto(f"{BASE}/?as=scrutiny_officer&view=bureau")
        pg.wait_for_selector(".stat")
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(OUT / "02_bureau_dashboard.png"), full_page=True)
        # 3. discrepancy view
        pg.goto(f"{BASE}/?as=scrutiny_officer&view=discrepancy&id={disc_id}")
        pg.wait_for_selector(".cmp")
        pg.fill("#sNote", "Receipt claims Rs 35L maturing 2034 with AICTE lien; bank confirms Rs 15L maturing 2028, no lien. Institution asked to resubmit.")
        pg.wait_for_timeout(400)
        pg.screenshot(path=str(OUT / "03_discrepancy_view.png"), full_page=True)
        # 4. release workflow
        if rel_id:
            pg.goto(f"{BASE}/?as=dealing_hand&view=release&id={rel_id}")
            pg.wait_for_selector(".chk")
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(OUT / "04_release_clause_646.png"), full_page=True)
        # 5. audit trail
        pg.goto(f"{BASE}/?as=scrutiny_officer&view=audit")
        pg.wait_for_selector("table")
        pg.wait_for_timeout(400)
        pg.screenshot(path=str(OUT / "05_audit_trail.png"), full_page=True)
        br.close()
    print("saved:", sorted(p.name for p in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
