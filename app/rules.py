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
