"""Standardised document-extraction result shape.

Originally paired with a swappable DocumentParser ABC (for Docling, Textract,
future replacements) — that interface had zero live implementations once
Docling/Textract were decommissioned (see decommissioned/README.md) and moved
with the last remaining implementer to decommissioned/step3_docling_parser.py.
ExtractionResult itself stays here: worker_jobs.py and the decommissioned-but-
restorable step3/step5 modules still depend on this shape.
"""

from pydantic import BaseModel


class ExtractionResult(BaseModel):
    """Standardised output from any document parser.

    Matches the cv_extraction_passes column shape in 03-data-model.md §3.
    """
    extracted_text: str
    raw_output: dict | None = None
    engine: str | None = None
    engine_version: str | None = None
    confidence_score: float | None = None
    characters: int | None = None
    pages: int | None = None
    processing_duration_ms: int | None = None