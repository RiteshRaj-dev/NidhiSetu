"""Simulated clock so evaluators can advance time and watch T-90/60/30 alerts fire."""
from datetime import date, timedelta
from sqlalchemy.orm import Session
from .models import Setting


def get_offset(db: Session) -> int:
    s = db.get(Setting, "clock_offset_days")
    return int(s.value.get("days", 0)) if s else 0


def today(db: Session) -> date:
    return date.today() + timedelta(days=get_offset(db))


def set_offset(db: Session, days: int) -> None:
    s = db.get(Setting, "clock_offset_days")
    if s is None:
        db.add(Setting(key="clock_offset_days", value={"days": days}))
    else:
        s.value = {"days": days}
    db.commit()
