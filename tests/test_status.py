import datetime

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.services.job_runner import job_runner
from app.services.recovery import recover_interrupted_jobs


def test_job_status_progress_calculation(client, db_session):
    """
    Test GET /api/jobs/{job_id} returns accurate progress percentage,
    counts (succeeded + failed + pending == total), and 404 for unknown job.
    """
    # 1. Test 404 for unknown job
    res_404 = client.get("/api/jobs/non-existent-uuid")
    assert res_404.status_code == 404
    assert "not found" in res_404.json()["detail"].lower()

    # 2. Create job via API
    payload = {
        "title": "Data Engineering Summit",
        "event_name": "Distributed Pipelines 101",
        "issuer_name": "Data Org",
        "issue_date": "2026-08-01",
        "recipients": [
            {"name": "Valid Recipient A", "email": "a@data.com"},
            {"name": "Valid Recipient B", "email": "b@data.com"},
            {"name": "   ", "email": "blank@data.com"},  # Invalid: blank name
        ]
    }
    res = client.post("/api/jobs", json=payload)
    assert res.status_code == 202
    job_id = res.json()["job_id"]

    # In TestClient, BackgroundTasks run synchronously before post() returns!
    status_res = client.get(f"/api/jobs/{job_id}")
    assert status_res.status_code == 200
    data = status_res.json()

    assert data["id"] == job_id
    assert data["total_count"] == 3
    assert data["succeeded_count"] == 2
    assert data["failed_count"] == 1
    assert data["pending_count"] == 0
    assert data["status"] == "COMPLETED_WITH_ERRORS"
    assert data["progress_percentage"] == 100.0


def test_health_check_endpoint(client):
    """Test GET /health returns 200 with status ok."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


def test_health_check_database_failure(client):
    """Test GET /health returns 503 when the database check fails."""
    from unittest.mock import MagicMock

    from app.database import get_db
    from app.main import app

    def failing_get_db():

        mock_db = MagicMock()
        mock_db.execute.side_effect = RuntimeError("Database connection timed out")
        yield mock_db

    app.dependency_overrides[get_db] = failing_get_db
    try:
        res = client.get("/health")
        assert res.status_code == 503
        data = res.json()
        assert data["status"] == "unhealthy"
        assert data["database"] == "disconnected"
    finally:
        app.dependency_overrides.pop(get_db, None)



def test_timezone_aware_utc_serialization(client, db_session):
    """
    Test that API responses return datetimes as timezone-aware UTC

    (ISO 8601 with +00:00 or Z offset), including after reading from SQLite.
    """
    payload = {
        "title": "Timezone Test",
        "event_name": "DateTime Event",
        "issuer_name": "Tech Academy",
        "issue_date": "2026-10-01",
        "recipients": [{"name": "TZ User", "email": "tz@example.com"}]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]

    status_res = client.get(f"/api/jobs/{job_id}")
    assert status_res.status_code == 200
    data = status_res.json()

    created_at_str = data["created_at"]
    updated_at_str = data["updated_at"]

    # Must contain timezone indicator (+00:00 or Z)
    assert "+00:00" in created_at_str or created_at_str.endswith("Z")
    assert "+00:00" in updated_at_str or updated_at_str.endswith("Z")

    certs_res = client.get(f"/api/jobs/{job_id}/certificates")
    cert_data = certs_res.json()["items"][0]
    cert_created_at = cert_data["created_at"]
    assert "+00:00" in cert_created_at or cert_created_at.endswith("Z")



def test_job_certificates_pagination_and_isolation(client, db_session):
    """
    Test GET /api/jobs/{job_id}/certificates pagination:
    - Verifies page, page_size, total, total_pages
    - Ensures internal pdf_path is never exposed in the response
    """
    payload = {
        "title": "API Conference",
        "event_name": "REST Best Practices",
        "issuer_name": "Web Standard Org",
        "issue_date": "2026-08-01",
        "recipients": [
            {"name": f"Attendee {i:02d}", "email": f"attendee{i}@example.com"}
            for i in range(1, 26)
        ]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]

    # Page 1 (default page_size = 20)
    page1 = client.get(f"/api/jobs/{job_id}/certificates?page=1&page_size=10")
    assert page1.status_code == 200
    p1_data = page1.json()
    assert p1_data["total"] == 25
    assert p1_data["page"] == 1
    assert p1_data["page_size"] == 10
    assert p1_data["total_pages"] == 3
    assert len(p1_data["items"]) == 10

    # Ensure pdf_path is NOT exposed in the output schema
    for item in p1_data["items"]:
        assert "pdf_path" not in item
        assert item["status"] == "SUCCESS"

    # Page 3 (remaining 5 items)
    page3 = client.get(f"/api/jobs/{job_id}/certificates?page=3&page_size=10")
    assert page3.status_code == 200
    p3_data = page3.json()
    assert len(p3_data["items"]) == 5


def test_idempotent_runner_execution(client, db_session):
    """
    Running job_runner twice on the same job must be completely idempotent:
    already successful certificates are skipped and counts remain consistent.
    """
    payload = {
        "title": "Cloud Computing Workshop",
        "event_name": "Cloud Arch",
        "issuer_name": "Cloud Academy",
        "issue_date": "2026-09-01",
        "recipients": [{"name": "Cloud Engineer", "email": "cloud@example.com"}]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]

    # First check status is COMPLETED
    job = db_session.query(Job).filter_by(id=job_id).first()
    assert job.status == JobStatus.COMPLETED
    assert job.succeeded_count == 1

    # Re-run runner explicitly
    job_runner.run_job(job_id)

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED
    assert job.succeeded_count == 1
    assert job.failed_count == 0


def test_startup_recovery_routine(db_session):
    """
    Test startup recovery:
    An interrupted job in PROCESSING state with pending certificates
    is re-queued and processed to completion.
    """
    # Create an orphaned job directly in the database
    orphaned_job = Job(
        title="Interrupted Course",
        event_name="Crash Recovery 101",
        issuer_name="Recovery Org",
        issue_date=datetime.date(2026, 9, 1),
        status=JobStatus.PROCESSING,
        total_count=2,
        succeeded_count=0,
        failed_count=0
    )
    db_session.add(orphaned_job)
    db_session.flush()

    cert1 = Certificate(
        job_id=orphaned_job.id,
        recipient_name="Already Finished Student",
        recipient_email="finished@example.com",
        status=CertificateStatus.SUCCESS,
        pdf_path="dummy/path.pdf"
    )
    cert2 = Certificate(
        job_id=orphaned_job.id,
        recipient_name="Pending Student",
        recipient_email="pending@example.com",
        status=CertificateStatus.PENDING
    )
    orphaned_job.succeeded_count = 1
    db_session.add_all([cert1, cert2])
    db_session.commit()

    # Execute recovery function
    recovered = recover_interrupted_jobs()
    assert recovered == 1

    db_session.refresh(orphaned_job)
    db_session.refresh(cert2)
    assert orphaned_job.status == JobStatus.COMPLETED
    assert orphaned_job.succeeded_count == 2
    assert orphaned_job.failed_count == 0
    assert cert2.status == CertificateStatus.SUCCESS
