# Architecture & Design Decisions

This document details the technical decisions, alternatives evaluated, trade-offs accepted, empirical benchmark results, and production scaling roadmap for the Bulk Certificate Generator API.

---

## 1. Core Technical Decisions

### Decision 1: FastAPI over Flask / Django REST Framework
- **Choice**: FastAPI (with Starlette and Pydantic v2).
- **Alternatives Considered**: Flask, Django REST Framework (DRF).
- **Rationale**:
  - Native asynchronous I/O and lightweight `BackgroundTasks` support for non-blocking HTTP endpoints.
  - Automatic request parsing, validation, and auto-generated OpenAPI / Swagger interactive documentation.
  - Modern type safety with Python 3.12+ type hints.
- **Trade-offs**: Flask has a slightly simpler raw request model, but requires third-party plugins for OpenAPI, type validation, and async background execution. DRF brings heavy ORM coupling, migrations, and administrative overhead unnecessary for a dedicated microservice.

### Decision 2: SQLite by Default with PostgreSQL Compatibility
- **Choice**: SQLite default via `DATABASE_URL=sqlite:///./certificates.db`, powered by SQLAlchemy 2.0.
- **Alternatives Considered**: Pure PostgreSQL requirement, MySQL.
- **Rationale**:
  - Zero-configuration setup: runs out of the box on developer machines, continuous integration runners, and lightweight containers without needing external database containers.
  - Full relational capabilities: foreign keys enforced with `PRAGMA foreign_keys=ON;` on connection.
  - Switching to PostgreSQL in production requires only changing `DATABASE_URL=postgresql+psycopg2://user:pass@host/db`.
- **Trade-offs**: SQLite writes lock the entire database file during a transaction. To mitigate this under concurrency, we configured `connect_args={"timeout": 30.0, "check_same_thread": False}` and kept individual write transactions brief.

### Decision 3: BackgroundTasks behind a JobRunner Interface vs Celery
- **Choice**: FastAPI `BackgroundTasks` abstracted behind an abstract `JobRunner` interface.
- **Alternatives Considered**: Celery with Redis/RabbitMQ, RQ, ARQ.
- **Rationale**:
  - Simplicity and zero infrastructure overhead: runs within the application process without requiring Redis or extra worker daemon management.
  - Future-proof modularity: all execution logic is decoupled into `JobRunner.run_job(job_id)`. Swapping to Celery requires only implementing a `CeleryJobRunner` without modifying any endpoint or generator code.
- **Trade-offs**: FastAPI `BackgroundTasks` run in the same process memory space. If the server process crashes or restarts, background tasks are terminated. We solved this with an automated startup crash recovery routine that inspects orphaned `PROCESSING` jobs on startup.

### Decision 4: Per-Certificate Database Sessions & Transactions
- **Choice**: Background task creates its own isolated database session per certificate, committing after each certificate.
- **Alternatives Considered**: Single batch transaction committing at the end of the entire job.
- **Rationale**:
  - True fault isolation: if one certificate fails due to a rendering exception or unsupported character glyph, rolling back that certificate never impacts previously generated or subsequent certificates.
  - Live, accurate progress: polling clients observe incremental progress updates (`succeeded_count`, `failed_count`, `progress_percentage`) in real time.
  - Request isolation: the background runner never reuses the request-scoped database session, preventing race conditions or closed session errors.
- **Trade-offs**: Increased transaction overhead (1,000 commits for 1,000 certificates). However, local SSD benchmarks show 1,000 commits complete in under 2 minutes with minimal disk I/O impact.

### Decision 5: Lenient Schema & Decoupled Recipient Validation Strategy
- **Choice**: Pydantic request schema accepts lenient fields (`name: str | None`, `email: str | None`), while business validation rules live inside `app/services/validation.py`.
- **Alternatives Considered**: Strict Pydantic model validation (`EmailStr`, `min_length=1`).
- **Rationale**:
  - The business requirement mandates that individual bad recipients (empty name, name >100 characters, invalid email, duplicate email) must not cause an HTTP `422 Unprocessable Content` rejection for the entire bulk request.
  - Decoupling recipient-level validation allows valid recipients in a 500-student batch to generate while marking invalid rows as `FAILED` upfront with descriptive reasons.
- **Email Rules**:
  - Email is optional, but if provided, must match standard RFC 5322 regex.
  - Duplicate email checking applies strictly to non-null emails provided within the same batch. Recipients with omitted emails are not treated as duplicates.

### Decision 6: Local Disk Storage with Atomic Temp-File Writing
- **Choice**: Store PDFs on local filesystem under `storage/<job_id>/<certificate_id>.pdf`, writing to a temporary file (`.tmp`) first and renaming atomically upon success.
- **Alternatives Considered**: In-memory binary storage (storing raw PDF bytes in SQLite BLOBs or memory buffers).
- **Rationale**:
  - Crash safety: a killed or interrupted process leaves zero corrupt or partial PDF files.
  - Memory efficiency: generating 1,000 certificates in memory would cause significant RAM spikes. Writing to disk keeps peak memory consumption under 10 MB.
  - ZIP archives are built using disk-backed temporary files and deleted via `BackgroundTasks` after HTTP transmission.
- **Trade-offs**: Local disks are ephemeral in containerized cloud environments (e.g. AWS ECS / Kubernetes). Production environments will mount persistent network storage or upload to S3.

### Decision 7: ReportLab with Bundled DejaVu Sans & Glyph Verification
- **Choice**: Pure-Python ReportLab library with bundled DejaVu Sans TrueType font and strict glyph verification.
- **Alternatives Considered**: WeasyPrint, PyMuPDF, wkhtmltopdf.
- **Rationale**:
  - ReportLab is a pure Python dependency with zero OS-level binaries (unlike WeasyPrint which requires `cairo`, `pango`, and `gdk-pixbuf`).
  - Bundling DejaVu Sans guarantees consistent rendering of Latin, accented, Greek, and Cyrillic names across Windows, macOS, and Linux.
  - Glyph verification prevents rendering broken missing-glyph boxes ("tofu") for unsupported scripts (e.g. Telugu, Devanagari) by catching missing codepoints early and reporting them cleanly.

---

## 2. Empirical Performance & Concurrency Benchmarks

The benchmark suite (`scripts/benchmark.py`) yielded the following empirical metrics on the test hardware:

### Benchmark 1: 1,000 Recipients Batch
- **Total Recipients**: 1,000 valid recipients.
- **Total Generation Time**: 131.41 to 215.27 seconds depending on system disk I/O load.
- **Throughput**: ~4.6 to 7.6 certificates / second.
- **Peak Memory Usage (Measured)**:
  - **Python Heap Allocations (`tracemalloc`)**: Peak of **9.19 MB** (tracking Python objects and byte buffers).
  - **Operating System Process Resident Set Size (`psutil.Process().memory_info().rss`)**: Initial process RSS was **86.52 MB** at startup (Python interpreter runtime, loaded libraries, and font structures); peak process RSS during 1,000 certificate generation reached **111.86 MB** (a net process memory increase of 25.34 MB).
- **API Responsiveness**: `GET /api/jobs/{id}` remained responsive throughout the background generation loop, successfully reporting live progress.


### Benchmark 2: Concurrent Multi-Job Execution
- **Setup**: Two independent 100-recipient jobs submitted simultaneously in parallel threads.
- **Result**:
  - Job 1: 100/100 succeeded (`COMPLETED`).
  - Job 2: 100/100 succeeded (`COMPLETED`).
  - Total Concurrent Time: 6.97 seconds.
  - Database Errors: No locking errors observed in our concurrency test; busy timeout set to 30s (`connect_args={"timeout": 30.0}`).


---

## 3. Production Scaling Roadmap

To transition this service to handle millions of certificates per day, the following architecture is recommended:

```
[ Clients ] ---> [ Cloudflare / API Gateway (Rate Limiting, Auth) ]
                         |
                 [ FastAPI Web Nodes ]
                         |
         +---------------+---------------+
         |                               |
    [ PostgreSQL DB ]             [ Redis Message Broker ]
                                         |
                                  [ Celery Workers ]
                                  (Auto-scaling pool)
                                         |
                               [ S3 / Cloud Storage ]
```

1. **Distributed Task Queue (Celery + Redis)**:
   - Replace `BackgroundJobRunner` with a `CeleryJobRunner`.
   - Distribute PDF generation across scalable worker pods (e.g. Kubernetes Celery worker deployments), increasing throughput to hundreds of certificates per second.
2. **Object Storage (AWS S3 / Google Cloud Storage)**:
   - Store generated PDFs in Amazon S3 or Cloudflare R2 using pre-signed download URLs.
   - Eliminates container volume dependencies and reduces server bandwidth during large ZIP downloads.
3. **Authentication & Rate Limiting**:
   - Introduce API key / JWT authentication for job creation.
   - Enforce IP-based and user-based token bucket rate limits (e.g. via Redis) to prevent denial-of-service attempts.
4. **Automated File Retention & Cleanup**:
   - Establish lifecycle policies on generated certificates (e.g. ZIP files deleted after 24 hours; PDF certificates archived after 90 days).
