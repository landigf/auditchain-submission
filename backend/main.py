import warnings
warnings.filterwarnings("ignore")

from dotenv import load_dotenv
import os
import time as _time
from collections import defaultdict
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, FileResponse
from contextlib import asynccontextmanager
from db.database import init_db
from db.seed import seed
from api.routes import router

# ── Rate limiting config ─────────────────────────────────────────────────────
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "60"))
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter per client IP. Returns 429 when exceeded."""

    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request, call_next):
        if not RATE_LIMIT_ENABLED or request.url.path == "/health":
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        now = _time.time()
        window = [t for t in self._hits[ip] if now - t < 60]
        self._hits[ip] = window
        remaining = max(0, RATE_LIMIT_RPM - len(window))
        if len(window) >= RATE_LIMIT_RPM:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "retry_after_seconds": 60},
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(RATE_LIMIT_RPM),
                    "X-RateLimit-Remaining": "0",
                },
            )
        self._hits[ip].append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_RPM)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining - 1))
        return response


def _expire_clarifications():
    """Background job: mark abandoned clarification + approval requests every 15 min."""
    from datetime import datetime, timezone
    from db.database import SessionLocal
    from db.models import AuditRecord
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).isoformat()
        # Expire stale clarification requests
        pending = db.query(AuditRecord).filter(
            AuditRecord.state == "clarification_needed",
            AuditRecord.clarification_deadline < now,
        ).all()
        for r in pending:
            r.state = "abandoned"
        # Expire stale approval requests
        approval_pending = db.query(AuditRecord).filter(
            AuditRecord.state == "awaiting_approval",
            AuditRecord.approval_deadline < now,
        ).all()
        for r in approval_pending:
            r.state = "abandoned"
        if pending or approval_pending:
            db.commit()
    except Exception:
        pass
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed()
    from db.loaders import load_all
    load_all()

    # Start APScheduler for clarification expiry (every 15 min)
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(_expire_clarifications, "interval", minutes=15)
        scheduler.start()
        app.state.scheduler = scheduler
    except Exception:
        pass  # APScheduler not installed or failed — non-critical

    yield

    # Shutdown scheduler on app close
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown(wait=False)


app = FastAPI(
    title="AuditChain — Autonomous Sourcing Agent",
    description="Audit-ready procurement AI for StartHack 2026",
    version="2.0.0",
    lifespan=lifespan,
)

# Rate limiting BEFORE CORS (middleware order: last added = first executed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tightened per-deployment via CORS_ORIGINS env var if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok", "service": "AuditChain", "version": "2.0.0"}


# ── Eval tool (static HTML) ─────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"

@app.get("/eval")
def eval_page():
    eval_file = STATIC_DIR / "eval.html"
    if eval_file.exists():
        return FileResponse(eval_file, media_type="text/html")
    return JSONResponse({"error": "eval.html not found", "looked_at": str(eval_file)}, status_code=404)
