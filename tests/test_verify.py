def test_verify_successful_certificate(client, db_session):
    """
    Test GET /api/verify/{certificate_id}:
    - Returns valid=True, recipient_name, event_name, title, issuer_name, issue_date
    - Strictly never returns email addresses, job_ids, or file paths
    """
    payload = {
        "title": "Verified Credential",
        "event_name": "Authenticity 101",
        "issuer_name": "Tech Academy",
        "issue_date": "2026-10-01",
        "recipients": [{"name": "Verified Recipient", "email": "secret.email@example.com"}]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]

    certs_res = client.get(f"/api/jobs/{job_id}/certificates")
    cert_id = certs_res.json()["items"][0]["id"]

    verify_res = client.get(f"/api/verify/{cert_id}")
    assert verify_res.status_code == 200
    data = verify_res.json()

    assert data["valid"] is True
    assert data["recipient_name"] == "Verified Recipient"
    assert data["event_name"] == "Authenticity 101"
    assert data["title"] == "Verified Credential"
    assert data["issuer_name"] == "Tech Academy"
    assert data["issue_date"] == "2026-10-01"

    # Crucial security assertion: email, job_id, and file paths MUST NOT appear in verification
    assert "email" not in data
    assert "recipient_email" not in data
    assert "job_id" not in data
    assert "pdf_path" not in data


def test_verify_non_existent_or_failed_certificate_returns_404(client, db_session):
    """
    Public verification endpoint returns 404 for unknown IDs
    and for certificates that failed generation.
    """
    # 1. Unknown ID
    res_unknown = client.get("/api/verify/unknown-uuid-000")
    assert res_unknown.status_code == 404

    # 2. Failed certificate
    payload = {
        "title": "Failed Cert",
        "event_name": "Test",
        "issuer_name": "Tech Academy",
        "issue_date": "2026-10-01",
        "recipients": [{"name": "   ", "email": "blank@example.com"}]
    }
    res = client.post("/api/jobs", json=payload)
    job_id = res.json()["job_id"]
    certs_res = client.get(f"/api/jobs/{job_id}/certificates")
    failed_cert_id = certs_res.json()["items"][0]["id"]

    res_failed = client.get(f"/api/verify/{failed_cert_id}")
    assert res_failed.status_code == 404
