from datetime import date, datetime, timezone
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models import CertificateStatus, JobStatus

T = TypeVar("T")


# ---------------------------------------------------------
# Request Models
# ---------------------------------------------------------

class RecipientInput(BaseModel):
    """
    Lenient recipient model.
    Accepts raw strings or None. No strict EmailStr or required non-empty string here,
    so that individual bad recipients never trigger a 422 HTTP validation error.
    Per-recipient validation takes place in services/validation.py.
    """
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    email: str | None = None


class JobCreate(BaseModel):
    """
    Schema for creating a bulk certificate job.
    Enforces required event fields and list constraints (1 to 1000 recipients).
    Violations here properly produce 422 Unprocessable Entity.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "title": "Certificate of Completion",
                "event_name": "Full-Stack Python Bootcamp",
                "issuer_name": "Tech Academy",
                "issue_date": "2026-10-15",
                "recipients": [
                    {
                        "name": "Alice Johnson",
                        "email": "alice.johnson@example.com",
                    },
                    {
                        "name": "Bob Smith",
                        "email": None,
                    },
                    {
                        "name": "   ",
                        "email": "invalid.user@example.com",
                    },
                ],
            },
            "examples": [
                {
                    "title": "Certificate of Completion",
                    "event_name": "Full-Stack Python Bootcamp",
                    "issuer_name": "Tech Academy",
                    "issue_date": "2026-10-15",
                    "recipients": [
                        {
                            "name": "Alice Johnson",
                            "email": "alice.johnson@example.com",
                        },
                        {
                            "name": "Bob Smith",
                            "email": None,
                        },
                        {
                            "name": "   ",
                            "email": "invalid.user@example.com",
                        },
                    ],
                }
            ],
        },
    )

    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Certificate title (e.g. Certificate of Completion)"
    )
    event_name: str = Field(..., min_length=1, max_length=255, description="Event or course name")
    issuer_name: str = Field(..., min_length=1, max_length=255, description="Issuing authority")

    issue_date: date = Field(..., description="Date of issuance (YYYY-MM-DD)")
    recipients: list[RecipientInput] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="List of recipients (1 to 1000 recipients allowed)"
    )


class ErrorResponse(BaseModel):
    """Standard HTTP error response payload."""
    detail: str


# ---------------------------------------------------------
# Response Models
# ---------------------------------------------------------

class JobCreatedResponse(BaseModel):
    """Response returned upon POST /api/jobs (202 Accepted)."""
    job_id: str
    status: JobStatus
    total_count: int
    message: str


class CertificateResponse(BaseModel):
    """Detail of an individual certificate."""
    id: str
    job_id: str
    recipient_name: str | None = None
    recipient_email: str | None = None
    status: CertificateStatus
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at", when_used="always")
    def serialize_datetime(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()



class PaginatedResponse(BaseModel, Generic[T]):
    """Standard pagination wrapper."""
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class JobStatusResponse(BaseModel):
    """Full progress status response for a job."""
    id: str
    title: str
    event_name: str
    issuer_name: str
    issue_date: date
    status: JobStatus
    total_count: int
    succeeded_count: int
    failed_count: int
    pending_count: int
    progress_percentage: float
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at", when_used="always")
    def serialize_datetime(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()



class CertificateVerifyResponse(BaseModel):
    """
    Public verification response (Rule 8: never expose recipient email address).
    """
    certificate_id: str
    recipient_name: str
    event_name: str
    title: str
    issuer_name: str
    issue_date: date
    status: CertificateStatus
