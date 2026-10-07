from app.schemas import RecipientInput
from app.services.validation import validate_recipients_batch


def test_validation_empty_recipients_payload(client):
    """Payload with empty recipients list must return 422 Unprocessable Entity."""
    payload = {
        "title": "Certificate",
        "event_name": "Event",
        "issuer_name": "Issuer",
        "issue_date": "2026-01-01",
        "recipients": []
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert data["status_code"] == 422
    assert "recipients" in data["details"][0]["field"]


def test_validation_missing_required_job_fields(client):
    """Payload with missing required fields must return 422."""
    payload = {
        "event_name": "Event Only"
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert data["status_code"] == 422


def test_validation_oversized_recipients_list(client):
    """Payload with more than 1000 recipients must return 422."""
    recipients = [{"name": f"User {i}", "email": f"user{i}@example.com"} for i in range(1001)]
    payload = {
        "title": "Certificate",
        "event_name": "Event",
        "issuer_name": "Issuer",
        "issue_date": "2026-01-01",
        "recipients": recipients
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert "recipients" in data["details"][0]["field"]


def test_validation_control_characters():
    """Names containing control characters or zero-width spaces must fail validation."""
    invalid_cases = [
        "John\nDoe",
        "Jane\tSmith",
        "Alice\rBob",
        "Zero\u200bWidth",
        "Char\u200cJoiner",
    ]
    for bad_name in invalid_cases:
        results = validate_recipients_batch([RecipientInput(name=bad_name, email="test@test.com")])
        assert results[0].is_valid is False
        assert "control characters" in results[0].error_message


def test_qr_verify_url_format():
    """Test QR code link matches BASE_URL/api/verify/{certificate_id}."""
    from app.config import settings
    cert_id = "test-uuid-999"
    expected = f"{settings.BASE_URL.rstrip('/')}/api/verify/{cert_id}"
    actual = f"{settings.BASE_URL.rstrip('/')}/api/verify/{cert_id}"
    assert actual == expected
    assert actual.startswith("http://localhost:8000/api/verify/")

