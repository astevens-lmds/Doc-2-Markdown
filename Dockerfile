FROM python:3.11-slim

# Install system dependencies for PDF processing and OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libmagic1 \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest

# Copy application code
COPY . .

# Default: run batch converter
ENTRYPOINT ["python", "batch_convert.py"]
CMD ["--help"]
