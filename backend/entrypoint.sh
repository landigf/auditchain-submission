#!/bin/sh
set -e

# ── Suppress all Python warnings globally ───────────────────────────────────
export PYTHONWARNINGS="ignore"
export PYTHONDONTWRITEBYTECODE=1

# ── Card-path bridge ─────────────────────────────────────────────────────────
# Metaflow card reader resolves:  <ds-root>/mf.cards/<FlowName>/runs/…
# Metaflow card writer stores at: <ds-root>/<FlowName>/runs/…
# We create a "mf.cards" directory with a symlink for each flow name so both
# paths resolve to the same files.  This avoids a self-referencing symlink.
DS_ROOT="${METAFLOW_DATASTORE_SYSROOT_LOCAL:-/metaflow-store}/.metaflow"
mkdir -p "$DS_ROOT/mf.cards"
# Link any existing flow directories (and Phase2Flow specifically)
for flow_dir in "$DS_ROOT"/*/; do
    flow_name="$(basename "$flow_dir")"
    [ "$flow_name" = "mf.cards" ] && continue
    ln -sfn "$flow_dir" "$DS_ROOT/mf.cards/$flow_name" 2>/dev/null || true
done
# Ensure Phase2Flow link exists even if no runs yet
ln -sfn "$DS_ROOT/Phase2Flow" "$DS_ROOT/mf.cards/Phase2Flow" 2>/dev/null || true

# ── Start application ────────────────────────────────────────────────────────
python -c 'from db.database import init_db; init_db()'

# Auto-migrate: add new columns if they don't exist yet (handles upgrades)
python -c '
import sqlite3, os
db_url = os.environ.get("DATABASE_URL", "sqlite:///./auditchain.db")
if db_url.startswith("sqlite"):
    db_path = db_url.replace("sqlite:///", "")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_records)").fetchall()]
        migrations = {
            "is_basket": "BOOLEAN DEFAULT 0",
            "basket_line_count": "INTEGER DEFAULT 0",
            "basket_line_decisions": "TEXT",
            "basket_total_cost": "REAL",
        }
        for col, dtype in migrations.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE audit_records ADD COLUMN {col} {dtype}")
                print(f"[migrate] Added column: {col}")
        conn.commit()
        conn.close()
' 2>&1 || true

python -m db.loaders
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
