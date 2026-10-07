import re


def sanitize_filename(name: str) -> str:
    """
    Sanitizes a string to be safely used as a filename:
    - Removes path traversal characters (/, \\).
    - Removes dangerous filesystem characters (<, >, :, ", |, ?, *).
    - Truncates to reasonable length (max 50 chars).
    - Replaces spaces with underscores.
    """
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = clean.replace(" ", "_").strip("._ ")
    return clean[:50] or "certificate"
