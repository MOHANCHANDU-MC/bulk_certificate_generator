import re
from dataclasses import dataclass

from app.schemas import RecipientInput

# Standard RFC 5322 compatible regex for email validation
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


@dataclass
class ValidationResult:
    """Carries recipient validation outcome."""
    is_valid: bool
    clean_name: str | None
    clean_email: str | None
    error_message: str | None = None


def validate_recipients_batch(recipients: list[RecipientInput]) -> list[ValidationResult]:
    """
    Validates a list of recipients according to business rules:
    - Name: must not be None, empty, or whitespace-only; must not exceed 100 characters.
    - Email: optional, but if provided, must be a non-empty valid email address.
    - Duplicates: if an email is provided, it must not appear more than once within the same job batch.
      (Emails are compared case-insensitively).

    Returns a list of ValidationResult matching the exact ordering of the input.
    """
    results: list[ValidationResult] = []
    seen_emails: set[str] = set()

    for item in recipients:
        raw_name = item.name.strip() if item.name and isinstance(item.name, str) else None
        raw_email = item.email.strip() if item.email and isinstance(item.email, str) else None

        # 1. Validate Recipient Name
        if not raw_name:
            results.append(
                ValidationResult(
                    is_valid=False,
                    clean_name=item.name,
                    clean_email=raw_email,
                    error_message="Recipient name is required and cannot be blank."
                )
            )
            continue

        # Check for control characters (newlines, carriage returns, tabs) and zero-width spaces
        disallowed_chars = {"\n", "\r", "\t", "\u200b", "\u200c", "\u200d", "\ufeff"}
        if any(ch in disallowed_chars for ch in raw_name) or any(ord(ch) < 32 for ch in raw_name):
            results.append(
                ValidationResult(
                    is_valid=False,
                    clean_name=raw_name,
                    clean_email=raw_email,
                    error_message=(
                        "Recipient name cannot contain control characters "
                        "(newlines, tabs) or zero-width characters."
                    )
                )
            )
            continue


        if len(raw_name) > 100:
            results.append(
                ValidationResult(
                    is_valid=False,
                    clean_name=raw_name,
                    clean_email=raw_email,
                    error_message=(
                        f"Recipient name exceeds maximum allowed length of 100 characters "
                        f"({len(raw_name)} chars)."
                    )
                )
            )
            continue

        # 2. Validate Email (optional, but if provided must be valid and non-duplicate)
        if raw_email:
            normalized_email = raw_email.lower()
            if not EMAIL_REGEX.match(normalized_email):
                results.append(
                    ValidationResult(
                        is_valid=False,
                        clean_name=raw_name,
                        clean_email=raw_email,
                        error_message=f"Invalid email address format: '{raw_email}'."
                    )
                )
                continue

            if normalized_email in seen_emails:
                results.append(
                    ValidationResult(
                        is_valid=False,
                        clean_name=raw_name,
                        clean_email=raw_email,
                        error_message=f"Duplicate email '{raw_email}' found in this batch."
                    )
                )
                continue

            seen_emails.add(normalized_email)

        # 3. Valid Recipient
        results.append(
            ValidationResult(
                is_valid=True,
                clean_name=raw_name,
                clean_email=raw_email,
                error_message=None
            )
        )

    return results
