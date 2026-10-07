from app.models import Certificate, CertificateStatus, Job, JobStatus


def test_create_job_success_all_valid(client, db_session):
    """Test creating a job with all valid recipients returns 202 and correct counts."""
    payload = {
        "title": "Certificate of Excellence",
        "event_name": "FastAPI Workshop 2026",
        "issuer_name": "Tech Corp",
        "issue_date": "2026-05-15",
        "recipients": [
            {"name": "Alice Wonderland", "email": "alice@example.com"},
            {"name": "Bob Builder", "email": "bob@example.com"}
        ]
    }

    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 202
    data = response.json()

    assert "job_id" in data
    assert data["status"] == "PENDING"
    assert data["total_count"] == 2

    # Verify response body returned to client
    assert "job_id" in data
    assert data["status"] == "PENDING"
    assert data["total_count"] == 2

    # In TestClient, BackgroundTasks run to completion before client.post returns!
    # Assert final status and counts in database (Amendment Rule 3)
    job = db_session.query(Job).filter_by(id=data["job_id"]).first()
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.total_count == 2
    assert job.succeeded_count == 2
    assert job.failed_count == 0

    certs = db_session.query(Certificate).filter_by(job_id=job.id).all()
    assert len(certs) == 2
    for cert in certs:
        assert cert.status == CertificateStatus.SUCCESS
        assert cert.error_message is None
        assert cert.pdf_path is not None



def test_create_job_mixed_recipients(client, db_session):
    """
    Test creating a job with a mix of valid and invalid recipients.
    Job must still be accepted (202), invalid recipients marked FAILED immediately,
    and valid recipients marked PENDING.
    """
    payload = {
        "title": "Certificate of Attendance",
        "event_name": "Python Conference",
        "issuer_name": "Python Org",
        "issue_date": "2026-06-01",
        "recipients": [
            {"name": "Valid User", "email": "valid@example.com"},
            {"name": "   ", "email": "blank_name@example.com"},  # Blank name
            {"name": "Too Long " + "A" * 105, "email": "toolong@example.com"},  # Name > 100 chars
            {"name": "Invalid Email User", "email": "not-an-email"},  # Bad email
            {"name": "Duplicate One", "email": "dupe@example.com"},
            {"name": "Duplicate Two", "email": "dupe@example.com"},  # Duplicate email
            {"name": "Optional Email User", "email": None}  # Valid: optional email
        ]
    }

    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 202
    data = response.json()

    job_id = data["job_id"]
    job = db_session.query(Job).filter_by(id=job_id).first()
    # In TestClient, BackgroundTasks have finished: assert final status is COMPLETED_WITH_ERRORS
    assert job.status == JobStatus.COMPLETED_WITH_ERRORS
    assert job.total_count == 7
    # Valid (3): "Valid User", "Duplicate One" (1st occurrence), "Optional Email User" -> SUCCESS
    # Invalid (4): blank name, too long name, bad email, "Duplicate Two" (duplicate) -> FAILED
    assert job.succeeded_count == 3
    assert job.failed_count == 4

    certs = db_session.query(Certificate).filter_by(job_id=job_id).all()
    assert len(certs) == 7

    # Check status breakdown
    success_certs = [c for c in certs if c.status == CertificateStatus.SUCCESS]
    failed_certs = [c for c in certs if c.status == CertificateStatus.FAILED]

    assert len(success_certs) == 3
    assert len(failed_certs) == 4



    # Check failure error messages are informative
    error_messages = [c.error_message for c in failed_certs]
    assert any("blank" in msg for msg in error_messages if msg)
    assert any("exceeds maximum allowed length" in msg for msg in error_messages if msg)
    assert any("Invalid email address format" in msg for msg in error_messages if msg)
    assert any("Duplicate email" in msg for msg in error_messages if msg)


def test_create_job_all_invalid_recipients(client, db_session):
    """
    Requirement Amendment 4:
    If no recipient is valid, mark the job FAILED immediately and do not queue anything.
    """
    payload = {
        "title": "Certificate of Attendance",
        "event_name": "Python Conference",
        "issuer_name": "Python Org",
        "issue_date": "2026-06-01",
        "recipients": [
            {"name": "", "email": "bad1@example.com"},
            {"name": "Bad Email", "email": "not-an-email"}
        ]
    }

    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 202
    data = response.json()

    assert data["status"] == "FAILED"
    assert "no recipients were valid" in data["message"]

    job = db_session.query(Job).filter_by(id=data["job_id"]).first()
    assert job.status == JobStatus.FAILED
    assert job.failed_count == 2
    assert job.succeeded_count == 0
