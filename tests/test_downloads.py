import datetime
import io
import zipfile

from app.models import Certificate, CertificateStatus, Job, JobStatus


def test_single_pdf_download_success(client, db_session):
    """
    Test GET /api/jobs/{job_id}/certificates/{certificate_id}/download:
    - Returns 200 with media_type application/pdf
    - Content starts with %PDF- header
    """
    payload = {
        "title": "Download Test Course",
        "event_name": "API Mastery",
        "issuer_name": "Tech Academy",
        "issue_date": "2026-10-01",
        "recipients": [{"name": "Download Recipient", "email": "download@example.com"}]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]

    # Get certificate id
    certs_res = client.get(f"/api/jobs/{job_id}/certificates")
    cert_id = certs_res.json()["items"][0]["id"]

    dl_res = client.get(f"/api/jobs/{job_id}/certificates/{cert_id}/download")
    assert dl_res.status_code == 200
    assert "application/pdf" in dl_res.headers["content-type"]
    assert dl_res.content.startswith(b"%PDF-")


def test_single_download_wrong_job_id_returns_404(client, db_session):
    """Certificate must belong to the job_id specified in the URL, else 404."""
    payload = {
        "title": "Job Isolation",
        "event_name": "Security 101",
        "issuer_name": "Tech Academy",
        "issue_date": "2026-10-01",
        "recipients": [{"name": "Alice Security", "email": "alice.sec@example.com"}]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]

    certs_res = client.get(f"/api/jobs/{job_id}/certificates")
    cert_id = certs_res.json()["items"][0]["id"]

    # Request with wrong job_id
    wrong_job_id = "00000000-0000-0000-0000-000000000000"
    dl_res = client.get(f"/api/jobs/{wrong_job_id}/certificates/{cert_id}/download")
    assert dl_res.status_code == 404
    assert "not found" in dl_res.json()["detail"].lower()


def test_single_download_failed_certificate_returns_409(client, db_session):
    """Attempting to download a FAILED certificate returns 409 Conflict."""
    payload = {
        "title": "Failed Cert Download",
        "event_name": "Error Handling",
        "issuer_name": "Tech Academy",
        "issue_date": "2026-10-01",
        "recipients": [
            {"name": "Valid Student", "email": "valid@example.com"},
            {"name": "   ", "email": "blank@example.com"},  # Will fail
        ]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]

    certs_res = client.get(f"/api/jobs/{job_id}/certificates")
    items = certs_res.json()["items"]
    failed_item = [c for c in items if c["status"] == "FAILED"][0]

    dl_res = client.get(f"/api/jobs/{job_id}/certificates/{failed_item['id']}/download")
    assert dl_res.status_code == 409
    assert "not available for download" in dl_res.json()["detail"]


def test_single_download_path_traversal_refusal(client, db_session):
    """If a certificate row has an invalid or outside path, endpoint refuses with 404."""
    job = Job(
        title="Test", event_name="Event", issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 1, 1), status=JobStatus.COMPLETED,
        total_count=1, succeeded_count=1, failed_count=0
    )
    db_session.add(job)
    db_session.flush()

    traversal_cert = Certificate(
        job_id=job.id,
        recipient_name="Hacker",
        status=CertificateStatus.SUCCESS,
        pdf_path="../../etc/passwd"
    )
    db_session.add(traversal_cert)
    db_session.commit()

    res = client.get(f"/api/jobs/{job.id}/certificates/{traversal_cert.id}/download")
    assert res.status_code == 404


def test_zip_download_success_and_unique_filenames(client, db_session):
    """
    Test GET /api/jobs/{job_id}/download:
    - Generates ZIP in temporary file
    - Includes only SUCCESS certificates
    - Unique filenames inside ZIP when two recipients have the exact same name
    """
    payload = {
        "title": "Duplicate Names Event",
        "event_name": "Naming Conference",
        "issuer_name": "Tech Academy",
        "issue_date": "2026-10-01",
        "recipients": [
            {"name": "John Smith", "email": "john1@example.com"},
            {"name": "John Smith", "email": "john2@example.com"},
            {"name": "   ", "email": "blank@example.com"}  # Failed cert: excluded from ZIP
        ]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]

    zip_res = client.get(f"/api/jobs/{job_id}/download")
    assert zip_res.status_code == 200
    assert "application/zip" in zip_res.headers["content-type"]

    # Read zip bytes and inspect archive contents
    with zipfile.ZipFile(io.BytesIO(zip_res.content)) as zf:
        namelist = zf.namelist()
        assert len(namelist) == 2  # Only the 2 successful certificates
        assert namelist[0] != namelist[1]  # Filenames must be unique!
        for name in namelist:
            assert name.startswith("John_Smith_")
            assert name.endswith(".pdf")
            # Verify file content inside archive starts with %PDF-
            cert_content = zf.read(name)
            assert cert_content.startswith(b"%PDF-")


def test_zip_download_unknown_job_returns_404(client):
    """Attempting to download ZIP for a non-existent job returns 404."""
    res = client.get("/api/jobs/00000000-0000-0000-0000-000000000000/download")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_zip_download_pending_or_processing_job_returns_409(client, db_session):
    """Attempting to download ZIP while job is still PENDING or PROCESSING returns 409."""
    job = Job(
        title="Processing Job",
        event_name="Event",
        issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 1, 1),
        status=JobStatus.PROCESSING,
        total_count=10,
        succeeded_count=0,
        failed_count=0
    )
    db_session.add(job)
    db_session.commit()

    res = client.get(f"/api/jobs/{job.id}/download")
    assert res.status_code == 409
    assert "still in progress" in res.json()["detail"]


def test_single_pdf_download_non_ascii_filename_encoding(client, db_session):
    """Non-ASCII recipient names use filename*=UTF-8'' and safe ASCII fallback in headers."""
    payload = {
        "title": "International Summit",
        "event_name": "Global Python",
        "issuer_name": "Tech Academy",
        "issue_date": "2026-10-01",
        "recipients": [{"name": "José Müller", "email": "jose@example.com"}]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]

    certs_res = client.get(f"/api/jobs/{job_id}/certificates")
    cert_id = certs_res.json()["items"][0]["id"]

    dl_res = client.get(f"/api/jobs/{job_id}/certificates/{cert_id}/download")
    assert dl_res.status_code == 200
    content_disp = dl_res.headers.get("content-disposition", "")
    assert "filename*=" in content_disp
    assert "UTF-8''" in content_disp


