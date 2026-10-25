"""Admin-configurable rule tables (no code change needed when the APH changes)."""
import copy
from sqlalchemy.orm import Session
from .models import Setting

DEFAULT_RULES = {
    "reminder_offsets_days": [90, 60, 30, 15],
    "expiring_window_days": 90,
    "min_lock_in_years": 8,
    "required_docs_submission": ["affidavit_6", "loa_eoa"],
    "release_documents": ["request_letter", "affidavit_6", "original_proof", "loa_eoa"],
    "release_clearances": ["pgrc", "vigilance", "grants"],
    "required_fields": ["ref_no", "bank", "amount", "issue_date", "maturity_date"],
    "order_type": {"FDR": "NOC", "JOINT_FDR": "NOC", "RPGF": "NOC", "RTGS": "SANCTION_ORDER"},
    "unsupported_banks": ["The Example Co-operative Bank"],
}


def get_rules(db: Session) -> dict:
    s = db.get(Setting, "rules")
    rules = copy.deepcopy(DEFAULT_RULES)
    if s:
        rules.update(s.value)
    return rules


def set_rules(db: Session, patch: dict) -> dict:
    rules = get_rules(db)
    rules.update({k: v for k, v in patch.items() if k in DEFAULT_RULES})
    s = db.get(Setting, "rules")
    if s is None:
        db.add(Setting(key="rules", value=rules))
    else:
        s.value = rules
    db.commit()
    return rules


_INT_LISTS = ("reminder_offsets_days",)
_STR_LISTS = ("required_docs_submission", "release_documents", "release_clearances", "required_fields", "unsupported_banks")


def validate_patch(patch: dict) -> None:
    """Reject unknown keys and wrong types so a bad rule can never break the scheduler or release gate."""
    unknown = set(patch) - set(DEFAULT_RULES)
    if unknown:
        raise ValueError("Unknown rule(s): " + ", ".join(sorted(unknown)))
    for k, v in patch.items():
        if k in _INT_LISTS:
            if not (isinstance(v, list) and v and all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= 3650 for x in v)):
                raise ValueError(f"{k} must be a non-empty list of whole days between 1 and 3650")
        elif k in _STR_LISTS:
            if not (isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)):
                raise ValueError(f"{k} must be a list of non-empty strings")
        elif k in ("expiring_window_days", "min_lock_in_years"):
            if not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 3650):
                raise ValueError(f"{k} must be a number between 0 and 3650")
        elif k == "order_type":
            if not (isinstance(v, dict) and all(isinstance(a, str) and b in ("NOC", "SANCTION_ORDER") for a, b in v.items())):
                raise ValueError("order_type must map instrument kinds to NOC or SANCTION_ORDER")
