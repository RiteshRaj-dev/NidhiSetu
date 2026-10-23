"""Canonicalisation so formatting differences are never reported as discrepancies."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from dateutil import parser as dparser

BANK_ALIASES = {
    "State Bank of India": ["sbi", "s.b.i", "s b i", "state bank of india", "state bank of india, sbi"],
    "Punjab National Bank": ["pnb", "p.n.b", "punjab national bank"],
    "Bank of Baroda": ["bob", "bank of baroda", "b.o.b"],
    "Canara Bank": ["canara", "canara bank"],
    "Union Bank of India": ["ubi", "union bank", "union bank of india"],
    "HDFC Bank": ["hdfc", "hdfc bank", "hdfc bank ltd", "hdfc bank limited"],
    "ICICI Bank": ["icici", "icici bank", "icici bank ltd", "icici bank limited"],
    "Axis Bank": ["axis", "axis bank", "axis bank ltd", "axis bank limited"],
    "Bank of India": ["boi", "bank of india"],
    "Indian Bank": ["indian bank"],
}
_ALIAS_LOOKUP = {a: canon for canon, al in BANK_ALIASES.items() for a in al}


def canon_bank(s: str | None) -> str | None:
    if not s:
        return None
    k = re.sub(r"[^a-z. ]", "", s.lower()).strip()
    k = re.sub(r"\s+", " ", k)
    if k in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[k]
    best, score = None, 0.0
    for a, canon in _ALIAS_LOOKUP.items():
        r = SequenceMatcher(None, k, a).ratio()
        if r > score:
            best, score = canon, r
    return best if score >= 0.88 else s.strip()


def canon_ref(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def parse_amount(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float, Decimal)):
        return round(float(s), 2)
    t = str(s)
    t = re.sub(r"(?i)rs\.?|inr|₹|/-", "", t)
    t = t.replace(",", "").replace(" ", "")
    m = re.search(r"\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return round(float(Decimal(m.group(0))), 2)
    except InvalidOperation:
        return None


def parse_date(s) -> date | None:
    if s is None:
        return None
    if isinstance(s, date):
        return s
    t = str(s).strip()
    if not t:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):          # ISO dates must never be read day-first
        try:
            return date.fromisoformat(t)
        except ValueError:
            return None
    try:
        return dparser.parse(t, dayfirst=True, fuzzy=False).date()
    except (ValueError, OverflowError):
        return None


def parse_bool(s) -> bool | None:
    if isinstance(s, bool) or s is None:
        return s
    return str(s).strip().lower() in ("1", "true", "yes", "y", "marked")
