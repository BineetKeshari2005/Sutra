import re


def slugify(text: str, separator: str = "-") -> str:
    """Generate an ASCII slug from text.

    Args:
        text: Input string.
        separator: Character used to separate words (default: '-').

    Returns:
        Clean slugified string.
    """
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    # Replaces single whitespace characters with separator without collapsing consecutive whitespace
    text = re.sub(r"\s", separator, text)
    return text.strip(separator)
