"""Synthetic test-set generator: FDR receipts (3 bank-style layouts, clean/skewed/poor scans), bank-side records,
and seeded discrepancies. All names, numbers and IDs are fictitious."""
import io
import json
import random
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

BANKS = {   # canonical -> aliases printed on receipts
    "State Bank of India": ["State Bank of India", "SBI"],
    "Punjab National Bank": ["Punjab National Bank", "PNB"],
    "Bank of Baroda": ["Bank of Baroda"],
    "Canara Bank": ["Canara Bank"],
    "Union Bank of India": ["Union Bank of India"],
    "HDFC Bank": ["HDFC Bank Ltd", "HDFC Bank"],
    "ICICI Bank": ["ICICI Bank Limited", "ICICI Bank"],
    "Axis Bank": ["Axis Bank Ltd"],
}
SLABS = [500000, 1000000, 1500000, 2500000, 3500000, 5000000]
CITIES = ["Bhopal", "Pune", "Jaipur", "Nagpur", "Lucknow", "Indore", "Patna", "Mysuru", "Kochi", "Guwahati", "Ranchi", "Surat"]
KINDS_ = ["Engineering", "Technology", "Management", "Polytechnic", "Pharmacy", "Science"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_date(d: date, style: int) -> str:
    return [f"{d.day:02d}-{MONTHS[d.month-1]}-{d.year}", f"{d.day:02d}/{d.month:02d}/{d.year}",
            f"{d.day} {MONTHS[d.month-1]} {d.year}"][style % 3]


def fmt_amount(a: int, style: int) -> str:
    s = f"{a:,.0f}"
    # Indian digit grouping
    r = str(a)
    head, tail = r[:-3], r[-3:]
    head = ",".join([head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1]) if head else ""
    ind = f"{head},{tail}" if head else tail
    return [f"Rs. {ind}.00", f"INR {ind}", f"Rs. {ind}/-"][style % 3]


LAYOUTS = [
    {"title": "FIXED DEPOSIT RECEIPT", "ref": "FDR No.", "bank": "Bank", "amt": "Deposit Amount", "iss": "Date of Deposit", "mat": "Maturity Date"},
    {"title": "TERM DEPOSIT ADVICE", "ref": "Deposit Account No.", "bank": "Issuing Bank", "amt": "Principal Amount", "iss": "Value Date", "mat": "Date of Maturity"},
    {"title": "FD CERTIFICATE", "ref": "Receipt Number", "bank": "Bank Name", "amt": "Amount", "iss": "Issue Date", "mat": "Maturity Date"},
]


def draw_receipt(path: Path, inst: dict, rnd: random.Random) -> None:
    L = LAYOUTS[inst["layout"]]
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, 790, inst["bank_printed"].upper())
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, 755, L["title"])
    c.setFont("Helvetica", 12)
    rows = [(L["ref"], inst["ref_no"]), (L["bank"], inst["bank_printed"]),
            (L["amt"], fmt_amount(inst["amount"], inst["fmt"])),
            (L["iss"], fmt_date(date.fromisoformat(inst["issue_date"]), inst["fmt"])),
            (L["mat"], fmt_date(date.fromisoformat(inst["maturity_date"]), inst["fmt"])),
            ("Depositor", inst["institution"])]
    y = 710
    for k, v in rows:
        c.drawString(60, y, f"{k}: {v}")
        y -= 30
    lien = "Lien marked in favour of AICTE (Joint FDR)" if inst["lien"] else "No lien marked"
    c.drawString(60, y, f"Lien: {lien}")
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(60, 90, "SYNTHETIC DOCUMENT generated for SIH 2026 prototype testing. Not a real instrument.")
    c.save()


def degrade(pdf_path: Path, mode: str, rnd: random.Random) -> None:
    """Replace the vector PDF with an image-only scan: rotation, noise, blur, JPEG artefacts."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    img = pdf[0].render(scale=1.6 if mode == "poor" else 2.0).to_pil().convert("L")
    pdf.close()
    ang = rnd.uniform(1.0, 3.5) * rnd.choice([-1, 1]) if mode == "skew" else rnd.uniform(2.5, 5.0) * rnd.choice([-1, 1])
    img = img.rotate(ang, expand=True, fillcolor=255, resample=Image.BICUBIC)
    arr = np.array(img).astype(np.float32)
    sigma = 8 if mode == "skew" else 22
    arr += np.random.default_rng(rnd.randint(0, 10**6)).normal(0, sigma, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if mode == "poor":
        img = img.filter(ImageFilter.GaussianBlur(1.1))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=55 if mode == "poor" else 75)
    Image.open(io.BytesIO(buf.getvalue())).convert("RGB").save(pdf_path, "PDF", resolution=150)


def generate(out_dir: Path, n: int = 150, seed: int = 26265, today: date | None = None) -> dict:
    rnd = random.Random(seed)
    today = today or date.today()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pdf").mkdir(exist_ok=True)
    institutions = []
    for i in range(40):
        institutions.append({"pid": f"1-{rnd.randint(1000000, 9999999)}", "name": f"{rnd.choice(CITIES)} Institute of {rnd.choice(KINDS_)} {chr(65 + i % 26)}{i}",
                             "state": "Synthetic"})
    items = []
    for k in range(n):
        inst = institutions[k % len(institutions)]
        bank = rnd.choice(list(BANKS))
        amount = rnd.choice(SLABS)
        mat_offset = rnd.choice([rnd.randint(-400, -1), rnd.randint(0, 29), rnd.randint(30, 89), rnd.randint(90, 400), rnd.randint(400, 3000)])
        maturity = today + timedelta(days=mat_offset)
        issue = maturity - timedelta(days=365 * rnd.choice([8, 8, 9, 10]))
        true = {"ref_no": str(rnd.randint(10**10, 10**11 - 1)), "bank": bank, "amount": amount,
                "issue_date": issue.isoformat(), "maturity_date": maturity.isoformat(), "lien": True}
        decl = dict(true)    # what the institution's document claims
        seeded = []
        r = rnd.random()
        if r < 0.30:         # 30% seeded discrepancies, each of a different type
            t = rnd.choice(["amount", "maturity_date", "lien", "not_in_bank"])
            if t == "amount":
                decl["amount"] = int(true["amount"] * rnd.choice([1.5, 2, 2.5, 3]))
            elif t == "maturity_date":
                decl["maturity_date"] = (maturity + timedelta(days=365 * rnd.choice([1, 2, 6]))).isoformat()
            elif t == "lien":
                true["lien"] = False       # bank says no AICTE lien although the receipt claims one
            else:
                true = None
            seeded = [t]
        kind = rnd.choice(["FDR", "FDR", "JOINT_FDR", "RPGF", "RTGS"])
        printed = rnd.choice(BANKS[bank])
        item = {"id": k, "institution": inst["name"], "pid": inst["pid"], "kind": kind,
                "layout": rnd.randrange(3), "fmt": rnd.randrange(3), "bank_printed": printed,
                "scan": rnd.choices(["clean", "skew", "poor"], [0.5, 0.3, 0.2])[0],
                **{k2: decl[k2] for k2 in decl}, "seeded": seeded, "bank_truth": true}
        p = out_dir / "pdf" / f"fdr_{k:04d}.pdf"
        draw_receipt(p, item, rnd)
        if item["scan"] != "clean":
            degrade(p, item["scan"], rnd)
        item["file"] = str(p.relative_to(out_dir))
        items.append(item)
    ds = {"seed": seed, "generated_for": today.isoformat(), "institutions": institutions, "items": items}
    (out_dir / "ground_truth.json").write_text(json.dumps(ds, indent=1))
    return ds


if __name__ == "__main__":
    import sys
    d = generate(Path(sys.argv[1] if len(sys.argv) > 1 else "eval/data"), int(sys.argv[2]) if len(sys.argv) > 2 else 150)
    print(f"generated {len(d['items'])} synthetic instruments")
