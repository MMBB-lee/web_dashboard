"""Parse authorized PDF, text, and Markdown documents."""

from __future__ import annotations

from pathlib import Path


class UnsupportedDocumentError(ValueError):
    """Raised when a document type is not supported."""


def read_document(file_path: str | Path) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return read_text_file(path)
    if suffix == ".pdf":
        return read_pdf_file(path)
    raise UnsupportedDocumentError(f"Unsupported document type: {suffix}")


def read_text_file(file_path: str | Path) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix not in {".txt", ".md", ".markdown"}:
        raise UnsupportedDocumentError(f"Unsupported text document type: {suffix}")
    return path.read_text(encoding="utf-8")


def read_pdf_file(file_path: str | Path) -> str:
    from pypdf import PdfReader

    path = Path(file_path)
    if path.suffix.lower() != ".pdf":
        raise UnsupportedDocumentError(f"Unsupported PDF document type: {path.suffix}")

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(page.strip() for page in pages if page.strip())
