import abc
import logging
import traceback

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.services.certificate_generator import (
    CertificateRenderData,
    UnsupportedCharactersError,
    generate_certificate_pdf,
)

logger = logging.getLogger(__name__)


class JobRunner(abc.ABC):
    """
    Abstract interface for job processing.
    Can be backed by FastAPI BackgroundTasks, or swapped for Celery/Redis.
    """
    @abc.abstractmethod
    def run_job(self, job_id: str) -> None:
        """Execute processing for the given job_id."""
        raise NotImplementedError


class BackgroundJobRunner(JobRunner):
    """
    Default JobRunner implementation executing asynchronously in the background.
    - Creates its OWN dedicated database session per certificate.
    - Skips non-PENDING certificates so re-running is safe.
    - Catches all exceptions per certificate and isolates failures.
    - Updates job status dynamically upon completion.
    """

    def run_job(self, job_id: str) -> None:
        logger.info("Starting processing for Job: %s", job_id)

        # 1. Mark Job as PROCESSING
        with SessionLocal() as db:
            job = db.query(Job).filter_by(id=job_id).first()
            if not job:
                logger.error("Job %s not found in database", job_id)
                return
            if job.status == JobStatus.FAILED and job.failed_count == job.total_count:
                # Already finalized as failed during validation
                return

            job.status = JobStatus.PROCESSING
            db.commit()

        # 2. Retrieve list of all certificate IDs for this job
        with SessionLocal() as db:
            cert_ids = [
                c.id for c in db.query(Certificate.id).filter_by(job_id=job_id).all()
            ]

        # 3. Process each certificate one by one with a dedicated DB session per certificate
        try:
            for cert_id in cert_ids:
                self._process_single_certificate(job_id=job_id, cert_id=cert_id)
        except Exception as e:
            logger.critical("Fatal unexpected error in job runner loop for Job %s: %s", job_id, e)
            logger.critical(traceback.format_exc())
            with SessionLocal() as db:
                job = db.query(Job).filter_by(id=job_id).first()
                if job:
                    job.status = JobStatus.FAILED
                    db.commit()
            return

        # 4. Finalize Job Status based on certificate rows
        with SessionLocal() as db:
            job = db.query(Job).filter_by(id=job_id).first()
            if not job:
                return

            # Re-count statuses directly from database for total consistency
            success_count = db.query(Certificate).filter_by(
                job_id=job_id,
                status=CertificateStatus.SUCCESS
            ).count()
            failed_count = db.query(Certificate).filter_by(
                job_id=job_id,
                status=CertificateStatus.FAILED
            ).count()
            pending_count = db.query(Certificate).filter_by(
                job_id=job_id,
                status=CertificateStatus.PENDING
            ).count()

            job.succeeded_count = success_count
            job.failed_count = failed_count

            if pending_count > 0:
                # Some unfinished certificates remained
                job.status = JobStatus.PROCESSING
            elif failed_count == 0:
                job.status = JobStatus.COMPLETED
            elif success_count == 0:
                job.status = JobStatus.FAILED
            else:
                job.status = JobStatus.COMPLETED_WITH_ERRORS

            db.commit()
            logger.info(
                "Job %s completed: status=%s, total=%d, succeeded=%d, failed=%d",
                job.id,
                job.status.value,
                job.total_count,
                job.succeeded_count,
                job.failed_count
            )

    def _process_single_certificate(self, job_id: str, cert_id: str) -> None:
        """
        Processes a single certificate in its own isolated database transaction.
        Never reuses external database sessions.
        """
        with SessionLocal() as db:
            cert = db.query(Certificate).filter_by(id=cert_id, job_id=job_id).first()
            if not cert:
                return

            # Skip if not PENDING (e.g., already failed during validation, or already succeeded)
            if cert.status != CertificateStatus.PENDING:
                logger.debug("Skipping certificate %s with status %s", cert.id, cert.status.value)
                return

            job = db.query(Job).filter_by(id=job_id).first()
            if not job:
                return

            # Output destination path relative to STORAGE_DIR
            relative_pdf_path = f"{job_id}/{cert.id}.pdf"
            absolute_pdf_path = settings.STORAGE_DIR / relative_pdf_path

            render_data = CertificateRenderData(
                certificate_id=cert.id,
                recipient_name=cert.recipient_name or "Recipient",
                event_name=job.event_name,
                title=job.title,
                issuer_name=job.issuer_name,
                issue_date=job.issue_date
            )

            try:
                generate_certificate_pdf(data=render_data, output_path=absolute_pdf_path)

                # Set success status and relative pdf path
                cert.status = CertificateStatus.SUCCESS
                cert.pdf_path = relative_pdf_path
                cert.error_message = None
                job.succeeded_count += 1
                db.commit()
                logger.info("Successfully generated certificate %s for Job %s", cert.id, job_id)

            except UnsupportedCharactersError as e:
                # Clean error message without stack trace
                clean_err_msg = str(e)
                logger.warning("Certificate %s failed font validation: %s", cert.id, clean_err_msg)
                self._record_certificate_failure(db, cert, job, clean_err_msg)

            except Exception as e:
                # Catch any unexpected generation failure
                logger.error("Failed to generate certificate %s: %s", cert.id, e)
                logger.error(traceback.format_exc())
                clean_err_msg = f"Failed to generate certificate PDF: {type(e).__name__}"
                self._record_certificate_failure(db, cert, job, clean_err_msg)

    def _record_certificate_failure(
        self,
        db: Session,
        cert: Certificate,
        job: Job,
        error_message: str
    ) -> None:
        """Helper to mark certificate as FAILED and update job failed count in one transaction."""
        cert.status = CertificateStatus.FAILED
        cert.error_message = error_message
        cert.pdf_path = None
        job.failed_count += 1
        db.commit()


# Singleton runner instance
job_runner: JobRunner = BackgroundJobRunner()
