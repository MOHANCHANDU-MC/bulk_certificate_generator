# Bulk Certificate Generator API

A high-performance, fault-tolerant FastAPI backend service designed to accept bulk certificate generation requests, validate recipient batches, render professional landscape A4 PDF certificates using ReportLab, bundle certificates into downloadable ZIP archives, and expose public digital authenticity verification via QR codes.

---

## 1. Features
- **Bulk Job Submission**: Submit jobs containing up to 1,000 recipients in a single `POST` request.
- **Resilient Fault Isolation**: A failure on one certificate (due to bad format, duplicate email, unsupported character set, or rendering fault) never stops other valid certificates from being generated in the same job.
- **Accurate Status Tracking**: Live progress tracking via `GET /api/jobs/{id}` showing total, succeeded, failed, pending counts, and progress percentage.
- **Professional PDF Generation**: Landscape A4 vector certificates drawn using ReportLab with dynamic font autoscaling (from 38pt down to 18pt) and measured-width word-wrapping up to 3 lines.
- **QR Code Verification**: Every certificate embeds a dynamic QR code pointing directly to `GET /api/verify/{certificate_id}`.
- **Bulk ZIP Download**: Generates streaming ZIP archives directly from disk with unique, sanitized filenames for each recipient (`{clean_name}_{short_id}.pdf`).
- **Crash Recovery**: Automatically re-queues and resumes orphaned `PROCESSING` jobs upon application restart.
- **Clean Architecture**: Built using FastAPI, SQLAlchemy 2.0 with type-annotated models, and Pydantic v2.

---

## 2. Python Version & Requirements
- **Target Python Version**: Python 3.12+ (tested on Python 3.12 in CI and Docker; development verified on Python 3.14).
- Dependencies are pinned in `requirements.txt`.
- Bundled TrueType Font: DejaVu Sans (`app/templates/fonts/DejaVuSans.ttf`) is included for Unicode coverage (Latin, Cyrillic, Greek).

---

## 3. Supported Character Scripts & Limitations
- **Supported Scripts**:
  - Latin (including full accented characters such as `José Müller`, `René`, `Åsa`).
  - Cyrillic (e.g. `Алексей Смирнов`).
  - Greek (e.g. `Γιώργος Παπαδόపుλος`).
- **Indic Script Limitation**:
  - The bundled DejaVu Sans font does not cover Indic scripts (Telugu, Devanagari, Tamil, etc.), and ReportLab does not perform complex text shaping.
  - To prevent rendering corrupt boxes ("tofu"), the API inspects every character against the font's glyph table. If an unsupported character is detected, generation halts cleanly for that recipient with a clear status message:
    `"Name contains N unsupported character(s): '...' and M more."`
  - Indic script support (using Noto Serif / Sans fonts with HarfBuzz text shaping) is planned for future scope.

---

## 4. Setup & Running Locally

### Step 1: Clone and Set Up Virtual Environment
```bash
git clone <repo-url>
cd BCG
python -m venv venv
```

Activate the virtual environment:
- **Windows (PowerShell)**:
  ```powershell
  .\venv\Scripts\Activate.ps1
  ```
- **macOS / Linux**:
  ```bash
  source venv/bin/activate
  ```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables (Optional)
```bash
cp .env.example .env
```
Default settings connect to a local SQLite database (`certificates.db`) and store PDFs in `storage/`.

### Step 4: Run the Application
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
The interactive Swagger API documentation is available at `http://localhost:8000/docs`.

> **Note on Database Migrations**: Database tables are created automatically on startup in the lifespan hook (`Base.metadata.create_all(bind=engine)`). Migrations using Alembic are planned as the next step for production schema lifecycle management.

---

## 5. Running Tests & Linter

Run tests with test coverage:
```bash
pytest --cov=app --cov-report=term-missing
```

Run code formatting and lint check:
```bash
ruff check .
```

---

## 6. API Summary Table

| Method | Endpoint | Description | Status Code |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/jobs` | Submit a bulk certificate generation request | `202 Accepted` |
| `GET` | `/api/jobs/{job_id}` | Check job progress, counts, and status | `200 OK` |
| `GET` | `/api/jobs/{job_id}/certificates` | Get paginated certificate items for a job | `200 OK` |
| `GET` | `/api/jobs/{job_id}/certificates/{certificate_id}/download` | Download single certificate PDF | `200 OK` / `409` |
| `GET` | `/api/jobs/{job_id}/download` | Download ZIP archive of all successful PDFs | `200 OK` / `409` |
| `GET` | `/api/verify/{certificate_id}` | Public endpoint verifying certificate authenticity | `200 OK` / `404` |
| `GET` | `/health` | Application & database connectivity healthcheck | `200 OK` / `503` |

---

## 7. Example cURL Commands

### 1. Submit a Certificate Generation Request

Using the sample payload file:
```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/sample_job.json"
```

Or submitting directly with an inline JSON body:
```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Certificate of Completion",
    "event_name": "Full Stack Backend Bootcamp",
    "issuer_name": "Tech Academy",
    "issue_date": "2026-10-07",
    "recipients": [
      {"name": "Alice Wonderland", "email": "alice@example.com"},
      {"name": "José Müller", "email": "jose@example.com"},
      {"name": "   ", "email": "blank@example.com"},
      {"name": "Bob Builder", "email": "not-an-email"}
    ]
  }'
```
*Response (`202 Accepted`):*
```json
{
  "job_id": "4e284dc2-baa9-4cdb-9ab7-77778ae4a8f5",
  "status": "PENDING",
  "total_count": 4,
  "message": "Job accepted and queued for processing."
}
```


### 2. Check Job Status & Progress
```bash
curl http://localhost:8000/api/jobs/40b1ae56-02e0-47bf-8f55-2fffaea7eb75
```
*Response (`200 OK`):*
```json
{
  "id": "40b1ae56-02e0-47bf-8f55-2fffaea7eb75",
  "title": "Certificate of Completion",
  "event_name": "Full Stack Backend Bootcamp",
  "issuer_name": "Tech Academy",
  "issue_date": "2026-10-07",
  "status": "COMPLETED_WITH_ERRORS",
  "total_count": 4,
  "succeeded_count": 2,
  "failed_count": 2,
  "pending_count": 0,
  "progress_percentage": 100.0,
  "created_at": "2026-10-07T13:30:00+00:00",
  "updated_at": "2026-10-07T13:30:01+00:00"
}
```

### 3. List Certificates with Pagination
```bash
curl "http://localhost:8000/api/jobs/40b1ae56-02e0-47bf-8f55-2fffaea7eb75/certificates?page=1&page_size=20"
```

### 4. Download Single Certificate PDF
```bash
curl -O -J http://localhost:8000/api/jobs/40b1ae56-02e0-47bf-8f55-2fffaea7eb75/certificates/<CERTIFICATE_ID>/download
```

### 5. Download ZIP Archive of all Certificates
```bash
curl -O -J http://localhost:8000/api/jobs/40b1ae56-02e0-47bf-8f55-2fffaea7eb75/download
```

### 6. Verify Certificate Authenticity
```bash
curl http://localhost:8000/api/verify/<CERTIFICATE_ID>
```
*Response (`200 OK`):*
```json
{
  "valid": true,
  "recipient_name": "Alice Wonderland",
  "event_name": "Full Stack Backend Bootcamp",
  "title": "Certificate of Completion",
  "issuer_name": "Tech Academy",
  "issue_date": "2026-10-07"
}
```

---

## 8. Running with Docker & Docker Compose

### Using Docker
```bash
docker build -t bulk-cert-api:latest .
docker run -p 8000:8000 -v $(pwd)/storage:/app/storage bulk-cert-api:latest
```

### Using Docker Compose
```bash
docker compose up --build -d
```

---

## 9. Architecture & File Structure

```
BCG/
├── app/
│   ├── main.py                     # FastAPI app, lifespan crash recovery, health endpoint
│   ├── config.py                   # Pydantic Settings configuration
│   ├── database.py                 # SQLAlchemy 2.0 engine, sessions, foreign key hooks
│   ├── models.py                   # Job and Certificate ORM models
│   ├── schemas.py                  # Pydantic v2 schemas and UTC serializers
│   ├── routers/
│   │   ├── jobs.py                 # Job creation, status, certificates list, ZIP download
│   │   ├── certificates.py         # Single certificate PDF download
│   │   └── verify.py               # Public QR authenticity verification
│   ├── services/
│   │   ├── validation.py           # Recipient validation logic
│   │   ├── certificate_generator.py# ReportLab PDF engine, glyph checks, auto font-scale
│   │   ├── job_service.py          # Batch orchestration and DB transactions
│   │   ├── job_runner.py           # BackgroundJobRunner interface with isolated sessions
│   │   ├── file_utils.py           # Safe filename sanitization
│   │   └── recovery.py             # Startup crash recovery
│   └── templates/fonts/
│       ├── DejaVuSans.ttf          # Bundled TrueType font
│       └── LICENSE                 # Font license
├── scripts/
│   ├── benchmark.py                # 1,000 recipient and concurrency test benchmark
│   └── generate_samples.py         # Sample certificate PDF generator
├── tests/                          # 38 automated unit and integration tests

├── Dockerfile                      # python:3.12-slim non-root containerfile
├── docker-compose.yml              # Local docker compose setup
├── .github/workflows/ci.yml        # GitHub Actions CI pipeline
├── DECISIONS.md                    # In-depth architectural trade-offs & scaling plan
└── docs/WALKTHROUGH.md             # Code explanation, request lifecycle, interview Q&A
```

---

## 10. Learnings & Future Scope
- **Learnings**:
  - Combining per-certificate database transactions with lenient request schemas guarantees true fault isolation.
  - Generating PDFs directly on disk prevents high memory spikes even during large batches (1,000 certificates consume <10 MB peak memory).
- **Future Scope**:
  - Support for Indic and complex scripts using Noto Sans fonts combined with the HarfBuzz / uharfbuzz text-shaping engine.
  - Scaled distributed execution using Celery + Redis workers and AWS S3 / Cloud Storage for persistent PDF archiving.
  - Production database schema migrations using Alembic.
