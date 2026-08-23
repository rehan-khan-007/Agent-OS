"""
Lists every PDF in docs/papers/ alongside a text preview (usually
captures the title and start of the abstract), so the corpus can be
manually scanned for off-topic results before ingestion — arXiv's
`abs:` search matches loosely on individual words, so a few
unrelated papers can slip into results for a narrow query.

Usage:
    python3 scripts/list_papers.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retrieval.ingestion import load_document

PAPERS_DIR = Path(__file__).resolve().parents[2] / "docs" / "papers"


def main():
    pdfs = sorted(PAPERS_DIR.glob("*.pdf"))
    print(f"{len(pdfs)} PDF(s) in {PAPERS_DIR}\n")

    for pdf in pdfs:
        try:
            text = load_document(str(pdf))
            preview = " ".join(text[:200].split())
        except Exception as e:
            preview = f"(could not extract text: {e})"
        print(f"{pdf.name}")
        print(f"  {preview}")
        print()


if __name__ == "__main__":
    main()
