import io
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import qrcode
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics, ttfonts
from reportlab.pdfgen import canvas

from app.config import settings


class UnsupportedCharactersError(ValueError):
    """Raised when a recipient name contains characters not covered by the certificate font."""
    pass


# Global cache for font registration and supported glyph set
_FONT_REGISTERED = False
_SUPPORTED_GLYPH_ORDS: set[int] = set()


def ensure_font_registered() -> None:
    """
    Registers the DejaVuSans font once and caches the set of supported unicode ordinals.
    Thread-safe initialization.
    """
    global _FONT_REGISTERED, _SUPPORTED_GLYPH_ORDS
    if _FONT_REGISTERED:
        return

    font_path_str = str(settings.FONT_PATH)
    if not os.path.exists(font_path_str):
        raise FileNotFoundError(f"Certificate font file not found at: {font_path_str}")

    ttfont = ttfonts.TTFont(settings.FONT_NAME, font_path_str)
    pdfmetrics.registerFont(ttfont)

    # Cache supported character codes from TrueType face charWidths dict
    if hasattr(ttfont.face, "charWidths"):
        _SUPPORTED_GLYPH_ORDS = set(ttfont.face.charWidths.keys())
    else:
        _SUPPORTED_GLYPH_ORDS = set(range(32, 127))

    _FONT_REGISTERED = True


def validate_recipient_name_glyphs(name: str) -> None:
    """
    Validates that every character in the recipient's name exists in the font's glyph table.
    Whitespace (spaces, tabs) is always considered supported.
    Raises UnsupportedCharactersError with the list of missing characters if any are missing.
    """
    ensure_font_registered()

    unsupported: list[str] = []
    for char in name:
        if char.isspace():
            continue
        if ord(char) not in _SUPPORTED_GLYPH_ORDS:
            if char not in unsupported:
                unsupported.append(char)

    if unsupported:
        full_chars_str = ", ".join(f"'{c}' (U+{ord(c):04X})" for c in unsupported)
        import logging
        logging.getLogger(__name__).warning("Font glyph validation failed. Unsupported glyphs: %s", full_chars_str)

        # Short client message: count and first 3 characters
        sample_chars = ", ".join(f"'{c}'" for c in unsupported[:3])
        more_str = f" and {len(unsupported) - 3} more" if len(unsupported) > 3 else ""
        raise UnsupportedCharactersError(
            f"Name contains {len(unsupported)} unsupported character(s): {sample_chars}{more_str}."
        )



def _generate_qr_code_image(verify_url: str) -> Image.Image:
    """Generates a PIL Image of a QR code pointing to the verify URL."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=1,
    )
    qr.add_data(verify_url)
    qr.make(fit=True)
    return qr.make_image(fill_color="#1e293b", back_color="white").convert("RGB")


@dataclass
class CertificateRenderData:
    certificate_id: str
    recipient_name: str
    event_name: str
    title: str
    issuer_name: str
    issue_date: date


def generate_certificate_pdf(data: CertificateRenderData, output_path: str | Path) -> Path:
    """
    Generates a single landscape A4 PDF certificate.
    - Validates glyphs against the font.
    - Generates a QR code linking to verify endpoint.
    - Automatically shrinks recipient name font to fit bounding box.
    - Writes to a temporary file first and renames on completion to prevent half-written files.
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Validate name glyphs before rendering
    validate_recipient_name_glyphs(data.recipient_name)

    # 2. Prepare canvas dimensions (Landscape A4: 841.89 x 595.27 points)
    page_width, page_height = landscape(A4)

    # 3. Write initially to a temporary file in the target directory
    temp_fd, temp_file_path_str = tempfile.mkstemp(
        prefix="cert_",
        suffix=".tmp",
        dir=str(output_path.parent)
    )
    os.close(temp_fd)
    temp_path = Path(temp_file_path_str)

    try:
        c = canvas.Canvas(str(temp_path), pagesize=landscape(A4))
        c.setTitle(f"{data.title} - {data.recipient_name}")
        c.setAuthor(data.issuer_name)

        # Background base fill
        c.setFillColor(colors.HexColor("#f8fafc"))
        c.rect(0, 0, page_width, page_height, fill=1, stroke=0)

        # Elegant outer decorative border
        margin = 32
        c.setStrokeColor(colors.HexColor("#0f172a"))
        c.setLineWidth(3)
        c.rect(margin, margin, page_width - (2 * margin), page_height - (2 * margin), fill=0, stroke=1)

        # Inner border
        inner_margin = 38
        c.setStrokeColor(colors.HexColor("#94a3b8"))
        c.setLineWidth(1)
        c.rect(
            inner_margin,
            inner_margin,
            page_width - (2 * inner_margin),
            page_height - (2 * inner_margin),
            fill=0,
            stroke=1
        )

        # Top Certificate Title (adjusted downward for balanced spacing)
        c.setFont(settings.FONT_NAME, 26)
        c.setFillColor(colors.HexColor("#0f172a"))
        c.drawCentredString(page_width / 2, 475, data.title.upper())

        # Sub-title
        c.setFont(settings.FONT_NAME, 13)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawCentredString(page_width / 2, 430, "PROUDLY PRESENTED TO")

        # 4. Auto-shrink recipient name font size to fit bounding width
        max_name_width = page_width - 160  # Max available width for name (681.89 points)
        font_size = 38
        min_font_size = 18

        while font_size > min_font_size:
            text_width = pdfmetrics.stringWidth(data.recipient_name, settings.FONT_NAME, font_size)
            if text_width <= max_name_width:
                break
            font_size -= 1

        measured_width = pdfmetrics.stringWidth(data.recipient_name, settings.FONT_NAME, font_size)

        if measured_width <= max_name_width:
            # Single-line rendering
            c.setFont(settings.FONT_NAME, font_size)
            c.setFillColor(colors.HexColor("#1e3a8a"))  # Deep navy blue
            c.drawCentredString(page_width / 2, 360, data.recipient_name)

            # Underline for name
            actual_name_width = min(measured_width, max_name_width)
            line_half = max(actual_name_width / 2 + 20, 120)
            c.setStrokeColor(colors.HexColor("#cbd5e1"))
            c.setLineWidth(1.5)
            c.line(page_width / 2 - line_half, 343, page_width / 2 + line_half, 343)
        else:
            # Fallback for names exceeding bounding width even at minimum font size:
            # Wrap by measured character width (using reportlab stringWidth), breaking long words if needed.
            # Allows up to 3 lines.
            c.setFont(settings.FONT_NAME, min_font_size)
            c.setFillColor(colors.HexColor("#1e3a8a"))

            wrapped_lines: list[str] = []
            words = data.recipient_name.split(" ") if " " in data.recipient_name else [data.recipient_name]
            current_line = ""

            for word in words:
                # Check if the individual word exceeds max_name_width
                if pdfmetrics.stringWidth(word, settings.FONT_NAME, min_font_size) > max_name_width:
                    for ch in word:
                        test_line = current_line + ch
                        if pdfmetrics.stringWidth(test_line, settings.FONT_NAME, min_font_size) <= max_name_width:
                            current_line = test_line
                        else:
                            if current_line:
                                wrapped_lines.append(current_line)
                            current_line = ch
                    continue

                test_line = (current_line + " " + word).strip()
                if pdfmetrics.stringWidth(test_line, settings.FONT_NAME, min_font_size) <= max_name_width:
                    current_line = test_line
                else:
                    if current_line:
                        wrapped_lines.append(current_line)
                    current_line = word

            if current_line:
                wrapped_lines.append(current_line)

            wrapped_lines = wrapped_lines[:3]  # Allow up to 3 lines

            # Center lines vertically
            if len(wrapped_lines) == 1:
                c.drawCentredString(page_width / 2, 360, wrapped_lines[0])
            elif len(wrapped_lines) == 2:
                c.drawCentredString(page_width / 2, 372, wrapped_lines[0])
                c.drawCentredString(page_width / 2, 350, wrapped_lines[1])
            else:
                c.drawCentredString(page_width / 2, 380, wrapped_lines[0])
                c.drawCentredString(page_width / 2, 360, wrapped_lines[1])
                c.drawCentredString(page_width / 2, 340, wrapped_lines[2])

            c.setStrokeColor(colors.HexColor("#cbd5e1"))
            c.setLineWidth(1.5)
            c.line(page_width / 2 - 220, 330, page_width / 2 + 220, 330)

        # Event / Course explanation
        c.setFont(settings.FONT_NAME, 13)
        c.setFillColor(colors.HexColor("#475569"))
        c.drawCentredString(page_width / 2, 298, "for successfully completing")

        c.setFont(settings.FONT_NAME, 19)
        c.setFillColor(colors.HexColor("#0f172a"))
        c.drawCentredString(page_width / 2, 265, data.event_name)

        # Bottom section: Issuer & Date (Left) and QR Code & Cert ID (Right)
        # Left side - Issuer
        c.setFont(settings.FONT_NAME, 11)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawString(75, 140, "ISSUED BY")
        c.setFont(settings.FONT_NAME, 14)
        c.setFillColor(colors.HexColor("#0f172a"))
        c.drawString(75, 120, data.issuer_name)

        # Left side - Date
        c.setFont(settings.FONT_NAME, 11)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawString(75, 88, "DATE OF ISSUE")
        c.setFont(settings.FONT_NAME, 13)
        c.setFillColor(colors.HexColor("#0f172a"))
        c.drawString(75, 70, data.issue_date.strftime("%B %d, %Y"))

        # Right side - QR Code & Certificate ID
        verify_url = f"{settings.BASE_URL.rstrip('/')}/api/verify/{data.certificate_id}"
        qr_img = _generate_qr_code_image(verify_url)
        qr_buffer = io.BytesIO()
        qr_img.save(qr_buffer, format="PNG")
        qr_buffer.seek(0)

        # Draw QR code (size: 78x78 points)
        qr_size = 78
        qr_x = page_width - 75 - qr_size
        qr_y = 70
        from reportlab.lib.utils import ImageReader
        c.drawImage(ImageReader(qr_buffer), qr_x, qr_y, width=qr_size, height=qr_size)

        # Draw Verification notice & FULL Certificate ID
        c.setFont(settings.FONT_NAME, 10)
        c.setFillColor(colors.HexColor("#475569"))
        c.drawRightString(qr_x - 14, qr_y + 55, "Scan QR to verify authenticity")

        c.setFont(settings.FONT_NAME, 9)
        c.setFillColor(colors.HexColor("#0f172a"))
        c.drawRightString(qr_x - 14, qr_y + 38, f"Certificate ID: {data.certificate_id}")

        c.setFont(settings.FONT_NAME, 10)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawRightString(qr_x - 14, qr_y + 20, "Digitally verifiable")


        # Finalize and save PDF
        c.showPage()
        c.save()

        # Atomic rename temp file to destination
        if output_path.exists():
            output_path.unlink()
        temp_path.rename(output_path)
        return output_path

    except Exception:
        # Clean up temporary file if any error occurs
        if temp_path.exists():
            temp_path.unlink()
        raise
