# Codebase Walkthrough & Interview Guide

This guide is written for engineers who want to understand, explain, and modify every line of the Bulk Certificate Generator API in a technical interview.

---

## 1. File-by-File Explanation

### Core Application
- **`app/main.py`**:
  The application entrypoint. Configures the FastAPI app instance, custom 422 validation exception handlers, CORS, the `/health` database ping endpoint, and registers all routers. It contains the `lifespan` context manager which executes table creation and invokes the crash recovery routine on startup.
- **`app/config.py`**:
  Application settings powered by `pydantic-settings`. Reads values from environment variables or `.env` files with type validation (`BASE_URL`, `DATABASE_URL`, `STORAGE_DIR`, `FONT_PATH`).
- **`app/database.py`**:
  SQLAlchemy 2.0 database engine, session factory (`SessionLocal`), and `get_db` generator dependency. It registers an event listener for SQLite connections enabling `PRAGMA foreign_keys=ON;` and configures a 30-second busy timeout.
- **`app/models.py`**:
  Defines SQLAlchemy 2.0 ORM models using `Mapped[...]` and `mapped_column(...)`. Contains `Job` and `Certificate` tables, enums (`JobStatus`, `CertificateStatus`), foreign keys with cascade deletion, and indexing on `(job_id, status)`.
- **`app/schemas.py`**:
  Pydantic v2 schemas for requests and responses. Defines `RecipientInput` (lenient string fields), `JobCreate` (enforces 1–1000 recipients and required fields), `JobStatusResponse` (progress tracking), `CertificateResponse`, and custom field serializers formatting all datetimes as timezone-aware UTC ISO 8601 strings.

### Routers
- **`app/routers/jobs.py`**:
  Handles job creation (`POST /api/jobs`, returns `202 Accepted`), status inspection (`GET /api/jobs/{id}`), paginated certificates (`GET /api/jobs/{id}/certificates`), and bulk ZIP archive download (`GET /api/jobs/{id}/download`).
- **`app/routers/certificates.py`**:
  Handles single PDF download (`GET /api/jobs/{job_id}/certificates/{cert_id}/download`). Verifies job ownership, ensures status is `SUCCESS` (returns 409 otherwise), guards against directory traversal, and returns `FileResponse` with safe RFC 5987 headers.
- **`app/routers/verify.py`**:
  Public verification endpoint (`GET /api/verify/{certificate_id}`). Checks if certificate is `SUCCESS` and returns only public metadata (`recipient_name`, `event_name`, `issuer_name`, `issue_date`), strictly omitting email addresses and server paths.

### Services
- **`app/services/validation.py`**:
  Per-recipient validation engine. Checks names for non-emptiness, maximum 100 characters, absence of control/zero-width characters, optional RFC 5322 email syntax, and in-batch email uniqueness.
- **`app/services/certificate_generator.py`**:
  Vector PDF drawing engine using ReportLab. Registers DejaVu Sans once globally, validates recipient characters against the TrueType glyph table (raising `UnsupportedCharactersError` if unsupported), generates an in-memory QR code linking to verification, autoscales font size from 38pt down to 18pt, wraps up to 3 lines by measured width, and writes to a temporary file before atomically renaming.
- **`app/services/job_runner.py`**:
  Implements the `JobRunner` abstract interface with `BackgroundJobRunner`. Runs in the background, creates an isolated DB session per certificate, commits after each record, catches exceptions, and updates job totals.
- **`app/services/recovery.py`**:
  Crash recovery routine executed during app startup. Re-queues orphaned `PROCESSING` jobs.
- **`app/services/file_utils.py`**:
  Sanitizes filenames to prevent filesystem exploits or traversal characters.

---

## 2. Request Lifecycle: `POST /api/jobs` to PDF Download

```
1. Client submits POST /api/jobs with JSON payload.
2. Pydantic validates payload structure (min 1, max 1000 recipients, required title/event/issuer/date).
   - If invalid: Returns 422 immediately.
3. jobs.py invokes create_certificate_job in job_service.py.
4. validation.py inspects each recipient:
   - Valid recipient: Marked PENDING.
   - Invalid recipient: Marked FAILED with exact error reason.
5. If all recipients are invalid: Job marked FAILED immediately, no task queued.
6. If >=1 valid: Job created as PENDING, committed to DB, 202 Accepted returned with job_id.
7. BackgroundTasks enqueues job_runner.run_job(job_id).
8. job_runner:
   - Marks job as PROCESSING.
   - Loops through certificates. For each PENDING certificate:
     - Opens new DB session.
     - Calls certificate_generator.py.
     - Writes PDF to storage/<job_id>/<cert_id>.pdf atomically via .tmp file.
     - Commits certificate as SUCCESS and increments succeeded_count.
     - If error occurs: Commits certificate as FAILED with error_message and increments failed_count.
   - Derives final job status (COMPLETED, COMPLETED_WITH_ERRORS, or FAILED).
9. Client polls GET /api/jobs/{job_id} until status is final.
10. Client downloads PDF or ZIP archive via download endpoints.
```

---

## 3. Top 15 Technical Interview Questions & Answers

### Q1: Why did you choose FastAPI over Flask or Django?
**Answer**: FastAPI provides asynchronous I/O, automatic OpenAPI documentation, and native Pydantic v2 data validation out of the box with high performance. Unlike Django, it has no heavy ORM or migration bloat, making it ideal for a focused microservice.

### Q2: Why did you decouple recipient validation from the Pydantic schema?
**Answer**: If recipient validation lived in Pydantic, a single invalid email or blank name in a batch of 1,000 students would reject the entire request with an HTTP 422 error. By using lenient string schemas and custom validation in `services/validation.py`, we accept the batch, mark invalid recipients as `FAILED` upfront, and successfully generate all valid certificates.

### Q3: Why does the background task open its own database session per certificate?
**Answer**: The request database session closes as soon as the HTTP 202 response returns to the client. Background tasks executing on a closed session trigger database errors. Creating a dedicated session per certificate ensures thread safety, session independence, and per-certificate transaction commits.

### Q4: Why commit to the database after each certificate rather than once at the end?
**Answer**: Committing after each certificate ensures accurate, real-time progress for polling clients, and guarantees that if a crash or exception occurs on certificate #500, certificates #1 through #499 remain safely committed as `SUCCESS`.

### Q5: How do you prevent partial or corrupted PDF files if the server restarts mid-generation?
**Answer**: We write PDF files to a temporary file (`.tmp`) in the same directory first. Only after ReportLab finishes drawing and closes the canvas do we atomically rename the temporary file to the final `.pdf` path.

### Q6: Why did you bundle DejaVu Sans instead of using ReportLab's default Helvetica?
**Answer**: ReportLab's built-in 14 Type 1 fonts (like Helvetica) only support standard ASCII/WinAnsi characters. Names containing accents (José Müller), Cyrillic, or Greek render as errors or question marks. Bundling DejaVu Sans provides broad Unicode glyph coverage across all operating systems.

### Q7: Why do Indic names like Telugu or Hindi fail, and how is it handled?
**Answer**: DejaVu Sans does not include Indic glyphs, and ReportLab lacks complex text shaping engines (like HarfBuzz) needed for Indic conjuncts. Before rendering, we inspect each character against the font's glyph table. If unsupported characters are found, we raise `UnsupportedCharactersError`, cleanly marking that certificate as `FAILED` with a clear explanation without crashing the job.

### Q8: How does the application handle crashes while jobs are in `PROCESSING`?
**Answer**: During application startup in the FastAPI lifespan hook (`app/main.py`), a recovery routine searches for jobs left in `PROCESSING`. If unfinished certificates remain in `PENDING`, the job is marked `PENDING` and re-queued into the `JobRunner`. If all certificates are already finished, its status is finalized.

### Q9: Why is the `JobRunner` abstracted behind an interface?
**Answer**: It establishes a clean separation between business logic and the execution runtime. FastAPI `BackgroundTasks` works for single-server setups, but if we need distributed workers, we can implement `CeleryJobRunner` with Redis without modifying endpoints, database models, or generator code.

### Q10: How do you prevent path traversal attacks when downloading certificates?
**Answer**: The file path is read strictly from the database, never from user input. Furthermore, `download_single_certificate` resolves the file path and verifies using `path.relative_to(STORAGE_DIR)` that the target file resides strictly within the storage directory.

### Q11: How do you handle filename collisions in the ZIP archive when two recipients have the same name?
**Answer**: When writing files into the zip archive (`zipfile.write`), we use an archive name constructed from the sanitized recipient name and a short certificate UUID suffix (`{sanitized_name}_{cert_id[:8]}.pdf`).

### Q12: Why are ZIP archives written to temporary files on disk rather than in-memory?
**Answer**: A job with 1,000 certificates would require generating a 25+ MB archive entirely in RAM. Creating the ZIP on disk avoids memory pressure, and FastAPI `BackgroundTasks` automatically deletes the temporary file once the stream finishes.

### Q13: Why is SQLite suitable for this assignment, and what are its concurrency limitations?
**Answer**: SQLite requires zero external services and works identically across all test environments. Its limitation is database-level locking on writes. We mitigated this by setting a 30-second busy timeout (`timeout: 30.0`) and keeping write transactions short. For production multi-instance workloads, switching to PostgreSQL requires only updating `DATABASE_URL`.

### Q14: How does the `/health` endpoint verify database health?
**Answer**: Instead of merely returning a static JSON response, it executes `SELECT 1` on the active SQLAlchemy session. If database connectivity is broken, it catches the exception and returns `503 Service Unavailable`.

### Q15: Why are recipient emails excluded from the public `/api/verify` endpoint?
**Answer**: To protect user privacy. QR codes printed on physical or digital certificates are public. Anyone scanning the QR code should be able to verify authenticity (name, event, date, issuer) without harvesting the recipient's personal email address.

---

## 4. "Change a Requirement" Scenarios

### Scenario 1: Add a Retry Mechanism for Failed Certificates
- **Requirement**: Allow clients to trigger retries for failed certificates via `POST /api/jobs/{job_id}/retry`.
- **Where to change**:
  1. In `app/services/job_service.py`, add a function `reset_failed_certificates(db, job_id)` that queries `Certificate` records with status `FAILED`, sets their status back to `PENDING`, and updates `job.failed_count`.
  2. In `app/routers/jobs.py`, add a `POST /{job_id}/retry` endpoint that calls `reset_failed_certificates` and enqueues `job_runner.run_job(job_id)`.

### Scenario 2: Add an Optional Recipient Field (e.g. Grade / Honors)
- **Requirement**: Support an optional `grade` field (e.g. "Distinction") printed on the certificate.
- **Where to change**:
  1. `app/models.py`: Add `grade: Mapped[str | None] = mapped_column(String(50), nullable=True)` to `Certificate`.
  2. `app/schemas.py`: Add `grade: str | None = None` to `RecipientInput`.
  3. `app/services/job_service.py`: Pass `grade` from input to `Certificate(...)`.
  4. `app/services/certificate_generator.py`: Add `grade` to `CertificateRenderData`, and draw `"Awarded with: " + data.grade` conditionally below the event name.

### Scenario 3: Switch Execution Runtime from BackgroundTasks to Celery + Redis
- **Where to change**:
  1. Create `app/services/celery_app.py` configuring Celery with Redis broker: `celery = Celery("tasks", broker=settings.REDIS_URL)`.
  2. In `app/services/job_runner.py`, create `CeleryJobRunner(JobRunner)`:
     ```python
     @celery.task
     def celery_run_job_task(job_id: str):
         BackgroundJobRunner().run_job(job_id)

     class CeleryJobRunner(JobRunner):
         def run_job(self, job_id: str) -> None:
             celery_run_job_task.delay(job_id)
     ```
  3. In `app/services/job_runner.py`, change the singleton export:
     `job_runner: JobRunner = CeleryJobRunner() if settings.USE_CELERY else BackgroundJobRunner()`.
  4. No endpoint, validation, or PDF generation code needs to be modified.
