import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db
from app.routers import certificates, jobs, verify

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan context:
    - Creates database tables on startup (no migrations used, Alembic is next step).
    - Checks and recovers orphaned jobs left in PROCESSING.
    """
    logger.info("Starting up %s v%s...", settings.APP_NAME, settings.APP_VERSION)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified.")

    # Startup recovery routine
    try:
        from app.services.recovery import recover_interrupted_jobs
        recovered = recover_interrupted_jobs()
        if recovered > 0:
            logger.info("Startup recovery processed %d orphaned jobs.", recovered)
    except Exception as e:
        logger.error("Error during startup crash recovery: %s", e)

    yield
    logger.info("Shutting down application...")



app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan
)


# Consistent JSON error format for validation errors (422)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Returns a clean, structured JSON response on validation errors."""
    errors = []
    for err in exc.errors():
        field = ".".join(str(loc) for loc in err.get("loc", []))
        errors.append({
            "field": field,
            "message": err.get("msg"),
            "type": err.get("type")
        })
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "error": "Validation Error",
            "status_code": 422,
            "details": errors
        }
    )





# Health check endpoint
@app.get(
    "/health",
    tags=["Health"],
    summary="API health and database connectivity check",
    description="Verifies the API is online and the database connection pool is operational.",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Database connection failed",
            "content": {
                "application/json": {
                    "example": {
                        "status": "unhealthy",
                        "database": "disconnected",
                        "error": "Database connectivity check failed"
                    }
                }
            },
        }
    },
)
def health_check(db: Session = Depends(get_db)):
    """
    Health check endpoint:
    Performs a simple query against the database to confirm operational health.
    Returns 200 with status 'ok', or 503 if the database check fails.
    """
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "database": "connected",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION
        }
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "database": "disconnected",
                "error": "Database connectivity check failed"
            }
        )


# Include routers
app.include_router(jobs.router)
app.include_router(certificates.router)
app.include_router(verify.router)

