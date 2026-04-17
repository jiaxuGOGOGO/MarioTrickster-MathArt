from pathlib import Path
import sys
from pypdf import PdfReader


def extract(src: str, dst: str) -> None:
    reader = PdfReader(src)
    parts: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        parts.append(f"\n\n--- PAGE {i} ---\n\n{text}")
    Path(dst).write_text("".join(parts), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: extract_pdf_text.py <src.pdf> <dst.txt>")
    extract(sys.argv[1], sys.argv[2])
