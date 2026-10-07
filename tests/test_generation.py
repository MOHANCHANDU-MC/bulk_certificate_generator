import datetime
from pathlib import Path

import pytest

from app.services.certificate_generator import (
    CertificateRenderData,
    UnsupportedCharactersError,
    generate_certificate_pdf,
    validate_recipient_name_glyphs,
)


@pytest.fixture
def temp_cert_dir(tmp_path: Path) -> Path:
    return tmp_path / "certs"


def test_generate_pdf_normal_name(temp_cert_dir: Path):
    """Test generating standard PDF certificate: valid file, non-empty, and %PDF- header."""
    data = CertificateRenderData(
        certificate_id="cert-101",
        recipient_name="Alice Wonderland",
        event_name="FastAPI Bootcamp",
        title="Certificate of Achievement",
        issuer_name="Python Academy",
        issue_date=datetime.date(2026, 6, 15)
    )
    dest_path = temp_cert_dir / "alice.pdf"
    result = generate_certificate_pdf(data, dest_path)

    assert result.exists()
    assert result.stat().st_size > 5000  # Non-trivial PDF size

    with open(result, "rb") as f:
        header = f.read(5)
    assert header == b"%PDF-"


def test_generate_pdf_long_name_auto_shrink(temp_cert_dir: Path):
    """Test generating PDF with a very long name (exercises auto font-shrinking loop)."""
    long_name = "Sir Bartholomew Montgomery-Cunningham of Upper Wallington Upon Tyne"
    data = CertificateRenderData(
        certificate_id="cert-102",
        recipient_name=long_name,
        event_name="Advanced Distributed Systems Mastery",
        title="Certificate of Excellence",
        issuer_name="Engineering Institute",
        issue_date=datetime.date(2026, 6, 15)
    )
    dest_path = temp_cert_dir / "long_name.pdf"
    result = generate_certificate_pdf(data, dest_path)

    assert result.exists()
    with open(result, "rb") as f:
        header = f.read(5)
    assert header == b"%PDF-"


def test_generate_pdf_non_ascii_supported_scripts(temp_cert_dir: Path):
    """
    Test generating PDFs with non-ASCII supported scripts:
    Latin accented, Greek, and Cyrillic names.
    """
    test_names = [
        "José Müller",
        "Γιώργος Παπαδόπουλος",
        "Алексей Смирнов"
    ]
    for idx, name in enumerate(test_names):
        data = CertificateRenderData(
            certificate_id=f"cert-script-{idx}",
            recipient_name=name,
            event_name="International Open Source Summit",
            title="Certificate of Honor",
            issuer_name="Global Tech Foundation",
            issue_date=datetime.date(2026, 7, 20)
        )
        dest_path = temp_cert_dir / f"script_{idx}.pdf"
        result = generate_certificate_pdf(data, dest_path)
        assert result.exists()
        with open(result, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-"


def test_generate_pdf_unspaced_100_char_name(temp_cert_dir: Path):
    """
    Test generating PDF with a 100-character unspaced name.
    Verifies that measured-width wrapping breaks the long word across up to 3 lines.
    """
    unspaced_name = "A" * 100
    data = CertificateRenderData(
        certificate_id="cert-103",
        recipient_name=unspaced_name,
        event_name="Extreme Length Stress Test",
        title="Certificate of Endurance",
        issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 6, 15)
    )
    dest_path = temp_cert_dir / "unspaced_100.pdf"
    result = generate_certificate_pdf(data, dest_path)

    assert result.exists()
    with open(result, "rb") as f:
        header = f.read(5)
    assert header == b"%PDF-"


def test_generate_pdf_unsupported_indic_script(temp_cert_dir: Path):
    """
    Test that names with unsupported Indic scripts (e.g. Telugu, Devanagari)
    raise UnsupportedCharactersError before drawing.
    """
    data = CertificateRenderData(
        certificate_id="cert-indic",
        recipient_name="మోహన్ చందు",
        event_name="Tech Conference",
        title="Certificate of Participation",
        issuer_name="Tech Academy",
        issue_date=datetime.date(2026, 7, 20)
    )
    dest_path = temp_cert_dir / "telugu.pdf"
    with pytest.raises(UnsupportedCharactersError) as exc_info:
        generate_certificate_pdf(data, dest_path)

    assert "unsupported character(s)" in str(exc_info.value)
    # Ensure temporary and destination files are not left behind
    assert not dest_path.exists()



def test_validate_recipient_name_glyphs():
    """Unit test glyph validation directly."""
    # Supported
    validate_recipient_name_glyphs("John Doe")
    validate_recipient_name_glyphs("René Descartes")
    validate_recipient_name_glyphs("Дмитрий")

    # Unsupported
    with pytest.raises(UnsupportedCharactersError):
        validate_recipient_name_glyphs("రమేష్")
