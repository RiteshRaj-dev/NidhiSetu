import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("NIDHISETU_DATA", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'nidhisetu.db'}")
VAULT_DIR = Path(os.getenv("NIDHISETU_VAULT", DATA_DIR / "vault"))
VAULT_DIR.mkdir(parents=True, exist_ok=True)
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production-use-a-32-byte-secret")
JWT_ALG = "HS256"
JWT_TTL_HOURS = 8
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"   # enables passwordless demo login
# Field-level encryption key for the document vault (AES-256-GCM via cryptography if installed)
VAULT_KEY = os.getenv("VAULT_KEY", "")
