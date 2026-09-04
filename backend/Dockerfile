FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for python-magic and other libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────────────
# DECOMMISSIONED — Docling model warm-up (pipeline step 3).
# Docling is no longer in the extraction path; models no longer need
# baking in. Commented rather than deleted so step 3 can be restored
# together with decommissioned/. It was also failing on
# this base image (ImportError: libxcb.so.1), which blocked every
# backend image build.
# ─────────────────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════
# Pre-download Docling ML models at build time (has internet access).
# At RUNTIME the worker runs on the no_internet Docker network — models
# MUST be baked into the image or convert() hangs forever trying to
# reach HuggingFace.
#
# We force a real convert() against a minimal valid PDF to trigger
# lazy model loading during the build. After this step, all layout,
# table-structure, and formula models are cached in /root/.cache/huggingface.
# ═════════════════════════════════════════════════════════════════════
# RUN python -c "\
# from docling.datamodel.pipeline_options import PdfPipelineOptions; \
# from docling.document_converter import DocumentConverter, PdfFormatOption; \
# from docling.datamodel.base_models import InputFormat; \
# from docling_core.types.io import DocumentStream; \
# import io; \
# \
# _MINIMAL_PDF = ( \
#     b'%PDF-1.4\n' \
#     b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n' \
#     b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n' \
#     b'3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj\n' \
#     b'xref\n0 4\n0000000000 65535 f \n' \
#     b'0000000009 00000 n \n' \
#     b'0000000058 00000 n \n' \
#     b'0000000115 00000 n \n' \
#     b'trailer<</Size 4/Root 1 0 R>>\n' \
#     b'startxref\n190\n%%EOF' \
# ); \
# opts = PdfPipelineOptions(); \
# opts.do_ocr = False; \
# opts.do_table_structure = True; \
# converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}); \
# source = DocumentStream(name='build-seed', stream=io.BytesIO(_MINIMAL_PDF)); \
# result = converter.convert(source); \
# text = result.document.export_to_text(); \
# print(f'Docling models cached — extracted {len(text)} chars')"

# Lock the cache for offline-only runtime on the no_internet network.
# Docling and HuggingFace will NEVER attempt network access from here.
ENV HF_HUB_OFFLINE=1

# Copy application code
COPY app/ ./app/
COPY alembic.ini .
COPY alembic/ ./alembic/

# Expose API port
EXPOSE 8000

# Default command - run the API server. Temps injects PORT; fall back to
# 8000 for local docker-compose, which doesn't set it.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]