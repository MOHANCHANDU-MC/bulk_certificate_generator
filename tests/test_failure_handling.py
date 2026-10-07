from unittest.mock import patch

from app.models import Certificate, CertificateStatus, Job, JobStatus


def test_single_certificate_failure_isolation(client, db_session):
    """
    Test that when ONE certificate rendering fails (simulated via monkeypatch/mock),
    the failure is isolated to that certificate:
    - That certificate is marked FAILED with a clean error message.
    - All other valid certificates in the same job succeed.
    - The job reaches COMPLETED_WITH_ERRORS.
    """
    payload = {
        "title": "Failure Isolation Test",
        "event_name": "Resilience 101",
        "issuer_name": "Fault Tolerance Org",
        "issue_date": "2026-10-01",
        "recipients": [
            {"name": "Good Recipient 1", "email": "good1@example.com"},
            {"name": "Faulty Recipient", "email": "faulty@example.com"},
            {"name": "Good Recipient 2", "email": "good2@example.com"},
        ]
    }

    from app.services import job_runner

    real_generate_pdf = job_runner.generate_certificate_pdf

    def faulty_generate_pdf(data, output_path):
        if data.recipient_name == "Faulty Recipient":
            raise RuntimeError("Disk write simulation failed")
        return real_generate_pdf(data, output_path)

    with patch("app.services.job_runner.generate_certificate_pdf", side_effect=faulty_generate_pdf):
        res = client.post("/api/jobs", json=payload)
        assert res.status_code == 202
        job_id = res.json()["job_id"]

    job = db_session.query(Job).filter_by(id=job_id).first()
    assert job.status == JobStatus.COMPLETED_WITH_ERRORS
    assert job.total_count == 3
    assert job.succeeded_count == 2
    assert job.failed_count == 1

    certs = db_session.query(Certificate).filter_by(job_id=job_id).all()
    assert len(certs) == 3

    good_certs = [c for c in certs if c.recipient_name.startswith("Good")]
    faulty_certs = [c for c in certs if c.recipient_name == "Faulty Recipient"]

    assert len(good_certs) == 2
    for c in good_certs:
        assert c.status == CertificateStatus.SUCCESS
        assert c.pdf_path is not None
        assert c.error_message is None

    assert len(faulty_certs) == 1
    failed_c = faulty_certs[0]
    assert failed_c.status == CertificateStatus.FAILED
    assert failed_c.pdf_path is None
    assert "Failed to generate certificate PDF: RuntimeError" in failed_c.error_message
