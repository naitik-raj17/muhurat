"""Text extraction from uploaded documents (PDF, DOCX)."""

from __future__ import annotations

import io

from app.domain.errors import ValidationFailed

PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK\x03\x04"  # DOCX is a zip container
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def detect_type(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf") and data.startswith(PDF_MAGIC):
        return "application/pdf"
    if name.endswith(".docx") and data.startswith(ZIP_MAGIC):
        return DOCX_TYPE
    raise ValidationFailed("Upload a PDF or DOCX contract")


def extract_text(content_type: str, data: bytes) -> str:
    if content_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if content_type == DOCX_TYPE:
        from docx import Document

        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    raise ValidationFailed(f"Unsupported content type {content_type}")
