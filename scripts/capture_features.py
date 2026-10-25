"""Screenshots for the added features (CSV import/export, ELA forensics, show-cause notice, manual review, order PDF).
Usage: python scripts/capture_features.py [base_url]   (portal must be running, seeded, with DEMO_MODE=1)"""
import io
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval import synth  # noqa: E402
from app.ocr import render_pages  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = ROOT / "docs" / "screenshots"
api = httpx.Client(base_url=BASE, timeout=90)


def tok(u):
    return {"Authorization": "Bearer " + api.post("/api/auth/login", json={"username": u}).json()["token"]}


def jpg(im, q):
    b = io.BytesIO(); im.save(b, "JPEG", quality=q); b.seek(0)
    return Image.open(b).convert("RGB")


def forged_receipt(tmp) -> Path:
    """A re-saved scan with the amount pasted over (synthetic) -> flagged by ELA."""
    item = {"id": 3, "institution": "Demo Institute", "layout": 0, "fmt": 0, "bank_printed": "State Bank of India",
            "ref_no": "70011223344", "amount": 3500000, "issue_date": "2018-08-15", "maturity_date": "2027-01-01", "lien": True}
    pdf = Path(tmp) / "f.pdf"
    synth.draw_receipt(pdf, item, random.Random(3))
    orig = render_pages(pdf)[0]
    scan = jpg(jpg(orig, 85), 85)
    w, h = scan.size
    box = (int(w * .45), int(h * .30), int(w * .80), int(h * .34))
    patch = orig.crop(box)
    d = ImageDraw.Draw(patch)
    d.rectangle((0, 0, patch.width, patch.height), fill="white")
    d.text((10, 8), "Rs. 95,00,000.00", fill="black", font=ImageFont.load_default(size=40))
    scan.paste(patch, box)
    out = Path(tmp) / "forged_scan.jpg"
    jpg(scan, 90).save(out, "JPEG", quality=90)
    return out


def main():
    tmp = tempfile.mkdtemp()
    dealer, so, ca = tok("dealing_hand"), tok("scrutiny_officer"), tok("competent_authority")
    imp = api.post("/api/instruments/bulk-csv?sample=true", headers=dealer).json()
    ids = {r["ref_no"]: r["id"] for r in imp["instruments"]}
    rows = api.get("/api/instruments", headers=dealer).json()
    if not ids:
        ids = {r["declared"]["ref_no"]: r["id"] for r in rows if (r["declared"]["ref_no"] or "").startswith("LEG")}
    # a released case (order PDF + SHA-256)
    cand = [r for r in rows if r["category"] == "Matured" and r["verification"] == "VERIFIED" and r["state"] == "ACTIVE"]
    rel_id = None
    if cand:
        rel_id = cand[0]["id"]
        for t in ("request_letter", "affidavit_6", "original_proof", "loa_eoa"):
            api.post(f"/api/instruments/{rel_id}/documents", headers=dealer, data={"doc_type": t},
                     files={"file": (t + ".pdf", b"synthetic " + t.encode(), "application/pdf")})
        api.post(f"/api/instruments/{rel_id}/release", headers=dealer)
        for c in ("pgrc", "vigilance", "grants"):
            api.post(f"/api/instruments/{rel_id}/release/clearance", headers=dealer, json={"cell": c, "approve": True})
        print("decision:", api.post(f"/api/instruments/{rel_id}/release/decision", headers=ca, json={"approve": True}).status_code)
    pdf = api.get(f"/api/instruments/{ids['LEG0003']}/discrepancy-notice.pdf", headers=so)
    (Path(tmp) / "notice.pdf").write_bytes(pdf.content)
    subprocess.run(["pdftoppm", "-r", "80", "-png", "-singlefile", str(Path(tmp) / "notice.pdf"), str(OUT / "11_show_cause_notice")], check=True)
    forged = forged_receipt(tmp)

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1366, "height": 900})
        pg.goto(f"{BASE}/?as=dealing_hand&view=bureau"); pg.wait_for_timeout(1800)
        pg.locator("#expCsv").scroll_into_view_if_needed()
        pg.screenshot(path=str(OUT / "06_bureau_csv_import_export.png"), full_page=True)
        pg.goto(f"{BASE}/?as=dealing_hand&view=institution"); pg.wait_for_timeout(1500)
        pg.set_input_files("#upFile", str(forged)); pg.click("#upGo"); pg.wait_for_selector("#elaBtn", timeout=90000)
        pg.wait_for_timeout(1200); pg.click("#elaBtn"); pg.wait_for_timeout(1800)
        pg.screenshot(path=str(OUT / "07_ela_forensics_heatmap.png"), full_page=True)
        pg.goto(f"{BASE}/?as=scrutiny_officer&view=discrepancy&id={ids['LEG0003']}"); pg.wait_for_timeout(2000)
        pg.screenshot(path=str(OUT / "08_discrepancy_show_cause.png"), full_page=True)
        pg.goto(f"{BASE}/?as=scrutiny_officer&view=discrepancy&id={ids['LEG0004']}"); pg.wait_for_timeout(2000)
        pg.fill("#sNote", "Bank nodal officer confirmed ref LEG0004 by official email, ref N-17")
        pg.screenshot(path=str(OUT / "09_manual_review_resolve.png"), full_page=True)
        if rel_id:
            pg.goto(f"{BASE}/?as=dealing_hand&view=release&id={rel_id}"); pg.wait_for_timeout(2000)
            pg.screenshot(path=str(OUT / "10_release_order_pdf_sha256.png"), full_page=True)
        b.close()
    print("saved", sorted(p.name for p in OUT.glob("*.png")))


main()
