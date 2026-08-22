"""
Document ingestion: loads raw text from a file so it can be chunked
and eventually embedded.

Supports .txt/.md (plain read) and .pdf (extracted page by page).
"""

from pathlib import Path

from pypdf import PdfReader


def _load_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def load_document(file_path: str) -> str:
    """
    Reads a document and returns its raw content as a string.

    Args:
        file_path: path to a .txt, .md, or .pdf file

    Returns:
        The full text content of the file.

    Raises:
        FileNotFoundError: if the file doesn't exist
        ValueError: if the file type isn't supported yet
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"No file found at {file_path}")

    if path.suffix == ".pdf":
        return _load_pdf(path)

    if path.suffix in [".txt", ".md"]:
        return path.read_text(encoding="utf-8")

    raise ValueError(
        f"Unsupported file type: {path.suffix}. "
        "Only .txt, .md, and .pdf are supported right now."
    )