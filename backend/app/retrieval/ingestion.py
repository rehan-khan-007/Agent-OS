"""
Document ingestion: loads raw text from a file so it can be chunked
and eventually embedded.

Kept intentionally simple for now — plain .txt/.md files only.
PDF/docx support can be added later as a separate loader function.
"""

from pathlib import Path


def load_document(file_path: str) -> str:
    """
    Reads a text-based document and returns its raw content as a string.

    Args:
        file_path: path to a .txt or .md file

    Returns:
        The full text content of the file.

    Raises:
        FileNotFoundError: if the file doesn't exist
        ValueError: if the file type isn't supported yet
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"No file found at {file_path}")

    if path.suffix not in [".txt", ".md"]:
        raise ValueError(
            f"Unsupported file type: {path.suffix}. "
            "Only .txt and .md are supported right now."
        )

    return path.read_text(encoding="utf-8")