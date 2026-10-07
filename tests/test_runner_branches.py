import datetime
from unittest.mock import patch

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.services.job_runner import job_runner
from app.services.recovery import recover_interrupted_jobs


def test_job_runner_missing_job(db_session):
    """Job runner returns safely if job_id does not exist."""
    job_runner.run_job("non-existent-job-id")


def test_job_runner_already_failed_all_invalid(db_session):
    """Job runner skips if job was already marked FAILED during creation."""
    job = Job(
        title="Failed Job",
        event_name="Event",
        issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 1, 1),
        status=JobStatus.FAILED,
        total_count=1,
        succeeded_count=0,
        failed_count=1
    )
    db_session.add(job)
    db_session.commit()

    job_runner.run_job(job.id)
    db_session.refresh(job)
    assert job.status == JobStatus.FAILED


def test_job_runner_fatal_exception_in_loop(db_session):
    """If an unexpected fatal exception occurs in the runner loop, job is marked FAILED."""
    job = Job(
        title="Fatal Job",
        event_name="Event",
        issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 1, 1),
        status=JobStatus.PENDING,
        total_count=1,
        succeeded_count=0,
        failed_count=0
    )
    db_session.add(job)
    db_session.flush()

    cert = Certificate(
        job_id=job.id,
        recipient_name="Student",
        status=CertificateStatus.PENDING
    )
    db_session.add(cert)
    db_session.commit()

    with patch.object(job_runner, "_process_single_certificate", side_effect=Exception("Database crash")):
        job_runner.run_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.FAILED


def test_recovery_all_certificates_already_finished(db_session):
    """
    Test recovery when a job was left in PROCESSING,
    but all its certificates were already processed (mix of success and failed).
    """
    job = Job(
        title="Finished but Processing",
        event_name="Event",
        issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 1, 1),
        status=JobStatus.PROCESSING,
        total_count=2,
        succeeded_count=0,
        failed_count=0
    )
    db_session.add(job)
    db_session.flush()

    c1 = Certificate(job_id=job.id, recipient_name="C1", status=CertificateStatus.SUCCESS, pdf_path="a.pdf")
    c2 = Certificate(job_id=job.id, recipient_name="C2", status=CertificateStatus.FAILED, error_message="err")
    db_session.add_all([c1, c2])
    db_session.commit()

    recovered = recover_interrupted_jobs()
    assert recovered == 1

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED_WITH_ERRORS
    assert job.succeeded_count == 1
    assert job.failed_count == 1


def test_recovery_all_certificates_succeeded(db_session):
    """Test recovery when all certificates in the interrupted job succeeded."""
    job = Job(
        title="All Succeeded but Processing",
        event_name="Event",
        issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 1, 1),
        status=JobStatus.PROCESSING,
        total_count=1,
        succeeded_count=0,
        failed_count=0
    )
    db_session.add(job)
    db_session.flush()

    c1 = Certificate(job_id=job.id, recipient_name="C1", status=CertificateStatus.SUCCESS, pdf_path="a.pdf")
    db_session.add(c1)
    db_session.commit()

    recovered = recover_interrupted_jobs()
    assert recovered == 1

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


def test_recovery_all_certificates_failed(db_session):
    """Test recovery when all certificates in the interrupted job failed."""
    job = Job(
        title="All Failed but Processing",
        event_name="Event",
        issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 1, 1),
        status=JobStatus.PROCESSING,
        total_count=1,
        succeeded_count=0,
        failed_count=0
    )
    db_session.add(job)
    db_session.flush()

    c1 = Certificate(job_id=job.id, recipient_name="C1", status=CertificateStatus.FAILED, error_message="err")
    db_session.add(c1)
    db_session.commit()

    recovered = recover_interrupted_jobs()
    assert recovered == 1

    db_session.refresh(job)
    assert job.status == JobStatus.FAILED
