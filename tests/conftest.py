import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NIDHISETU_DATA", tempfile.mkdtemp(prefix="nidhisetu_test_"))
os.environ["DEMO_MODE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def db():
    from app.db import Base, engine, SessionLocal, init_db
    Base.metadata.drop_all(engine)
    init_db()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    from app.main import app
    from app import seed
    from app.models import Institution
    inst = Institution(pid="1-TEST", name="Test Institute of Technology", state="X")
    db.add(inst); db.commit()
    seed.ensure_users(db)
    with TestClient(app) as c:
        def login(u):
            r = c.post("/api/auth/login", json={"username": u})
            return {"Authorization": "Bearer " + r.json()["token"]}
        c.login = login
        c.inst_id = inst.id
        yield c
