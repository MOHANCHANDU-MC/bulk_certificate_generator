import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Certificate, Job, JobStatus
from app.schemas import (
    CertificateResponse,
    ErrorResponse,
    JobCreate,
    JobCreatedResponse,
    JobStatusResponse,
    PaginatedResponse,
)
from app.services.job_runner import job_runner
from app.services.job_service import create_certificate_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


@router.post(
    "",
    response_model=JobCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit bulk certificate job",
    description=(
        "Accepts a bulk certificate generation request. Validates the payload and recipient batch. "
        "Queues valid certificates for background rendering and records any invalid recipients as failed."
    ),
)
def submit_certificate_job(
    job_in: JobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
) -> JobCreatedResponse:
    """
    Accepts a bulk certificate generation request.
    - Validates request payload (returns 422 if malformed or empty).
    - Validates recipients individually (bad recipients stored as FAILED).
    - If all recipients are invalid, job is marked FAILED immediately and no task queued.
    - Returns 202 Accepted with job_id, status, and total count.
    """
    job, response, should_queue = create_certificate_job(db=db, job_in=job_in)

    if should_queue:
        logger.info("Queuing background task for Job %s", job.id)
        background_tasks.add_task(job_runner.run_job, job.id)

    return response


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status and progress",
    description=(
        "Retrieves the real-time processing status, progress percentage, and success/failure counts "
        "for a certificate generation job."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Job not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Job with ID '6ba7b810-9dad-11d1-80b4-00c04fd430c8' not found."}
                }
            },
        }
    },
)
def get_job_status(job_id: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    """
    Retrieves the execution status and progress summary for a job:
    - Status (PENDING, PROCESSING, COMPLETED, COMPLETED_WITH_ERRORS, FAILED)
    - Total, Succeeded, Failed, and Pending counts
    - Progress percentage: ((succeeded + failed) / total) * 100
    - Timestamps
    - 404 if the job is not found
    """
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID '{job_id}' not found."
        )

    # Re-calculate pending count directly to guarantee consistency
    pending_count = max(job.total_count - (job.succeeded_count + job.failed_count), 0)
    progress_percentage = (
        round(((job.succeeded_count + job.failed_count) / job.total_count) * 100.0, 2)
        if job.total_count > 0 else 0.0
    )

    return JobStatusResponse(
        id=job.id,
        title=job.title,
        event_name=job.event_name,
        issuer_name=job.issuer_name,
        issue_date=job.issue_date,
        status=job.status,
        total_count=job.total_count,
        succeeded_count=job.succeeded_count,
        failed_count=job.failed_count,
        pending_count=pending_count,
        progress_percentage=progress_percentage,
        created_at=job.created_at,
        updated_at=job.updated_at
    )


@router.get(
    "/{job_id}/certificates",
    response_model=PaginatedResponse[CertificateResponse],
    summary="List certificates for a job",
    description=(
        "Returns a paginated list of certificates for a job, including recipient details, statuses, "
        "and failure error messages."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Job not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Job with ID '6ba7b810-9dad-11d1-80b4-00c04fd430c8' not found."}
                }
            },
        }
    },
)
def get_job_certificates(
    job_id: str,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page (max 100)"),
    db: Session = Depends(get_db)
) -> PaginatedResponse[CertificateResponse]:
    """
    Returns a paginated list of certificates for a job:
    - Exposes id, recipient_name, recipient_email, status, error_message, timestamps.
    - Does NOT expose internal pdf_path or server file paths.
    - Ordered stably by created_at ascending, then id ascending.
    """
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID '{job_id}' not found."
        )

    query = db.query(Certificate).filter_by(job_id=job_id).order_by(
        Certificate.created_at.asc(),
        Certificate.id.asc()
    )

    total_items = query.count()
    offset = (page - 1) * page_size
    items = query.offset(offset).limit(page_size).all()
    total_pages = (total_items + page_size - 1) // page_size if total_items > 0 else 0

    return PaginatedResponse[CertificateResponse](
        items=[CertificateResponse.model_validate(c) for c in items],
        total=total_items,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


@router.get(
    "/{job_id}/download",
    response_class=FileResponse,
    summary="Download ZIP of successful certificates",
    description=(
        "Downloads a ZIP archive containing all successfully generated certificate PDFs for a completed job. "
        "Streams from a temporary file on disk and automatically cleans it up after response delivery."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Job not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Job with ID '6ba7b810-9dad-11d1-80b4-00c04fd430c8' not found."}
                }
            },
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Job is still processing or has no successful certificates",
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "Job '6ba7b810-9dad-11d1-80b4-00c04fd430c8' is still in progress "
                            "(status: processing). Please try again later."
                        )
                    }
                }
            },
        },
    },
)

def download_job_certificates_zip(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
) -> FileResponse:
    """
    Downloads a ZIP archive containing all successful certificates for a job:
    - 404 for unknown job.
    - 409 if the job is still PENDING or PROCESSING.
    - 409 if the job has no successful certificates.
    - Streams from a temporary file on disk (not in-memory).
    - Uses unique filenames (sanitized name + short id) to handle recipients with identical names.
    - Automatically deletes temporary ZIP file after response is transmitted via BackgroundTasks.
    """
    import os
    import tempfile
    import zipfile

    from app.config import settings
    from app.models import CertificateStatus
    from app.services.file_utils import sanitize_filename

    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID '{job_id}' not found."
        )

    # 409 if job is not completed yet
    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job '{job_id}' is still in progress (status: {job.status.value}). Please try again later."
        )

    # Fetch successful certificates
    successful_certs = db.query(Certificate).filter_by(
        job_id=job_id,
        status=CertificateStatus.SUCCESS
    ).all()

    if not successful_certs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No certificates succeeded for Job '{job_id}'. Archive cannot be generated."
        )

    # Create temporary ZIP file
    storage_root = settings.STORAGE_DIR.resolve()
    temp_zip_fd, temp_zip_path_str = tempfile.mkstemp(prefix=f"job_{job_id[:8]}_", suffix=".zip")
    os.close(temp_zip_fd)

    try:
        with zipfile.ZipFile(temp_zip_path_str, "w", zipfile.ZIP_DEFLATED) as zipf:
            for cert in successful_certs:
                if not cert.pdf_path:
                    continue
                file_path = (settings.STORAGE_DIR / cert.pdf_path).resolve()
                try:
                    file_path.relative_to(storage_root)
                except ValueError:
                    continue

                if file_path.is_file():
                    safe_name = sanitize_filename(cert.recipient_name or "certificate")
                    arcname = f"{safe_name}_{cert.id[:8]}.pdf"
                    zipf.write(str(file_path), arcname=arcname)
    except Exception:
        if os.path.exists(temp_zip_path_str):
            os.remove(temp_zip_path_str)
        raise

    def cleanup_temp_file(path_to_delete: str) -> None:
        try:
            if os.path.exists(path_to_delete):
                os.remove(path_to_delete)
        except Exception as e:
            logger.warning("Could not delete temporary zip %s: %s", path_to_delete, e)

    background_tasks.add_task(cleanup_temp_file, temp_zip_path_str)

    job_slug = sanitize_filename(job.title)
    zip_download_name = f"{job_slug}_{job.id[:8]}_certificates.zip"

    return FileResponse(
        path=temp_zip_path_str,
        media_type="application/zip",
        filename=zip_download_name
    )



