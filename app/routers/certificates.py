from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Certificate, CertificateStatus
from app.schemas import ErrorResponse
from app.services.file_utils import sanitize_filename

router = APIRouter(prefix="/api/jobs", tags=["Certificates"])


@router.get(
    "/{job_id}/certificates/{certificate_id}/download",
    response_class=FileResponse,
    summary="Download single certificate PDF",
    description=(
        "Downloads a single certificate PDF by ID, verifying that it belongs to the given job "
        "and completed successfully. Files are streamed directly with safe Content-Disposition headers."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Certificate not found or not in this job",
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "Certificate 'e4d8a5c2-9dad-11d1-80b4-00c04fd430c8' not found for "
                            "Job '6ba7b810-9dad-11d1-80b4-00c04fd430c8'."
                        )
                    }
                }
            },
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Certificate is not in SUCCESS status",
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "Certificate 'e4d8a5c2-9dad-11d1-80b4-00c04fd430c8' is not available "
                            "for download (current status: failed). Reason: Name is required and cannot be empty."
                        )
                    }
                }
            },
        },
    },
)
def download_single_certificate(
    job_id: str,
    certificate_id: str,
    db: Session = Depends(get_db)
) -> FileResponse:
    """
    Downloads a single certificate PDF:
    - 404 if the certificate is not found OR does not belong to the job_id in the URL.
    - 409 if the certificate status is not SUCCESS (e.g., PENDING or FAILED).
    - Reads pdf_path strictly from the database, never from user input.
    - Validates that the file exists and is strictly within STORAGE_DIR (traversal prevention).
    - Returns application/pdf with a safe, sanitized Content-Disposition header.
    """
    cert = db.query(Certificate).filter_by(id=certificate_id, job_id=job_id).first()
    if not cert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate '{certificate_id}' not found for Job '{job_id}'."
        )

    if cert.status != CertificateStatus.SUCCESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Certificate '{certificate_id}' is not available for download "
                f"(current status: {cert.status.value}). Reason: {cert.error_message or 'Still processing'}."
            )
        )

    if not cert.pdf_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Certificate PDF file record is missing."
        )

    # Resolve path and verify it is strictly within STORAGE_DIR
    storage_root = settings.STORAGE_DIR.resolve()
    target_path = (settings.STORAGE_DIR / cert.pdf_path).resolve()

    try:
        target_path.relative_to(storage_root)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid certificate storage location."
        ) from None


    if not target_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Certificate PDF file does not exist on disk."
        )

    clean_name = sanitize_filename(cert.recipient_name or "certificate")
    # ASCII fallback for headers
    ascii_name = clean_name.encode("ascii", "ignore").decode("ascii") or "certificate"
    safe_download_name = f"{ascii_name}_{cert.id[:8]}.pdf"
    from urllib.parse import quote
    encoded_utf8_filename = quote(f"{clean_name}_{cert.id[:8]}.pdf")

    headers = {
        "Content-Disposition": (
            f'attachment; filename="{safe_download_name}"; '
            f"filename*=UTF-8''{encoded_utf8_filename}"
        )
    }

    return FileResponse(
        path=str(target_path),
        media_type="application/pdf",
        headers=headers
    )

