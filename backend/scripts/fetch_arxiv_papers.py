"""
Fetches real, topically-relevant papers from arXiv's public API and
downloads their PDFs — genuine corpus expansion, not filler.

Queries are scoped to this project's actual MTP domain (quantum
optimal control of spin systems) rather than a generic broad term,
so the resulting corpus is coherent and the eval question set stays
meaningful as it grows.

Uses the official `arxiv` PyPI package for search (correctly
implements arXiv's rate-limit requirement — max 1 request per 3
seconds per their Terms of Use — via Client(delay_seconds=3.0), not
something to reimplement by hand). PDF downloads are done directly
via httpx against result.pdf_url: the package's own download_pdf()
convenience method was removed as of arxiv==4.0.1 (verified against
the installed package's source, not assumed from older tutorials).

Usage:
    pip install arxiv --break-system-packages
    python3 scripts/fetch_arxiv_papers.py [papers_per_query]
"""

import sys
import time
from pathlib import Path

import arxiv
import httpx

PAPERS_DIR = Path(__file__).resolve().parents[2] / "docs" / "papers"

# Multiple targeted queries across genuinely distinct interest areas,
# rather than one broad query per topic that would pull in irrelevant
# results. Grouped by domain so it's easy to see (and adjust) what
# each part of the corpus actually covers.
QUERIES = [
    # MTP: quantum optimal control of spin systems
    "cat:quant-ph AND abs:optimal control",
    "cat:quant-ph AND abs:GRAPE pulse",
    "abs:gradient ascent pulse engineering",
    "abs:Newton-Raphson quantum control",
    "abs:NMR pulse sequence design",
    "abs:Bloch equations spin dynamics",

    # Entrepreneurship / business / innovation
    "cat:econ.GN AND abs:entrepreneurship",
    "cat:econ.GN AND abs:startup innovation",
    "cat:econ.GN AND abs:new venture growth",

    # Thermal / cooling engineering (relevant to the IDEAS cooling appliance project)
    "cat:physics.app-ph AND abs:personal cooling",
    "cat:physics.app-ph AND abs:wearable thermal management",
    "cat:eess.SY AND abs:cooling system control",

    # Investment / finance
    "cat:q-fin.PM AND abs:portfolio optimization",
    "cat:q-fin.RM AND abs:risk management",
    "cat:q-fin.GN AND abs:investment strategy",
    "cat:q-fin.CP AND abs:algorithmic trading",
    "cat:q-fin.ST AND abs:asset pricing",
]


def download_pdf(url: str, dest: Path) -> None:
    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    dest.write_bytes(response.content)


def main():
    papers_per_query = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    already_have = {p.stem for p in PAPERS_DIR.glob("*.pdf")}
    print(f"Already have {len(already_have)} PDFs in {PAPERS_DIR}")

    client = arxiv.Client(delay_seconds=3.0, num_retries=3)

    downloaded = []
    skipped_existing = 0
    skipped_error = 0

    for query in QUERIES:
        print(f"\nSearching: {query}")
        search = arxiv.Search(
            query=query,
            max_results=papers_per_query,
            sort_by=arxiv.SortCriterion.Relevance,
        )

        for result in client.results(search):
            arxiv_id = result.get_short_id()
            safe_id = arxiv_id.replace("/", "_")

            if safe_id in already_have:
                skipped_existing += 1
                continue

            if not result.pdf_url:
                skipped_error += 1
                print(f"  No PDF URL for {arxiv_id}, skipping")
                continue

            try:
                dest = PAPERS_DIR / f"{safe_id}.pdf"
                download_pdf(result.pdf_url, dest)
                downloaded.append(dest.name)
                already_have.add(safe_id)
                print(f"  Downloaded: {dest.name} — {result.title[:70]}")
            except Exception as e:
                skipped_error += 1
                print(f"  Failed to download {arxiv_id}: {e}")

            # Rate-limit PDF downloads too, out of the same politeness
            # arXiv's Terms of Use ask for on API calls generally —
            # Client(delay_seconds=3.0) only covers search requests.
            time.sleep(1.0)

    print(f"\n{'=' * 50}")
    print(f"Downloaded: {len(downloaded)} new PDFs")
    print(f"Skipped (already had): {skipped_existing}")
    print(f"Skipped (download error): {skipped_error}")
    print(f"Total PDFs now in {PAPERS_DIR}: {len(list(PAPERS_DIR.glob('*.pdf')))}")
    print(f"{'=' * 50}")
    print("\nThank you to arXiv for use of its open access interoperability.")


if __name__ == "__main__":
    main()
