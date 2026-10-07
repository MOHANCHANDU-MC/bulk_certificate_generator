# Generates standalone sample certificates across Latin, long names, accented, Cyrillic, and tests Telugu.
import datetime
import os
import sys

sys.path.insert(0, os.getcwd())

from app.services.certificate_generator import (
    CertificateRenderData,
    generate_certificate_pdf,
)

long_name = (
    "Dr. Elizabeth Alexandra Mary Windsor-Smithfield "
    "of Great Britain and Northern Ireland"
)

samples = [
    ("normal", "Alice Wonderland", "storage/sample_normal.pdf"),
    ("long", long_name, "storage/sample_long.pdf"),
    ("accented", "José Müller", "storage/sample_accented.pdf"),
    ("cyrillic", "Алексей Смирнов", "storage/sample_cyrillic.pdf"),
]


for label, name, path_str in samples:
    data = CertificateRenderData(
        certificate_id=f"sample-{label}-uuid",
        recipient_name=name,
        event_name="Full Stack Backend Certification",
        title="Certificate of Excellence",
        issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 10, 7)
    )
    generate_certificate_pdf(data, path_str)
    print(f"Generated {label} certificate at: {path_str}")
