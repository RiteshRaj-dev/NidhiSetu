"""OCR pre-fill: rasterise -> deskew/denoise -> Tesseract -> label-aware field parsing with per-field confidence.
The uploader always reviews and confirms the values (human-in-the-loop); nothing is auto-trusted."""
import io
import re
from pathlib import Path
import cv2
import numpy as np
import pypdfium2 as pdfium
import pytesseract
from PIL import Image
from . import forensics
from .normalize import parse_amount, parse_date, canon_bank, BANK_ALIASES

DATE_RE = r"(\d{1,2}[-/ .](?:[A-Za-z]{3,9}|\d{1,2})[-/ .,]+\d{2,4})"
PATTERNS = {
    "ref_no": re.compile(r"(?:FDR|F\.?D\.?R\.?|Receipt|Deposit|Account|A/c|Certificate|Instrument)\s*(?:No\.?|Number|Nr\.?|Ref(?:erence)?\.?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,})", re.I),
    "bank": re.compile(r"(?:Issuing\s+)?Bank(?:\s+Name)?\s*[:\-]\s*(.+)$", re.I),
    "amount": re.compile(r"(?:Deposit\s+)?(?:Amount|Principal|Sum)[^:\d\n]{0,20}[:\-]?\s*(?:Rs\.?|INR|₹)?\s*([\dOoIl][\dOoIl,]*(?:\.[\dOo]{1,2})?)", re.I),
    "issue_date": re.compile(r"(?:Date\s+of\s+(?:Deposit|Issue)|Deposit\s+Date|Issue\s+Date|Value\s+Date|Opening\s+Date)\s*[:\-]?\s*" + DATE_RE, re.I),
    "maturity_date": re.compile(r"(?:Maturity\s+Date|Date\s+of\s+Maturity|Matures\s+on|Due\s+Date)\s*[:\-]?\s*" + DATE_RE, re.I),
}


def render_pages(path: str | Path, scale: float = 2.6) -> list[Image.Image]:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(str(p))
        try:
            return [pdf[i].render(scale=scale).to_pil().convert("RGB") for i in range(min(len(pdf), 3))]
        finally:
            pdf.close()
    return [Image.open(p).convert("RGB")]


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) < 200:
        return gray, 0.0
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if angle > 45:
        angle -= 90
    if abs(angle) < 0.15 or abs(angle) > 12:
        return gray, 0.0
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE), angle


def preprocess(img: Image.Image) -> tuple[np.ndarray, float]:
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, None, 12, 7, 21)
    gray, angle = deskew(gray)
    gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return gray, angle


def ocr_lines(arr: np.ndarray) -> list[dict]:
    d = pytesseract.image_to_data(Image.fromarray(arr), config="--oem 1 --psm 6", output_type=pytesseract.Output.DICT)
    lines: dict[tuple, dict] = {}
    for i, txt in enumerate(d["text"]):
        if not txt.strip():
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        ln = lines.setdefault(key, {"words": [], "conf": []})
        ln["words"].append(txt)
        c = float(d["conf"][i])
        if c >= 0:
            ln["conf"].append(c)
    return [{"text": " ".join(v["words"]), "conf": (sum(v["conf"]) / len(v["conf"]) / 100) if v["conf"] else 0.0}
            for v in lines.values()]


def _fix_digits(s: str) -> str:
    return s.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"}))


def parse_lines(lines: list[dict]) -> tuple[dict, dict]:
    fields: dict = {k: None for k in ("ref_no", "bank", "amount", "issue_date", "maturity_date", "lien")}
    conf: dict = {k: 0.0 for k in fields}
    for ln in lines:
        t = ln["text"]
        for k, rx in PATTERNS.items():
            if fields[k] is not None:
                continue
            m = rx.search(t)
            if not m:
                continue
            v = m.group(1).strip()
            if k == "amount":
                v = _fix_digits(v)
                if parse_amount(v) is None:
                    continue
            if k in ("issue_date", "maturity_date") and parse_date(v) is None:
                continue
            if k == "ref_no":
                v = _fix_digits(v) if sum(c.isdigit() for c in v) >= len(v) - 2 else v
            fields[k], conf[k] = v, round(ln["conf"], 3)
        if fields["lien"] is None and re.search(r"\blien\b", t, re.I):
            neg = re.search(r"\bno\s+lien|not\s+marked|without\s+lien|lien\s*:\s*(no|nil)", t, re.I)
            pos = re.search(r"aicte|marked|joint|lien\s*:\s*(yes|marked)", t, re.I)
            fields["lien"] = False if neg else (True if pos else None)
            conf["lien"] = round(ln["conf"], 3)
    if fields["bank"] is None:   # fall back to scanning for a known bank name anywhere on the page
        for ln in lines:
            low = ln["text"].lower()
            for canon, aliases in BANK_ALIASES.items():
                if any(re.search(rf"\b{re.escape(a)}\b", low) for a in aliases if len(a) > 3):
                    fields["bank"], conf["bank"] = canon, round(ln["conf"], 3)
                    break
            if fields["bank"]:
                break
    if fields["bank"]:
        fields["bank"] = canon_bank(fields["bank"])
    return fields, conf


def preprocess_soft(img: Image.Image) -> np.ndarray:
    """Second-chance preprocessing for poor scans: upscale, local contrast, adaptive threshold."""
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=1.4, fy=1.4, interpolation=cv2.INTER_CUBIC)
    gray, _ = deskew(cv2.GaussianBlur(gray, (3, 3), 0))
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)


def extract_fields(path: str | Path) -> dict:
    all_lines: list[dict] = []
    angle = 0.0
    pages = render_pages(path)
    for img in pages:
        arr, a = preprocess(img)
        angle = a if abs(a) > abs(angle) else angle
        all_lines += ocr_lines(arr)
    fields, conf = parse_lines(all_lines)
    retried = False
    if any(fields[k] is None for k in ("ref_no", "amount", "issue_date", "maturity_date", "bank")):
        retried = True
        lines2: list[dict] = []
        for img in pages:
            lines2 += ocr_lines(preprocess_soft(img))
        f2, c2 = parse_lines(lines2)
        for k, v in f2.items():           # fill gaps only; never overwrite a first-pass value
            if fields[k] is None and v is not None:
                fields[k], conf[k] = v, c2[k]
        all_lines += lines2
    return {"engine": "tesseract-5 + opencv deskew", "deskew_angle": round(angle, 2), "second_pass": retried, "fields": fields,
            "confidence": conf, "raw_text": "\n".join(l["text"] for l in all_lines),
            "forensics": forensics.analyse(pages[0])}
