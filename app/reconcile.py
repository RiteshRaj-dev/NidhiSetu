from .normalize import canon_bank, canon_ref

FIELDS = ("ref_no", "bank", "amount", "issue_date", "maturity_date", "lien")


def _js(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _canon(field, v):
    if v is None:
        return None
    if field == "bank":
        return canon_bank(v)
    if field == "ref_no":
        return canon_ref(v)
    if field == "amount":
        return round(float(v), 2)
    return v


def compare(declared: dict, bank: dict) -> list[dict]:
    """Return a list of field-level mismatches between institution-declared and bank-confirmed values."""
    out = []
    for f in FIELDS:
        a, b = _canon(f, declared.get(f)), _canon(f, bank.get(f))
        if a != b:
            out.append({"field": f, "declared": _js(declared.get(f)), "bank": _js(bank.get(f))})
    return out
