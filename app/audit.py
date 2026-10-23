"""Tamper-evident append-only audit trail: every row stores sha256(prev_hash || canonical_json(entry))."""
import hashlib
import json
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import AuditLog

GENESIS = "0" * 64


def _digest(prev: str, entry: dict) -> str:
    blob = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev + blob).encode()).hexdigest()


def append(db: Session, actor: str, role: str, action: str, entity: str,
           entity_id: int | None = None, payload: dict | None = None) -> AuditLog:
    last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.hash if last else GENESIS
    payload = json.loads(json.dumps(payload or {}, default=str))
    entry = {"ts": datetime.utcnow().isoformat(timespec="microseconds"), "actor": actor, "role": role,
             "action": action, "entity": entity, "entity_id": entity_id, "payload": payload}
    row = AuditLog(**entry, prev_hash=prev, hash=_digest(prev, entry))
    db.add(row)
    db.flush()
    return row


def verify_chain(db: Session) -> dict:
    prev = GENESIS
    n = 0
    for row in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars():
        entry = {"ts": row.ts, "actor": row.actor, "role": row.role, "action": row.action,
                 "entity": row.entity, "entity_id": row.entity_id, "payload": row.payload}
        if row.prev_hash != prev or row.hash != _digest(prev, entry):
            return {"valid": False, "broken_at": row.id, "checked": n}
        prev = row.hash
        n += 1
    return {"valid": True, "checked": n, "head": prev}
