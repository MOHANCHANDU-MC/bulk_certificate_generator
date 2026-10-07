from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Certificate, CertificateStatus, Job
from app.schemas import ErrorResponse

router = APIRouter(prefix="/api/verify", tags=["Verification"])


class PublicCertificateVerification(BaseModel):
    """
    Public verification payload.
    Rule 8: Never return email address, job_id, or server file paths.
    """
    valid: bool
    recipient_name: str
    event_name: str
    title: str
    issuer_name: str
    issue_date: date


@router.get(
    "/{certificate_id}",
    response_model=PublicCertificateVerification,
    summary="Verify certificate authenticity",
    description=(
        "Public verification endpoint confirming whether a certificate is authentic and issued. "
        "Returns event details and recipient name without disclosing private email addresses."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Certificate not found or not in SUCCESS status",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Certificate 'e4d8a5c2-9dad-11d1-80b4-00c04fd430c8' is not recognized or not found."
                    }
                }
            },
        }
    },
)
def verify_certificate(
    certificate_id: str,
    db: Session = Depends(get_db)
) -> PublicCertificateVerification:
    """
    Public verification confirming a certificate is genuine:
    - Only valid if the certificate status is SUCCESS.
    - If certificate does not exist, is malformed, or has non-SUCCESS status, returns 404.
    - Returns valid=True, recipient_name, event_name, title, issuer_name, and issue_date.
    - Never exposes emails, job IDs, or file paths.
    """
    cert = db.query(Certificate).filter_by(id=certificate_id).first()
    if not cert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate '{certificate_id}' is not recognized or not found."
        )

    # Only SUCCESS certificates can be verified
    if cert.status != CertificateStatus.SUCCESS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate '{certificate_id}' is not a valid issued certificate."
        )

    job = db.query(Job).filter_by(id=cert.job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated event information could not be retrieved."
        )

    return PublicCertificateVerification(
        valid=True,
        recipient_name=cert.recipient_name or "",
        event_name=job.event_name,
        title=job.title,
        issuer_name=job.issuer_name,
        issue_date=job.issue_date
    )
