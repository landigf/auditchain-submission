from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .models import Base
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./auditchain.db")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=3,
        max_overflow=2,
        pool_timeout=30,
        pool_recycle=300,
        pool_pre_ping=True,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# New columns added in v2.1+ — need migration for existing DBs
_MIGRATIONS = [
    ("suppliers", "category_l1", "TEXT"),
    ("suppliers", "category_l2", "TEXT"),
    ("suppliers", "service_regions", "TEXT"),
    ("suppliers", "data_residency_supported", "INTEGER DEFAULT 0"),
    ("suppliers", "capacity_per_month", "INTEGER"),
    ("audit_records", "confidence_label", "TEXT"),
    ("audit_records", "approval_required", "INTEGER DEFAULT 0"),
    ("audit_records", "approval_questions", "TEXT"),
    ("audit_records", "approval_deadline", "TEXT"),
    ("audit_records", "approval_answered_at", "TEXT"),
    ("audit_records", "approval_answers", "TEXT"),
    ("audit_records", "approval_responder", "TEXT"),
    ("audit_records", "is_basket", "BOOLEAN DEFAULT FALSE"),
    ("audit_records", "basket_line_count", "INTEGER DEFAULT 0"),
    ("audit_records", "basket_line_decisions", "TEXT"),
    ("audit_records", "basket_total_cost", "REAL"),
]


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column exists in a table (works for both SQLite and PostgreSQL)."""
    if DATABASE_URL.startswith("sqlite"):
        result = conn.execute(text(f"PRAGMA table_info({table})"))
        return any(row[1] == column for row in result)
    else:
        result = conn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ), {"table": table, "column": column})
        return result.fetchone() is not None


def init_db():
    Base.metadata.create_all(bind=engine)
    # Apply column migrations (no-op if column already exists)
    with engine.connect() as conn:
        for table, col_name, col_type in _MIGRATIONS:
            try:
                if not _column_exists(conn, table, col_name):
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                    conn.commit()
                    print(f"[migrate] Added {table}.{col_name}")
            except Exception:
                pass  # column already exists or other benign error


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
