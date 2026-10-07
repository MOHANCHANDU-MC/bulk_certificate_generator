import logging

from app.database import SessionLocal
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.services.job_runner import job_runner

logger = logging.getLogger(__name__)


def recover_interrupted_jobs() -> int:
    """
    Scans for jobs left in PROCESSING state on application startup
    (which happens if the server was restarted or crashed during generation).
    - Checks if the job has any certificates still in PENDING.
    - If it has pending certificates, puts the job back to PENDING and re-queues it.
    - If all certificates were already processed, finalizes the job status.
    - Returns the number of recovered jobs.
    """
    recovered_count = 0

    with SessionLocal() as db:
        stuck_jobs = db.query(Job).filter_by(status=JobStatus.PROCESSING).all()
        if not stuck_jobs:
            logger.info("Startup check: No orphaned PROCESSING jobs found.")
            return 0

        logger.warning("Startup check: Found %d orphaned PROCESSING jobs. Recovering...", len(stuck_jobs))

        for job in stuck_jobs:
            pending_certs_count = db.query(Certificate).filter_by(
                job_id=job.id,
                status=CertificateStatus.PENDING
            ).count()

            if pending_certs_count > 0:
                # Mark job as PENDING and re-run through the runner
                job.status = JobStatus.PENDING
                db.commit()
                logger.info(
                    "Re-queuing orphaned Job %s (%d certificates pending)",
                    job.id,
                    pending_certs_count
                )
                job_runner.run_job(job.id)
                recovered_count += 1
            else:
                # All certificates already processed; finalize status
                failed_count = db.query(Certificate).filter_by(
                    job_id=job.id,
                    status=CertificateStatus.FAILED
                ).count()
                success_count = db.query(Certificate).filter_by(
                    job_id=job.id,
                    status=CertificateStatus.SUCCESS
                ).count()

                job.succeeded_count = success_count
                job.failed_count = failed_count

                if failed_count == 0:
                    job.status = JobStatus.COMPLETED
                elif success_count == 0:
                    job.status = JobStatus.FAILED
                else:
                    job.status = JobStatus.COMPLETED_WITH_ERRORS

                db.commit()
                logger.info("Finalized orphaned Job %s to status %s", job.id, job.status.value)
                recovered_count += 1

    return recovered_count
