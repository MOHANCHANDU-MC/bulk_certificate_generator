import logging

from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import JobCreate, JobCreatedResponse
from app.services.validation import validate_recipients_batch

logger = logging.getLogger(__name__)


def create_certificate_job(db: Session, job_in: JobCreate) -> tuple[Job, JobCreatedResponse, bool]:
    """
    Creates a new Job along with its associated Certificate records.
    - Runs per-recipient validation.
    - Bad recipients are marked FAILED immediately with their specific error_message.
    - Valid recipients are marked PENDING.
    - If NO recipients are valid, the Job is marked FAILED immediately and should_queue=False.
    - If at least one recipient is valid, Job status is PENDING and should_queue=True.

    Returns:
        tuple[Job, JobCreatedResponse, bool]: (job, response_payload, should_queue)
    """
    validation_results = validate_recipients_batch(job_in.recipients)
    total_count = len(job_in.recipients)

    valid_count = sum(1 for r in validation_results if r.is_valid)
    failed_count = total_count - valid_count

    # Determine initial job status
    if valid_count == 0:
        initial_job_status = JobStatus.FAILED
        should_queue = False
        message = "Job created but failed immediately: no recipients were valid."
    else:
        initial_job_status = JobStatus.PENDING
        should_queue = True
        message = "Job accepted and queued for processing."

    # Create the parent Job record
    new_job = Job(
        title=job_in.title,
        event_name=job_in.event_name,
        issuer_name=job_in.issuer_name,
        issue_date=job_in.issue_date,
        status=initial_job_status,
        total_count=total_count,
        succeeded_count=0,
        failed_count=failed_count
    )
    db.add(new_job)
    db.flush()  # Generates new_job.id without committing transaction yet

    # Create individual Certificate records
    certificates_to_add: list[Certificate] = []
    for val_res in validation_results:
        cert = Certificate(
            job_id=new_job.id,
            recipient_name=val_res.clean_name,
            recipient_email=val_res.clean_email,
            status=CertificateStatus.PENDING if val_res.is_valid else CertificateStatus.FAILED,
            error_message=val_res.error_message
        )
        certificates_to_add.append(cert)

    db.add_all(certificates_to_add)
    db.commit()
    db.refresh(new_job)

    logger.info(
        "Job created: id=%s total=%d valid=%d invalid=%d status=%s",
        new_job.id,
        total_count,
        valid_count,
        failed_count,
        new_job.status.value
    )

    response = JobCreatedResponse(
        job_id=new_job.id,
        status=new_job.status,
        total_count=new_job.total_count,
        message=message
    )

    return new_job, response, should_queue
