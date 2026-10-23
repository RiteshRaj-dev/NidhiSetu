"""Daily maturity monitor: T-90/60/30/15 reminders, overdue escalation. Idempotent and driven by the simulated clock."""
from datetime import date
from sqlalchemy.orm import Session
from . import audit
from .models import Instrument, Alert
from .rules import get_rules


def run_daily(db: Session, today: date, actor: str = "scheduler") -> list[Alert]:
    rules = get_rules(db)
    offsets = sorted(rules["reminder_offsets_days"])
    created: list[Alert] = []
    q = db.query(Instrument).filter(Instrument.state == "ACTIVE", Instrument.d_maturity_date.isnot(None),
                                    Instrument.verification.in_(("VERIFIED", "DISCREPANCY", "MANUAL_REVIEW")))
    for inst in q.all():
        days = (inst.d_maturity_date - today).days
        have = {k for (k,) in db.query(Alert.kind).filter(Alert.instrument_id == inst.id).all()}
        if days < 0:
            if "OVERDUE" not in have:
                a = Alert(instrument_id=inst.id, kind="OVERDUE", sent_on=today, recipient="Regulation Bureau scrutiny officer",
                          channel="portal+email+sms",
                          message=f"{inst.d_ref_no} matured {-days} day(s) ago with no renewal or release action. Escalated.")
                inst.escalated = True
                db.add(a); created.append(a)
                audit.append(db, actor, "system", "alert.overdue", "instrument", inst.id, {"days_overdue": -days})
            continue
        crossed = [o for o in offsets if days <= o]
        if not crossed:
            continue
        k = min(crossed)
        if any(f"T-{o}" in have for o in offsets if o <= k):
            continue
        a = Alert(instrument_id=inst.id, kind=f"T-{k}", sent_on=today, channel="portal+email+sms",
                  message=f"{inst.d_ref_no} ({inst.d_bank}) matures on {inst.d_maturity_date:%d-%b-%Y}: {days} day(s) left. Please renew or apply for release.")
        db.add(a); created.append(a)
        audit.append(db, actor, "system", f"alert.T-{k}", "instrument", inst.id, {"days_left": days})
    db.commit()
    return created
