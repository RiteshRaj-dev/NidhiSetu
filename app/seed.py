from sqlalchemy.orm import Session
from .models import User, Institution
from .security import hash_password

DEMO_USERS = [
    ("institution", "institution", "Institution Dealing User"),
    ("dealing_hand", "dealing_hand", "Dealing Hand, Approval Bureau"),
    ("scrutiny_officer", "scrutiny_officer", "Scrutiny Officer, Regulation Bureau"),
    ("competent_authority", "competent_authority", "Competent Authority"),
    ("bank_nodal", "bank_nodal", "Bank Nodal Officer"),
    ("admin", "admin", "System Administrator"),
]


def ensure_users(db: Session) -> None:
    if db.query(User).count():
        return
    first = db.query(Institution).first()
    for uname, role, disp in DEMO_USERS:
        db.add(User(username=uname, password_hash=hash_password(uname + "123"), role=role, display=disp,
                    institution_id=first.id if (role == "institution" and first) else None))
    db.commit()
